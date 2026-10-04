"""Other operating systems in the Limine menu (roadmap 3.4, ADR-022/023).

Pure module: no subprocess, no filesystem access. The live side runs
`os-prober` + `lsblk --json` (the argv lists are here; __main__ runs them,
tests inject the texts) and this module turns the results into one
/etc/glue/boot.d/NN-<slug>-<8 hex of PARTUUID>.conf fragment per OS.
glue-boot-update appends every fragment after the kernel entries each time it
regenerates /boot/limine.conf; limine_conf(foreign_entries=...) is the
Python reference of that text (tests/test_boot_update.py).

Entry syntax verified in Limine v12.9.0 CONFIG.md:
  UEFI: `protocol: efi` + `image_path: guid(<PARTUUID>):/<loader>` (the
        guid() resource accepts a GPT partition GUID or a filesystem UUID)
  BIOS: `protocol: bios` + `partition: <1-based number>` + `mbr_id: <hex>`
        (DOS table, lsblk PTUUID) or `gpt_uuid: <disk GUID>` (GPT, PTUUID of
        the DISK; a partition GUID makes Limine panic — chainload.c)
Same shape as CachyOS Calamares (bootloader/main.py, efi_chainload +
guid(partuuid)); their BIOS path uses diskseq as `drive`, which depends on
enumeration order, so Glue identifies the disk by its table id instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

# lsblk columns verified with `lsblk --list-columns` (util-linux 2.41).
LSBLK_ARGV: Tuple[str, ...] = (
    "lsblk", "--json", "-o",
    "PATH,TYPE,RM,HOTPLUG,PKNAME,UUID,PARTUUID,PTTYPE,PTUUID,PARTN",
)
OS_PROBER_ARGV: Tuple[str, ...] = ("os-prober",)
# Where the Artix live initramfs mounts the boot medium.
LIVE_BOOTMNT = "/run/artix/bootmnt"
BOOT_D = "/etc/glue/boot.d"

_HEX8 = 8
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_PRIORITY_WINDOWS = "10"
_PRIORITY_EFI = "20"
_PRIORITY_BIOS = "30"


@dataclass(frozen=True)
class ProbeLine:
    device: str                  # /dev/nvme0n1p1
    loader: Optional[str]        # /EFI/Microsoft/Boot/bootmgfw.efi (efi only)
    long_name: str               # Windows Boot Manager
    short_name: str              # Windows
    kind: str                    # efi | chain | linux | ...


@dataclass(frozen=True)
class ForeignOS:
    title: str
    device: str
    partuuid: str
    fs_uuid: str
    disk: str                    # /dev/nvme0n1
    kind: str                    # 'efi' | 'bios'
    loader: Optional[str]
    # BIOS chainload only: table type + table id + partition number
    pttype: str = ""
    ptuuid: str = ""
    partn: int = 0


@dataclass(frozen=True)
class Skipped:
    device: str
    reason: str


@dataclass
class DetectResult:
    entries: List[ForeignOS] = field(default_factory=list)
    skipped: List[Skipped] = field(default_factory=list)


# ---------------------------------------------------------------------------
# os-prober
# ---------------------------------------------------------------------------

def parse_os_prober(text: str) -> List[ProbeLine]:
    """`device[@/loader]:long name:short name:type` per line. Malformed lines
    (fewer than 4 fields, no /dev device) are dropped; a loader is only kept
    when it is an absolute path."""
    lines: List[ProbeLine] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(":")
        if len(fields) < 4:
            continue
        device_field, long_name, short_name = fields[0], fields[1], fields[2]
        kind = ":".join(fields[3:]).strip().lower()
        device, _, loader = device_field.partition("@")
        device = device.strip()
        loader = loader.strip() or None
        if not device.startswith("/dev/") or " " in device or not kind:
            continue
        if loader is not None and not loader.startswith("/"):
            loader = "/" + loader
        lines.append(ProbeLine(device, loader, long_name.strip() or short_name.strip(),
                               short_name.strip() or long_name.strip(), kind))
    return lines


# ---------------------------------------------------------------------------
# lsblk
# ---------------------------------------------------------------------------

def _flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes")


def _str(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def block_devices(lsblk_json: str) -> Dict[str, dict]:
    """PATH -> normalised record for every node of the lsblk tree (or list).
    Malformed JSON gives an empty mapping."""
    try:
        data = json.loads(lsblk_json or "")
        nodes = data["blockdevices"]
    except (ValueError, KeyError, TypeError):
        return {}
    out: Dict[str, dict] = {}

    def walk(items, parent: str) -> None:
        if not isinstance(items, list):
            return
        for node in items:
            if not isinstance(node, dict):
                continue
            path = _str(node.get("path"))
            if not path:
                continue
            pkname = _str(node.get("pkname")) or parent
            partn = node.get("partn")
            try:
                partn = int(partn) if partn not in (None, "") else 0
            except (TypeError, ValueError):
                partn = 0
            out[path] = {
                "path": path,
                "type": _str(node.get("type")).lower(),
                "rm": _flag(node.get("rm")),
                "hotplug": _flag(node.get("hotplug")),
                "pkname": pkname,
                "uuid": _str(node.get("uuid")),
                "partuuid": _str(node.get("partuuid")).lower(),
                "pttype": _str(node.get("pttype")).lower(),
                "ptuuid": _str(node.get("ptuuid")).lower(),
                "partn": partn,
            }
            walk(node.get("children"), path.rsplit("/", 1)[-1])

    walk(nodes, "")
    return out


def disk_of(devices: Dict[str, dict], path: Optional[str]) -> Optional[str]:
    """Whole-disk path (/dev/sda) holding `path`; the path itself when it is a
    disk; None when unknown. Follows PKNAME up through nested nodes."""
    if not path:
        return None
    node = devices.get(path)
    seen = set()
    while node is not None and node["pkname"] and node["path"] not in seen:
        seen.add(node["path"])
        node = devices.get("/dev/" + node["pkname"])
    if node is None:
        return None
    return node["path"]


def live_disk(lsblk_json: str, bootmnt_source: Optional[str]) -> Optional[str]:
    """Disk of the live medium from `findmnt -no SOURCE /run/artix/bootmnt`
    (the old GRUB-era helper, now pure). A loop device (ISO file on a disk) or
    an unknown source gives None."""
    src = (bootmnt_source or "").strip().split("\n")[0].split("[")[0]
    return disk_of(block_devices(lsblk_json), src or None)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _is_windows(line: ProbeLine) -> bool:
    return "windows" in (line.short_name + " " + line.long_name).lower()


def foreign_entries(probe_text: str, lsblk_json: str, *, firmware: str,
                    live_disk: Optional[str], target_root: Optional[str],
                    target_boot: Optional[str],
                    erased_disk: Optional[str] = None) -> DetectResult:
    """Normalise os-prober lines against lsblk and decide, per line, whether
    it becomes a Limine chainload entry.

    Excluded (DetectResult.skipped, with reason): partitions on `live_disk`;
    partitions on removable/hotplug disks that are not the target disk (disk
    of `target_root`/`target_boot`); `target_root` itself; every partition of
    `erased_disk` (erase mode wipes it); devices lsblk does not know; no
    PARTUUID; kinds the firmware cannot chainload (`linux` needs
    linux-boot-prober; `chain` on UEFI; `efi` on BIOS). The target disk and a
    reused ESP (`target_boot`) holding a foreign loader are KEPT.
    Deduplicated on (partuuid, loader).
    """
    if firmware not in ("uefi", "bios"):
        raise ValueError(f"firmware must be uefi or bios, got {firmware!r}")
    devices = block_devices(lsblk_json)
    target_disks = {d for d in (disk_of(devices, target_root),
                                disk_of(devices, target_boot)) if d}
    result = DetectResult()
    seen = set()
    for line in parse_os_prober(probe_text):
        dev = devices.get(line.device)
        skip = _skip_reason(line, dev, devices, firmware, live_disk,
                            target_root, target_disks, erased_disk)
        if skip:
            result.skipped.append(Skipped(line.device, skip))
            continue
        assert dev is not None
        loader = line.loader if firmware == "uefi" else None
        key = (dev["partuuid"], loader)
        if key in seen:
            continue
        seen.add(key)
        disk = disk_of(devices, line.device) or ""
        disk_node = devices.get(disk, {})
        result.entries.append(ForeignOS(
            title=line.long_name, device=line.device, partuuid=dev["partuuid"],
            fs_uuid=dev["uuid"], disk=disk,
            kind="efi" if firmware == "uefi" else "bios", loader=loader,
            pttype=disk_node.get("pttype", ""), ptuuid=disk_node.get("ptuuid", ""),
            partn=dev["partn"]))
    return result


def _skip_reason(line: ProbeLine, dev: Optional[dict], devices: Dict[str, dict],
                 firmware: str, live: Optional[str], target_root: Optional[str],
                 target_disks: set, erased_disk: Optional[str]) -> Optional[str]:
    if dev is None:
        return "not listed by lsblk"
    disk = disk_of(devices, line.device)
    if target_root and line.device == target_root:
        return "this partition is formatted for Glue Linux"
    if erased_disk and disk == erased_disk:
        return "its disk is erased by this installation"
    if live and disk == live:
        return "on the live medium"
    disk_node = devices.get(disk or "", {})
    if (disk_node.get("rm") or disk_node.get("hotplug")) and disk not in target_disks:
        return "on a removable disk"
    if line.kind == "linux":
        return "needs linux-boot-prober"
    if firmware == "uefi" and line.kind != "efi":
        return "BIOS boot record, not bootable from UEFI"
    if firmware == "bios" and line.kind != "chain":
        return "EFI loader, not bootable from BIOS"
    if firmware == "uefi" and not line.loader:
        return "no EFI loader path reported"
    if not dev["partuuid"]:
        return "no PARTUUID"
    if firmware == "bios":
        if disk_node.get("pttype") == "gpt":
            if not disk_node.get("ptuuid") or not dev["partn"]:
                return "GPT disk id or partition number unknown"
        elif disk_node.get("pttype") == "dos":
            if not re.fullmatch(r"[0-9a-f]{8}", disk_node.get("ptuuid", "")) or not dev["partn"]:
                return "MBR disk id or partition number unknown"
        else:
            return "unsupported partition table for BIOS chainload"
    return None


# ---------------------------------------------------------------------------
# boot.d fragments + summary
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-") or "os"


def entry_text(os_: ForeignOS) -> str:
    """One limine.conf fragment (Limine v12.9.0 keys only)."""
    title = os_.title.replace("\n", " ").strip()
    lines = [f"# {title} on {os_.device} (os-prober)", f"/{title}"]
    if os_.kind == "efi":
        lines += ["protocol: efi", f"image_path: guid({os_.partuuid}):{os_.loader}"]
    else:
        lines.append("protocol: bios")
        if os_.pttype == "gpt":
            lines.append(f"gpt_uuid: {os_.ptuuid}")
        else:
            lines.append(f"mbr_id: {os_.ptuuid}")
        lines.append(f"partition: {os_.partn}")
    return "\n".join(lines) + "\n"


def boot_d_files(result: DetectResult) -> List[Tuple[str, str]]:
    """(filename, text) per entry, sorted by filename. Names:
    NN-<slug>-<first 8 hex of PARTUUID>.conf, NN = 10 Windows, 20 other EFI,
    30 BIOS; a clash (MBR PARTUUIDs share the disk id) gets a -2/-3 suffix."""
    files: List[Tuple[str, str]] = []
    used = set()
    for os_ in result.entries:
        windows = "windows" in os_.title.lower()
        nn = _PRIORITY_WINDOWS if windows else (
            _PRIORITY_EFI if os_.kind == "efi" else _PRIORITY_BIOS)
        hexpart = re.sub(r"[^0-9a-f]", "", os_.partuuid)[:_HEX8]
        base = f"{nn}-{_slug(os_.title)}-{hexpart}"
        name, n = base, 1
        while name in used:
            n += 1
            name = f"{base}-{n}"
        used.add(name)
        files.append((name + ".conf", entry_text(os_)))
    return sorted(files)


def summary_lines(result: DetectResult) -> List[str]:
    """English lines for the pre-confirmation summary."""
    out: List[str] = []
    if result.entries:
        kept = ", ".join(f"{e.title} ({e.device})" for e in result.entries)
        out.append(f"Other operating systems kept in the boot menu: {kept}")
    if result.skipped:
        dropped = ", ".join(f"{s.device} ({s.reason})" for s in result.skipped)
        out.append(f"Not added to the boot menu: {dropped}")
    if not out:
        out.append("No other operating system was detected.")
    return out


# ---------------------------------------------------------------------------
# Live-side driver (commands injected)
# ---------------------------------------------------------------------------

def detect_other_os(disk_plan, *, env: Mapping[str, str],
                    capture: Callable[[Sequence[str]], str]):
    """Driver for __main__: os-prober + lsblk + findmnt on the live side via
    the injected `capture(argv) -> stdout` (raises OSError on failure);
    GLUE_OSPROBER_OUTPUT / GLUE_LSBLK_JSON / GLUE_LIVE_DISK in `env` replace
    the commands. Never fails the install: a missing os-prober gives an empty
    result + a warning. Returns (DetectResult, warnings)."""
    warnings = []
    probe, lsblk = env.get("GLUE_OSPROBER_OUTPUT"), env.get("GLUE_LSBLK_JSON")
    try:
        if probe is None:
            probe = capture(OS_PROBER_ARGV)
    except (OSError, ValueError) as exc:
        warnings.append(f"os-prober unavailable ({exc}); other operating "
                        "systems are not added to the boot menu")
        probe = ""
    try:
        if lsblk is None:
            lsblk = capture(LSBLK_ARGV)
        live = env.get("GLUE_LIVE_DISK") or live_disk(
            lsblk, capture(["findmnt", "-no", "SOURCE", LIVE_BOOTMNT]))
    except (OSError, ValueError):
        lsblk = lsblk or ""
        live = env.get("GLUE_LIVE_DISK")
    root = next((p.path for p in disk_plan.partitions if p.mountpoint == "/"), None)
    boot = next((p.path for p in disk_plan.partitions if p.mountpoint == "/boot"), None)
    result = foreign_entries(
        probe, lsblk, firmware=disk_plan.firmware, live_disk=live,
        target_root=root, target_boot=boot,
        erased_disk=disk_plan.device_path if disk_plan.mode == "erase" else None)
    return result, warnings
