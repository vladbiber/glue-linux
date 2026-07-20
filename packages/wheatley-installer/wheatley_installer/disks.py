"""
Disk & bootloader layer for Wheatley Linux installer.

ADR-6: pure `parse_lsblk(json)` -> devices + pure `plan_disk(device, firmware)`
-> DiskPlan + pure step compilation (`disk_steps`, `bootloader_steps`) into the
executor's existing Step types. The ONLY subprocess call in this module lives
in the thin `discover()` wrapper, which is never called at import time.
UEFI vs BIOS is decided by the caller (/sys/firmware/efi presence) and passed
in as a value — never probed inside pure code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Tuple

from wheatley_installer.executor import RunCommand, Step


class DiskError(Exception):
    """Raised for any disk discovery/planning failure. Never SystemExit."""


_GIB = 1024 ** 3
MIN_DISK_BYTES = 8 * _GIB

_VALID_FIRMWARE = frozenset({"uefi", "bios"})

_LSBLK_ARGV = [
    "lsblk", "--json", "-b",
    "-o", "NAME,PATH,SIZE,TYPE,MODEL,RM,MOUNTPOINTS",
]

# sgdisk type codes
_TYPE_ESP = "ef00"
_TYPE_BIOS_BOOT = "ef02"
_TYPE_LINUX = "8300"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BlockDevice:
    name: str
    path: str
    size_bytes: int
    model: str
    is_removable: bool
    has_mounted_partitions: bool


@dataclass(frozen=True)
class PartitionSpec:
    number: int          # 1-based partition number on the disk
    path: str            # e.g. /dev/sda1 or /dev/nvme0n1p1
    type_code: str       # sgdisk type code: ef00 / ef02 / 8300
    size: str            # sgdisk -n size field: '+512M', '+1M', or '0' (rest)
    filesystem: str      # 'vfat', 'ext4', or '' (no filesystem)
    mountpoint: str      # '/', '/boot/efi', or '' (not mounted)


@dataclass(frozen=True)
class DiskPlan:
    device_path: str
    firmware: str                          # 'uefi' or 'bios'
    partitions: Tuple[PartitionSpec, ...]  # ordered by partition number


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def partition_path(disk_path: str, number: int) -> str:
    """Partition device path: append the number, inserting 'p' when the disk
    path ends in a digit (/dev/nvme0n1 -> /dev/nvme0n1p1; /dev/sda -> /dev/sda1).
    """
    if not disk_path:
        raise DiskError("partition_path: disk path is empty")
    if disk_path[-1].isdigit():
        return f"{disk_path}p{number}"
    return f"{disk_path}{number}"


def _is_mounted_value(mountpoint) -> bool:
    """A mountpoint entry counts as mounted when it is a non-empty string
    (including the special '[SWAP]' marker lsblk uses for active swap)."""
    return isinstance(mountpoint, str) and mountpoint != ""


def _entry_mounted(entry: dict) -> bool:
    """True if this lsblk entry or any descendant has a mounted filesystem."""
    mountpoints = entry.get("mountpoints", [])
    if not isinstance(mountpoints, list):
        raise DiskError(
            f"lsblk entry '{entry.get('name', '?')}': 'mountpoints' is not a list"
        )
    if any(_is_mounted_value(mp) for mp in mountpoints):
        return True
    children = entry.get("children", [])
    if not isinstance(children, list):
        raise DiskError(
            f"lsblk entry '{entry.get('name', '?')}': 'children' is not a list"
        )
    return any(_entry_mounted(child) for child in children)


# ---------------------------------------------------------------------------
# Pure parsing
# ---------------------------------------------------------------------------

def parse_lsblk(json_text: str) -> List[BlockDevice]:
    """Parse `lsblk --json -b` output into BlockDevices.

    Keeps only entries with type == 'disk'. A disk has_mounted_partitions if
    it or any descendant has a non-empty mountpoint ('[SWAP]' counts).
    Raises DiskError on malformed JSON or missing keys.
    """
    try:
        data = json.loads(json_text)
    except ValueError as exc:
        raise DiskError(f"lsblk output is not valid JSON: {exc}") from exc

    if not isinstance(data, dict) or "blockdevices" not in data:
        raise DiskError("lsblk JSON missing 'blockdevices' key")
    entries = data["blockdevices"]
    if not isinstance(entries, list):
        raise DiskError("lsblk JSON: 'blockdevices' is not a list")

    devices: List[BlockDevice] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise DiskError("lsblk JSON: blockdevice entry is not an object")
        if entry.get("type") != "disk":
            continue
        for key in ("name", "path", "size"):
            if key not in entry or entry[key] is None:
                raise DiskError(
                    f"lsblk disk entry missing required key '{key}': {entry.get('name', '?')}"
                )
        size = entry["size"]
        if not isinstance(size, int):
            raise DiskError(
                f"lsblk disk '{entry['name']}': 'size' is not an integer byte count"
            )
        model = entry.get("model")
        devices.append(BlockDevice(
            name=str(entry["name"]),
            path=str(entry["path"]),
            size_bytes=size,
            model=str(model) if model else "",
            is_removable=bool(entry.get("rm", False)),
            has_mounted_partitions=_entry_mounted(entry),
        ))
    return devices


# ---------------------------------------------------------------------------
# Pure planning
# ---------------------------------------------------------------------------

def plan_disk(device: BlockDevice, firmware: str) -> DiskPlan:
    """Plan a full-disk GPT layout for the given firmware type.

    uefi: p1 = 512MiB EFI System (vfat -> /boot/efi), p2 = rest ext4 -> /.
    bios: p1 = 1MiB BIOS-boot (ef02, no fs), p2 = rest ext4 -> /.
    Raises DiskError on unknown firmware, disks < 8 GiB, or disks with
    mounted partitions (refuses to wipe the running system / live USB).
    """
    if firmware not in _VALID_FIRMWARE:
        raise DiskError(
            f"Unknown firmware: '{firmware}'; expected one of {sorted(_VALID_FIRMWARE)}"
        )
    if device.size_bytes < MIN_DISK_BYTES:
        raise DiskError(
            f"Disk {device.path} is too small: {device.size_bytes} bytes "
            f"(need at least {MIN_DISK_BYTES} bytes / 8 GiB)"
        )
    if device.has_mounted_partitions:
        raise DiskError(
            f"Disk {device.path} has mounted partitions — refusing to erase it"
        )

    if firmware == "uefi":
        first = PartitionSpec(
            number=1, path=partition_path(device.path, 1),
            type_code=_TYPE_ESP, size="+512M",
            filesystem="vfat", mountpoint="/boot/efi",
        )
    else:
        first = PartitionSpec(
            number=1, path=partition_path(device.path, 1),
            type_code=_TYPE_BIOS_BOOT, size="+1M",
            filesystem="", mountpoint="",
        )
    root = PartitionSpec(
        number=2, path=partition_path(device.path, 2),
        type_code=_TYPE_LINUX, size="0",
        filesystem="ext4", mountpoint="/",
    )
    return DiskPlan(
        device_path=device.path,
        firmware=firmware,
        partitions=(first, root),
    )


# ---------------------------------------------------------------------------
# Pure step compilation (into the executor's Step types)
# ---------------------------------------------------------------------------

def disk_steps(disk_plan: DiskPlan, *, target: str = "/mnt") -> List[Step]:
    """Compile disk-preparation Steps: zap, partition, mkfs, mount.

    Runs BEFORE basestrap. Root is mounted at target first, then the ESP
    (uefi only) at target/boot/efi.
    """
    t = target.rstrip("/")
    disk = disk_plan.device_path
    steps: List[Step] = [RunCommand(
        argv=["sgdisk", "--zap-all", disk],
        description=f"Wipe partition table on {disk}",
    )]

    for part in disk_plan.partitions:
        steps.append(RunCommand(
            argv=[
                "sgdisk",
                "-n", f"{part.number}:0:{part.size}",
                "-t", f"{part.number}:{part.type_code}",
                disk,
            ],
            description=(
                f"Create partition {part.number} "
                f"({part.type_code}) on {disk}"
            ),
        ))

    esp = next((p for p in disk_plan.partitions if p.mountpoint == "/boot/efi"), None)
    root = next(p for p in disk_plan.partitions if p.mountpoint == "/")

    if esp is not None:
        steps.append(RunCommand(
            argv=["mkfs.fat", "-F32", esp.path],
            description=f"Format {esp.path} as FAT32 (EFI System)",
        ))
    steps.append(RunCommand(
        argv=["mkfs.ext4", "-F", root.path],
        description=f"Format {root.path} as ext4",
    ))
    steps.append(RunCommand(
        argv=["mount", root.path, t],
        description=f"Mount {root.path} at {t}",
    ))
    if esp is not None:
        steps.append(RunCommand(
            argv=["mkdir", "-p", f"{t}/boot/efi"],
            description=f"Create {t}/boot/efi",
        ))
        steps.append(RunCommand(
            argv=["mount", esp.path, f"{t}/boot/efi"],
            description=f"Mount {esp.path} at {t}/boot/efi",
        ))
    return steps


def bootloader_steps(disk_plan: DiskPlan, *, target: str = "/mnt") -> List[Step]:
    """Compile GRUB install Steps (chrooted). Runs LAST, after service enabling."""
    t = target.rstrip("/")
    if disk_plan.firmware == "uefi":
        install = [
            "artix-chroot", t,
            "grub-install", "--target=x86_64-efi",
            "--efi-directory=/boot/efi", "--bootloader-id=Wheatley",
        ]
    else:
        install = [
            "artix-chroot", t,
            "grub-install", "--target=i386-pc", disk_plan.device_path,
        ]
    return [
        RunCommand(
            argv=install,
            description=f"Install GRUB ({disk_plan.firmware})",
        ),
        RunCommand(
            argv=["artix-chroot", t, "grub-mkconfig", "-o", "/boot/grub/grub.cfg"],
            description="Generate GRUB config",
        ),
    ]


def bootloader_packages(disk_plan: DiskPlan) -> List[str]:
    """Packages the bootloader needs in the basestrap set, sorted."""
    packages = ["grub"]
    if disk_plan.firmware == "uefi":
        packages.append("efibootmgr")
    return sorted(packages)


# ---------------------------------------------------------------------------
# Thin subprocess wrapper (the ONLY subprocess use in this module)
# ---------------------------------------------------------------------------

def discover() -> List[BlockDevice]:
    """Run lsblk and parse its output. Raises DiskError if lsblk is missing
    or fails. Never called at import time."""
    import subprocess
    try:
        result = subprocess.run(
            _LSBLK_ARGV, check=True, capture_output=True, text=True,
        )
    except FileNotFoundError as exc:
        raise DiskError("lsblk not found — cannot discover disks") from exc
    except subprocess.CalledProcessError as exc:
        raise DiskError(
            f"lsblk failed (exit {exc.returncode}): {exc.stderr.strip()}"
        ) from exc
    return parse_lsblk(result.stdout)
