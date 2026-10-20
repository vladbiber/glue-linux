"""Calamares job `gluefstab` (Glue Linux).

Adds the Glue swapfile line (globalstorage `glue_swapfile_fstab_line`, set by
glueinstall) to <rootMountPoint>/etc/fstab once Calamares' fstab module has
written the partition lines, and comments out the lines of an encrypted swap
Glue leaves unused (`glue_fstab_drop`, fs_spec list). Idempotent: a second run
changes nothing. Without the keys the job is a no-op. libcalamares is
imported lazily (tests fake it).
"""

import os
import sys

_PKG_PATHS = ("/usr/share/glue-installer", "/usr/lib/glue-installer")
for _p in _PKG_PATHS:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from glue_installer.calamares_adapter import AdapterError, check_root_mount  # noqa: E402
from glue_installer.calamares_config import ConfigError, append_fstab_line  # noqa: E402
from glue_installer.crypt import drop_fstab_specs  # noqa: E402

_TITLE = "Glue Linux swapfile fstab step"


def pretty_name():
    return "Adding the Glue swapfile to fstab"


def _calamares():
    import libcalamares
    return libcalamares


def run(is_mount=None):
    """Calamares entry point: None on success, (title, message) on failure.
    is_mount: injectable mountpoint test (None = os.path.ismount)."""
    cala = _calamares()
    gs = cala.globalstorage
    config = cala.job.configuration or {}
    try:
        target = check_root_mount(gs.value("rootMountPoint"), is_mount)
    except AdapterError as exc:
        return (f"{_TITLE} rejected", str(exc))
    line = gs.value("glue_swapfile_fstab_line")
    drop = gs.value("glue_fstab_drop") or []
    if not isinstance(drop, list) or not all(isinstance(d, str) for d in drop):
        return (f"{_TITLE} rejected", "glue_fstab_drop must be a list of strings")
    if (line is None or line == "") and not drop:
        cala.utils.debug("gluefstab: no swapfile line in globalstorage, nothing to do")
        cala.job.setprogress(1.0)
        return None
    if line is not None and not isinstance(line, str):
        return (f"{_TITLE} rejected", "glue_swapfile_fstab_line must be a string")
    rel = config.get("fstab", "/etc/fstab")
    if not isinstance(rel, str) or not rel.startswith("/") or ".." in rel.split("/"):
        return (f"{_TITLE} rejected", f"fstab must be an absolute path inside the target, got {rel!r}")
    fstab = target + rel
    try:
        changed = False
        if drop and os.path.exists(fstab):
            with open(fstab, encoding="utf-8") as fh:
                old = fh.read()
            new = drop_fstab_specs(old, drop)
            if new != old:
                with open(fstab, "w", encoding="utf-8") as fh:
                    fh.write(new)
                changed = True
        if line:
            changed = append_fstab_line(fstab, line) or changed
    except (OSError, ConfigError) as exc:
        return (f"{_TITLE} failed", f"{fstab}: {exc}")
    cala.utils.debug(f"gluefstab: {fstab} {'updated' if changed else 'already had the entry'}")
    cala.job.setprogress(1.0)
    return None
