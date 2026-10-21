"""Calamares job `gluemounts` (Glue Linux).

Runs right before Calamares' umount job. A process that still held /sys (or
proc, dev) when an artix-chroot step ended leaves that step's mount stacked
over Calamares' own, and umount then fails on the efivarfs it hides. Each
stacked copy under rootMountPoint is unmounted lazily; a target without any
is left as it is. libcalamares is imported lazily (tests fake it).
"""

import os
import subprocess
import sys

_PKG_PATHS = ("/usr/share/glue-installer", "/usr/lib/glue-installer")
for _p in _PKG_PATHS:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from glue_installer.stale_mounts import parse_mountinfo, stacked_mounts  # noqa: E402


def pretty_name():
    return "Releasing leftover mounts"


def _calamares():
    import libcalamares
    return libcalamares


def _umount_lazy(path):
    return subprocess.run(["umount", "-l", path], capture_output=True, text=True)


def run(mountinfo="/proc/self/mountinfo", umount=_umount_lazy):
    """Calamares entry point: always None; a failed unmount is only logged and
    left for the umount job to report."""
    cala = _calamares()
    root = cala.globalstorage.value("rootMountPoint")
    if not isinstance(root, str) or not root.startswith("/"):
        cala.utils.debug("gluemounts: no rootMountPoint, nothing to do")
        return None
    try:
        with open(mountinfo, encoding="utf-8") as fh:
            entries = parse_mountinfo(fh.read())
    except OSError as exc:
        cala.utils.warning(f"gluemounts: {mountinfo}: {exc}")
        return None
    for path in stacked_mounts(entries, root):
        res = umount(path)
        if res.returncode == 0:
            cala.utils.debug(f"gluemounts: unmounted leftover {path}")
        else:
            cala.utils.warning(f"gluemounts: {path}: {res.stderr.strip()}")
    cala.job.setprogress(1.0)
    return None
