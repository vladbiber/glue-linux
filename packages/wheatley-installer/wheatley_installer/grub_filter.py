"""
Post-process the generated grub.cfg: keep os-prober's entries for the OTHER
operating systems on the machine, but drop the ones that point at the live
install medium (the plugged-in USB stick).

Why: the user wants dual-boot entries (Windows, other Linux) in the installed
GRUB menu, so os-prober is ON — but with the install USB still plugged in,
os-prober also clones the stick's own boot entries ("live installer" style
menu items) into the installed menu. Those are identified by DEVICE, not by
title: any top-level menuentry/submenu block that references a partition
path/UUID/PARTUUID belonging to a removable disk or to the disk the live
medium booted from gets removed.

Pure core (`filter_grub_cfg`, `removable_identifiers`) + a best-effort `main`
that never fails the install over menu cosmetics (always exits 0).
"""

from __future__ import annotations

import json
import sys
from typing import Iterable, List, Set

# Where the Artix live initramfs mounts the boot medium.
_LIVE_BOOTMNT = "/run/artix/bootmnt"

_LSBLK_ARGV = [
    "lsblk", "--json", "-o", "PATH,TYPE,RM,UUID,PARTUUID",
]

# Identifiers shorter than this are too ambiguous to grep a grub.cfg for
# (an empty UUID would match every block and empty the whole menu).
_MIN_ID_LEN = 4


def removable_identifiers(
    lsblk_json: str,
    extra_disks: Iterable[str] = (),
    exclude_disks: Iterable[str] = (),
) -> List[str]:
    """Partition paths/UUIDs/PARTUUIDs of every partition living on a
    removable disk OR on one of `extra_disks` (e.g. the live boot disk).
    Disks in `exclude_disks` (the INSTALL TARGET — it may itself be a
    removable USB drive) are never included, so the new system's own boot
    entries can't be filtered away. Pure; returns [] on malformed input."""
    try:
        data = json.loads(lsblk_json)
        disks = data["blockdevices"]
    except (ValueError, KeyError, TypeError):
        return []
    extra = set(extra_disks)
    excluded = set(exclude_disks)
    ids: Set[str] = set()
    for disk in disks:
        if not isinstance(disk, dict) or disk.get("type") != "disk":
            continue
        if disk.get("path") in excluded:
            continue
        if not (disk.get("rm") or disk.get("path") in extra):
            continue
        for child in disk.get("children") or []:
            if not isinstance(child, dict):
                continue
            for key in ("path", "uuid", "partuuid"):
                value = child.get(key)
                if isinstance(value, str) and len(value) >= _MIN_ID_LEN:
                    ids.add(value)
    return sorted(ids)


def filter_grub_cfg(text: str, identifiers: Iterable[str]) -> str:
    """Remove every top-level menuentry/submenu block that mentions any of
    the identifiers. Non-block lines and clean blocks pass through verbatim.
    Pure text transformation."""
    ids = [i for i in identifiers if len(i) >= _MIN_ID_LEN]
    if not ids:
        return text
    out: List[str] = []
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        starts_block = (
            line == stripped  # top level only (no indentation)
            and (stripped.startswith("menuentry ") or stripped.startswith("submenu "))
        )
        if not starts_block:
            out.append(line)
            i += 1
            continue
        # Collect the whole block by brace depth (grub-mkconfig output keeps
        # braces out of quoted titles, so plain counting is reliable here).
        depth = 0
        block: List[str] = []
        while i < len(lines):
            block.append(lines[i])
            depth += lines[i].count("{") - lines[i].count("}")
            i += 1
            if depth <= 0:
                break
        block_text = "".join(block)
        if not any(ident in block_text for ident in ids):
            out.append(block_text)
    return "".join(out)


def _disk_of(mount_point: str) -> List[str]:
    """Disk path (e.g. ['/dev/sda']) backing the given mount point, best-effort."""
    import subprocess
    try:
        src = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", mount_point],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if not src:
            return []
        disk = subprocess.run(
            ["lsblk", "-no", "PKNAME", src],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip().splitlines()
        return [f"/dev/{disk[0]}"] if disk and disk[0] else []
    except Exception:
        return []


def main(argv=None) -> int:
    """Filter live-USB entries out of the given grub.cfg (default
    /mnt/boot/grub/grub.cfg). Best-effort: any failure leaves the file
    untouched and still exits 0 — a cosmetic menu must never fail an install."""
    import os
    import subprocess
    args = sys.argv[1:] if argv is None else list(argv)
    path = args[0] if args else "/mnt/boot/grub/grub.cfg"
    # cfg lives at <target>/boot/grub/grub.cfg — the target mount is 3 up
    target_mount = os.path.dirname(os.path.dirname(os.path.dirname(path))) or "/"
    try:
        lsblk_json = subprocess.run(
            _LSBLK_ARGV, capture_output=True, text=True, timeout=10,
        ).stdout
        ids = removable_identifiers(
            lsblk_json,
            extra_disks=_disk_of(_LIVE_BOOTMNT),
            exclude_disks=_disk_of(target_mount),
        )
        if not ids:
            return 0
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        filtered = filter_grub_cfg(text, ids)
        if filtered != text:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(filtered)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
