"""
Disk layer for Glue Linux installer (bootloader steps live in limine.py).

ADR-6: pure `parse_lsblk(json)` -> devices + pure `plan_disk(device, firmware)`
-> DiskPlan + pure step compilation (`disk_steps`) into the executor's Step
types. The ONLY subprocess call is the thin `discover()` wrapper (never at import).
UEFI vs BIOS is decided by the caller (/sys/firmware/efi presence) and passed
in as a value — never probed inside pure code.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Tuple

from glue_installer.executor import RunCommand, Step


class DiskError(Exception):
    """Raised for any disk discovery/planning failure. Never SystemExit."""


_GIB = 1024 ** 3
MIN_DISK_BYTES = 8 * _GIB

_VALID_FIRMWARE = frozenset({"uefi", "bios"})

_LSBLK_ARGV = [
    "lsblk", "--json", "-b",
    "-o", "NAME,PATH,SIZE,TYPE,MODEL,RM,MOUNTPOINTS,FSTYPE,PARTTYPE",
]

# GPT partition-type GUID of an EFI System Partition (lsblk PARTTYPE).
_ESP_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
MIN_ROOT_PART_BYTES = 8 * (1024 ** 3)
MIN_ESP_BYTES = 256 * (1024 ** 2)

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
    filesystem: str      # 'vfat', 'ext4', 'swap', or '' (none)
    mountpoint: str      # '/', '/boot', 'swap', or '' (not mounted)


@dataclass(frozen=True)
class Partition:
    """An EXISTING partition discovered via lsblk (not a planned one)."""
    name: str            # e.g. nvme0n1p3
    path: str            # e.g. /dev/nvme0n1p3
    parent_path: str     # e.g. /dev/nvme0n1
    size_bytes: int
    fstype: str          # '' when unformatted
    is_esp: bool         # GPT type is EFI System Partition
    is_mounted: bool


@dataclass(frozen=True)
class DiskPlan:
    device_path: str
    firmware: str                          # 'uefi' or 'bios'
    partitions: Tuple[PartitionSpec, ...]  # ordered by partition number
    mode: str = "erase"                    # 'erase' (repartition) | 'existing'
    swap_uuid: str = ""                    # mkswap -U (hibernation resume)
    swapfile_mib: int = 0                  # swapfile on root (existing mode)
    warnings: Tuple[str, ...] = ()         # shown in the summary (e.g. small ESP)


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


def _partition_number(path: str) -> int:
    """Trailing digits of a partition path (/dev/sda1 -> 1, /dev/nvme0n1p3 -> 3)."""
    m = re.search(r"(\d+)$", path or "")
    if not m:
        raise DiskError(f"cannot read a partition number from {path!r}")
    return int(m.group(1))


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


def parse_lsblk_partitions(json_text: str) -> List[Partition]:
    """Parse `lsblk --json -b` output into the flat list of EXISTING partitions
    (type == 'part') across all disks. Raises DiskError on malformed JSON."""
    try:
        data = json.loads(json_text)
    except ValueError as exc:
        raise DiskError(f"lsblk output is not valid JSON: {exc}") from exc
    entries = data.get("blockdevices")
    if not isinstance(entries, list):
        raise DiskError("lsblk JSON missing 'blockdevices' list")

    parts: List[Partition] = []
    for disk in entries:
        if not isinstance(disk, dict) or disk.get("type") != "disk":
            continue
        for child in disk.get("children") or []:
            if not isinstance(child, dict) or child.get("type") != "part":
                continue
            size = child.get("size")
            if not isinstance(size, int):
                continue
            parttype = (child.get("parttype") or "").lower()
            parts.append(Partition(
                name=str(child.get("name", "")),
                path=str(child.get("path", "")),
                parent_path=str(disk.get("path", "")),
                size_bytes=size,
                fstype=str(child.get("fstype") or ""),
                is_esp=parttype == _ESP_GUID,
                is_mounted=_entry_mounted(child),
            ))
    return parts


# ---------------------------------------------------------------------------
# Pure planning
# ---------------------------------------------------------------------------

def plan_existing_partition(
    root: Partition, firmware: str, partitions: List[Partition],
) -> DiskPlan:
    """Plan an install INTO an existing partition: format only that partition,
    never touch the partition table. On UEFI, reuses the disk's existing EFI
    System Partition (mounted at /boot, NOT reformatted). Partition numbers
    are the real ones (efibootmgr --part needs them). A reused ESP under
    256 MiB gets a warning: with Limine the kernels live on it.

    Raises DiskError when the root partition is mounted/too small, or when
    firmware is uefi and the root's disk has no ESP (use manual mode to add one).
    """
    if firmware not in _VALID_FIRMWARE:
        raise DiskError(
            f"Unknown firmware: '{firmware}'; expected one of {sorted(_VALID_FIRMWARE)}"
        )
    if root.is_mounted:
        raise DiskError(f"Partition {root.path} is mounted — refusing to format it")
    if root.size_bytes < MIN_ROOT_PART_BYTES:
        raise DiskError(
            f"Partition {root.path} is too small for Glue "
            f"(need at least 8 GiB)"
        )

    specs: List[PartitionSpec] = []
    warnings: List[str] = []
    if firmware == "uefi":
        esp = next(
            (p for p in partitions
             if p.is_esp and p.parent_path == root.parent_path), None,
        )
        if esp is None:
            raise DiskError(
                f"No EFI System Partition found on {root.parent_path} — "
                "create one with 'Partition manually (cfdisk)' first"
            )
        specs.append(PartitionSpec(
            number=_partition_number(esp.path), path=esp.path,
            type_code=_TYPE_ESP, size="",
            filesystem="", mountpoint="/boot",  # reuse: no mkfs
        ))
        if 0 < esp.size_bytes < MIN_ESP_BYTES:
            warnings.append(
                "The existing EFI partition is small (<256 MiB); kernels live "
                "on it with Limine — the install may run out of space")
    specs.append(PartitionSpec(
        number=_partition_number(root.path), path=root.path,
        type_code=_TYPE_LINUX, size="",
        filesystem="ext4", mountpoint="/",
    ))
    return DiskPlan(
        device_path=root.parent_path, firmware=firmware,
        partitions=tuple(specs), mode="existing", warnings=tuple(warnings),
    )


def plan_disk(device: BlockDevice, firmware: str) -> DiskPlan:
    """Plan a full-disk GPT layout for the given firmware type.

    uefi: p1 = 512MiB EFI System (vfat -> /boot, holds the kernels), p2 = rest ext4 -> /.
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
            filesystem="vfat", mountpoint="/boot",
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
    (uefi only) at target/boot — Limine keeps kernels on the ESP.
    """
    t = target.rstrip("/")
    disk = disk_plan.device_path
    steps: List[Step] = []

    # 'existing' mode never touches the partition table and never reformats
    # the ESP — it only formats the chosen root partition.
    if disk_plan.mode == "erase":
        steps.append(RunCommand(
            argv=["sgdisk", "--zap-all", disk],
            description=f"Wipe partition table on {disk}",
        ))
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

    esp = next((p for p in disk_plan.partitions if p.mountpoint == "/boot"), None)
    root = next(p for p in disk_plan.partitions if p.mountpoint == "/")

    if esp is not None and disk_plan.mode == "erase":
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
            argv=["mkdir", "-p", f"{t}/boot"],
            description=f"Create {t}/boot",
        ))
        steps.append(RunCommand(
            argv=["mount", esp.path, f"{t}/boot"],
            description=f"Mount {esp.path} at {t}/boot",
        ))
    return steps


# ---------------------------------------------------------------------------
# Thin subprocess wrapper (the ONLY subprocess use in this module)
# ---------------------------------------------------------------------------

def _run_lsblk() -> str:
    """Run lsblk and return its JSON stdout. Raises DiskError if lsblk is
    missing or fails. Never called at import time."""
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
    return result.stdout


def discover() -> List[BlockDevice]:
    """Discover whole disks via lsblk (see _run_lsblk for the error contract)."""
    return parse_lsblk(_run_lsblk())


def discover_partitions() -> List[Partition]:
    """Discover existing partitions via lsblk (same error contract)."""
    return parse_lsblk_partitions(_run_lsblk())
