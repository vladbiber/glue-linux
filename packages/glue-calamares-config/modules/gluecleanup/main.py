"""Calamares job `gluecleanup` (Glue Linux).

Runs before the partition job. When an earlier install failed (say at the
keyring), its target stays mounted under /tmp/calamares-root-* and a swap
partition on the disk may still be active, so the second try dies with
"Failed to add partition" from sfdisk. Every leftover target is unmounted
recursively and disk swap is turned off; zram stays. libcalamares is
imported lazily (tests fake it).
"""

import os
import subprocess
import sys

_PKG_PATHS = ("/usr/share/glue-installer", "/usr/lib/glue-installer")
for _p in _PKG_PATHS:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from glue_installer.stale_mounts import (  # noqa: E402
    disk_swaps, leftover_roots, parse_mountinfo)


def pretty_name():
    return "Releasing a previous install attempt"


def _calamares():
    import libcalamares
    return libcalamares


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True)


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def run(mountinfo="/proc/self/mountinfo", proc_swaps="/proc/swaps", cmd=_run):
    """Calamares entry point: always None; failures are only logged and left
    for the partition job to report."""
    cala = _calamares()
    for root in leftover_roots(parse_mountinfo(_read(mountinfo))):
        res = cmd(["umount", "-R", "-l", root])
        if res.returncode == 0:
            cala.utils.debug(f"gluecleanup: unmounted leftover {root}")
        else:
            cala.utils.warning(f"gluecleanup: {root}: {res.stderr.strip()}")
    for dev in disk_swaps(_read(proc_swaps)):
        res = cmd(["swapoff", dev])
        if res.returncode == 0:
            cala.utils.debug(f"gluecleanup: swapoff {dev}")
        else:
            cala.utils.warning(f"gluecleanup: swapoff {dev}: {res.stderr.strip()}")
    cala.job.setprogress(1.0)
    return None
