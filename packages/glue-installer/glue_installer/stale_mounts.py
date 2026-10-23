"""Mounts artix-chroot left behind under the install target.

artix-chroot mounts proc, sys, dev and efivarfs over the ones Calamares'
mount module already set up and unmounts them when the command ends. When a
process still holds one of them at that moment ("target is busy"), the copy
stays, stacked over Calamares' own mount, and the final umount job then fails
on the mount it hides (efivarfs under a leftover read-only /sys).
"""

from __future__ import annotations

from typing import Iterable, List, Tuple


def _unescape(path: str) -> str:
    # mountinfo writes space, tab, newline and backslash as octal escapes
    for esc, ch in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        path = path.replace(esc, ch)
    return path


def parse_mountinfo(text: str) -> List[Tuple[int, int, str]]:
    """(mount id, parent id, mount point) for each line of /proc/self/mountinfo."""
    out = []
    for line in text.splitlines():
        f = line.split()
        if len(f) < 5:
            continue
        try:
            out.append((int(f[0]), int(f[1]), _unescape(f[4])))
        except ValueError:
            continue
    return out


def stacked_mounts(entries: Iterable[Tuple[int, int, str]], root: str) -> List[str]:
    """Mount points under `root` with a second mount stacked on the first.

    Each returned path is one lazy unmount away from the original mount: only
    the lowest stacked copy is listed (its own children go with it), newest
    first."""
    root = root.rstrip("/") or "/"
    entries = list(entries)
    by_id = {mid: (parent, path) for mid, parent, path in entries}

    def under(path: str) -> bool:
        return path == root or path.startswith(root + "/")

    def stacked(mid: int) -> bool:
        parent, path = by_id[mid]
        return parent in by_id and by_id[parent][1] == path

    picked = [(mid, path) for mid, parent, path in entries
              if under(path) and path != root and stacked(mid)
              and not (parent in by_id and stacked(parent))]
    return [path for _mid, path in sorted(picked, reverse=True)]


# Calamares mounts every target under a fresh /tmp/calamares-root-XXXX
LEFTOVER_PREFIX = "/tmp/calamares-root-"


def leftover_roots(entries: Iterable[Tuple[int, int, str]],
                   prefix: str = LEFTOVER_PREFIX) -> List[str]:
    """Target roots a failed earlier run left mounted (the partition job
    cannot rewrite a disk with mounted partitions: sfdisk --append fails)."""
    roots = {path for _mid, _parent, path in entries
             if path.startswith(prefix) and "/" not in path[len(prefix):]}
    return sorted(roots)


def disk_swaps(proc_swaps: str) -> List[str]:
    """Swap devices on real disks from /proc/swaps; zram stays on."""
    out = []
    for line in proc_swaps.splitlines()[1:]:
        f = line.split()
        if f and f[0].startswith("/dev/") and not f[0].startswith("/dev/zram"):
            out.append(f[0])
    return out
