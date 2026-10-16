"""Other operating systems on their own EFI system partition.

os-prober reports Windows (and a few other EFI loaders it knows) but a Linux
booted by its own Limine, systemd-boot or GRUB on another ESP only shows up
as a `linux` line, which Limine cannot chainload. esp_entries() lists every
other ESP instead (IO injected: `list_esp(device)` returns the .efi paths)
and turns each vendor loader into an osdetect.ForeignOS, so the boot.d
fragments come out exactly like the os-prober ones. Pure module.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from glue_installer.osdetect import (
    ESP_PARTTYPE, DetectResult, ForeignOS, Skipped, block_devices, disk_of,
    parse_os_prober,
)



# EFI/<vendor> directory -> (label, loader file names in preference order)
_LOADERS = (
    ("microsoft", "Windows Boot Manager", ("boot/bootmgfw.efi",)),
    ("limine", "Limine", ("bootx64.efi", "limine_x64.efi")),
    ("systemd", "systemd-boot", ("systemd-bootx64.efi",)),
    ("refind", "rEFInd", ("refind_x64.efi",)),
)
_DISTRO_LOADERS = ("shimx64.efi", "grubx64.efi", "bootx64.efi")
_GENERIC_VENDORS = {"boot", "limine", "systemd", "refind", "grub", "microsoft",
                    "linux", "tools", "dell", "hp", "lenovo", "asus", "acer"}


def _esp_loaders(paths: Sequence[str]) -> List[Tuple[str, str]]:
    """[(vendor, /EFI/Vendor/loader.efi)] from relative paths under an ESP
    (`./EFI/limine/BOOTX64.EFI`). One loader per vendor directory; the
    removable-media fallback EFI/BOOT/BOOTX64.EFI only when nothing else."""
    by_vendor: Dict[str, Dict[str, str]] = {}
    for raw in paths:
        rel = raw.strip().lstrip("./").replace("\\", "/")
        parts = rel.split("/")
        if len(parts) < 3 or parts[0].lower() != "efi":
            continue
        vendor = parts[1].lower()
        by_vendor.setdefault(vendor, {})["/".join(parts[2:]).lower()] = "/" + rel
    found: List[Tuple[str, str]] = []
    for vendor in sorted(by_vendor):
        files = by_vendor[vendor]
        if vendor == "boot":
            continue
        names: Sequence[str] = _DISTRO_LOADERS
        for key, _label, wanted in _LOADERS:
            if vendor == key:
                names = wanted
        for name in names:
            if name in files:
                found.append((vendor, files[name]))
                break
    if not found and "bootx64.efi" in by_vendor.get("boot", {}):
        found.append(("boot", by_vendor["boot"]["bootx64.efi"]))
    return found


# menu entries that are not an operating system of that disk (CachyOS's
# Limine lists "Other systems and bootloaders", "EFI fallback", Windows, ...)
_NOT_AN_OS = re.compile(r"other systems|bootloader|fallback|firmware|uefi|windows|"
                        r"reboot|shut ?down|power ?off|memtest|glue linux", re.I)
_MAX_NAMES = 3


def _esp_titles(lines: Sequence[str]) -> List[str]:
    """OS names from the `TITLE:` lines of osdetect.ESP_LIST_SCRIPT (the ESP's
    own Limine/systemd-boot menu), generic entries left out, at most 3."""
    names: List[str] = []
    for line in lines:
        if line.startswith("TITLE:"):
            name = line[len("TITLE:"):].strip()
            if name and not _NOT_AN_OS.search(name) and name not in names:
                names.append(name)
    return names[:_MAX_NAMES]


def _loader_title(vendor: str, names: Sequence[str]) -> str:
    """Menu title: the OS names os-prober found on that disk when the loader
    is a generic one (Limine, systemd-boot, ...), else the vendor's OS."""
    label = {key: lab for key, lab, _ in _LOADERS}.get(vendor)
    if vendor == "microsoft":
        return "Windows Boot Manager"
    if vendor == "boot":
        label = "UEFI loader"
    if label is None and vendor not in _GENERIC_VENDORS:
        for name in names:
            if vendor in name.lower().replace(" ", ""):
                return name
        return vendor[:1].upper() + vendor[1:]
    label = label or vendor.upper()
    return f"{', '.join(names)} ({label})" if names else label


def esp_entries(probe_text: str, lsblk_json: str,
                list_esp: Callable[[str], List[str]], *,
                live_disk: Optional[str], target_root: Optional[str],
                target_boot: Optional[str],
                erased_disk: Optional[str] = None) -> DetectResult:
    """UEFI only: chainload entries for the loaders on every OTHER ESP.

    os-prober reports Windows but not a Linux on its own ESP with Limine,
    systemd-boot or GRUB (it lists that Linux as `linux`, which Limine cannot
    chainload). Here each ESP (PARTTYPE c12a7328-...) that is not the target
    /boot, not on the live, erased or a removable disk, is listed with
    `list_esp(device)` and every vendor loader becomes an entry named after
    the os-prober `linux` OSes of the same disk. A failing list_esp only
    skips that ESP."""
    devices = block_devices(lsblk_json)
    names_by_disk: Dict[str, List[str]] = {}
    for line in parse_os_prober(probe_text):
        if line.kind == "linux":
            disk = disk_of(devices, line.device)
            if disk and line.device != target_root:
                names = names_by_disk.setdefault(disk, [])
                if line.long_name not in names:
                    names.append(line.long_name)
    target_disks = {d for d in (disk_of(devices, target_root),
                                disk_of(devices, target_boot)) if d}
    result = DetectResult()
    for path in sorted(devices):
        dev = devices[path]
        if dev["type"] != "part" or dev["parttype"] != ESP_PARTTYPE:
            continue
        if path == target_boot:
            continue
        disk = disk_of(devices, path)
        disk_node = devices.get(disk or "", {})
        reason = None
        if erased_disk and disk == erased_disk:
            reason = "its disk is erased by this installation"
        elif live_disk and disk == live_disk:
            reason = "on the live medium"
        elif (disk_node.get("rm") or disk_node.get("hotplug")) and disk not in target_disks:
            reason = "on a removable disk"
        elif not dev["partuuid"]:
            reason = "no PARTUUID"
        if reason:
            result.skipped.append(Skipped(path, reason))
            continue
        try:
            listing = list_esp(path)
        except Exception as exc:  # noqa: BLE001 - one unreadable ESP skips only itself
            result.skipped.append(Skipped(path, f"could not be read ({exc})"))
            continue
        names = names_by_disk.get(disk or "", []) or _esp_titles(listing)
        for vendor, loader in _esp_loaders(listing):
            result.entries.append(ForeignOS(
                title=_loader_title(vendor, names),
                device=path, partuuid=dev["partuuid"], fs_uuid=dev["uuid"],
                disk=disk or "", kind="efi", loader=loader))
    return result


def merge_esp_entries(result: DetectResult, extra: DetectResult) -> None:
    """Add esp_entries() to os-prober's result, skipping loaders os-prober
    already reported (same partition and same path, case-insensitive) and
    a skipped reason already given for that device."""
    have = {(e.partuuid, (e.loader or "").lower()) for e in result.entries}
    for e in extra.entries:
        if (e.partuuid, (e.loader or "").lower()) not in have:
            have.add((e.partuuid, (e.loader or "").lower()))
            result.entries.append(e)
    skipped = {s.device for s in result.skipped}
    result.skipped.extend(s for s in extra.skipped if s.device not in skipped)
