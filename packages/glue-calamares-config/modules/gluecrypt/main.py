"""Calamares job `gluecrypt` (Glue Linux).

Only for an encrypted root (globalstorage `glue_crypt_root`, set by
glueinstall): glueinstall already built an initramfs with the `encrypt`
hook, but Calamares' keyboard job writes /etc/vconsole.conf later. This job
runs after it and rebuilds the initramfs so the passphrase prompt uses the
chosen keymap, then lets glue-boot-update rewrite limine.conf. Without the
key it is a no-op. libcalamares is imported lazily (tests fake it).
"""

import os
import sys

_PKG_PATHS = ("/usr/share/glue-installer", "/usr/lib/glue-installer")
for _p in _PKG_PATHS:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from glue_installer.calamares_adapter import AdapterError, check_root_mount  # noqa: E402
from glue_installer.crypt import rebuild_steps  # noqa: E402
from glue_installer.executor import ExecutorError, execute  # noqa: E402

_TITLE = "Glue Linux disk encryption step"
_DEFAULT_LOG = "/var/log/glue-install.log"


def pretty_name():
    return "Preparing the disk passphrase prompt"


def _calamares():
    import libcalamares
    return libcalamares


def run(is_mount=None, execute_steps=execute):
    """Calamares entry point: None on success, (title, message) on failure."""
    cala = _calamares()
    gs = cala.globalstorage
    if not gs.value("glue_crypt_root"):
        cala.utils.debug("gluecrypt: root is not encrypted, nothing to do")
        cala.job.setprogress(1.0)
        return None
    try:
        target = check_root_mount(gs.value("rootMountPoint"), is_mount)
    except AdapterError as exc:
        return (f"{_TITLE} rejected", str(exc))
    config = cala.job.configuration or {}
    try:
        execute_steps(rebuild_steps(target), dry_run=False,
                      output_path=config.get("log_file", _DEFAULT_LOG))
    except ExecutorError as exc:
        return (f"{_TITLE} failed", str(exc))
    cala.job.setprogress(1.0)
    return None
