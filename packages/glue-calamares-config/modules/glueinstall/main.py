"""Calamares job `glueinstall` (Glue Linux, roadmap 10.3, ADR-023).

Reads rootMountPoint + the Glue selection, compiles the Glue steps with
glue_installer.calamares_adapter and runs them on the host (the steps chroot
with artix-chroot themselves). libcalamares is imported lazily so tests can
substitute a fake module. Passwords never reach this module: the selection
keys it reads are listed in calamares_adapter.SELECTION_KEYS.
"""

import json
import os
import sys

_PKG_PATHS = ("/usr/share/glue-installer", "/usr/lib/glue-installer")
for _p in _PKG_PATHS:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from glue_installer.calamares_adapter import (  # noqa: E402
    AdapterError, compile_adapter, parse_selection, swapfile_fstab_line,
)
from glue_installer.calamares_config import (  # noqa: E402
    has_page_values, page_keys, selection_from_globalstorage,
)
from glue_installer.executor import ExecutorError, execute  # noqa: E402

_DEFAULT_SELECTION_FILE = "/etc/glue/selection.json"
_DEFAULT_LOG = "/var/log/glue-install.log"


def pretty_name():
    return "Installing Glue Linux"


def _calamares():
    import libcalamares
    return libcalamares


def _gs_partitions(gs):
    """Root/boot/swap device paths and root fs from globalstorage `partitions`."""
    root = boot = swap = None
    fstype = "ext4"
    for part in gs.value("partitions") or []:
        if not isinstance(part, dict):
            continue
        mp, fs, dev = part.get("mountPoint"), part.get("fs", ""), part.get("device")
        if mp == "/":
            root, fstype = dev, (part.get("fsName") or fs or "ext4")
        elif mp == "/boot":
            boot = dev
        elif fs in ("linuxswap", "swap"):
            swap = dev
    return root, boot, swap, fstype


def _firmware(gs):
    kind = gs.value("firmwareType")
    if kind in ("efi", "uefi"):
        return "uefi"
    if kind == "bios":
        return "bios"
    return "uefi" if os.path.isdir("/sys/firmware/efi") else "bios"


def _load_selection(gs, config, catalog):
    """glue_selection dict > the packagechooser@glue* pages > selection_file."""
    sel = gs.value("glue_selection")
    if isinstance(sel, dict):
        return sel
    pages = {key: gs.value(key) for key in page_keys(catalog)}
    if has_page_values(pages):
        return selection_from_globalstorage(pages, catalog)
    path = config.get("selection_file", _DEFAULT_SELECTION_FILE)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _host_facts():
    from glue_installer.cpu import detect_cpu_v3
    from glue_installer.disk_swap import read_ram_bytes, staged_pacman_conf
    from glue_installer.gpu import detect_gpu_vendors
    from glue_installer.laptop import (
        detect_amd_pstate_active, detect_cpu_vendor, detect_laptop)
    return dict(ram_bytes=read_ram_bytes(), is_laptop=detect_laptop(),
                gpu_vendors=detect_gpu_vendors(), cpu_v3=detect_cpu_v3(),
                cpu_vendor_id=detect_cpu_vendor(),
                amd_pstate_active=detect_amd_pstate_active(),
                stage_conf=staged_pacman_conf)


def _other_os(gs, firmware, root, boot):
    """os-prober on the live side (never fatal), via osdetect's driver."""
    import subprocess
    from glue_installer.calamares_adapter import _disk_plan
    from glue_installer.osdetect import detect_other_os

    def capture(argv):
        return subprocess.run(list(argv), check=True, capture_output=True,
                              text=True).stdout
    try:
        return detect_other_os(_disk_plan(firmware, root, boot, None),
                               env=os.environ, capture=capture)
    except Exception as exc:  # noqa: BLE001 - detection must never block the install
        return None, [f"other OS detection skipped ({exc})"]


def _root_bytes(root_mount):
    try:
        st = os.statvfs(root_mount)
        return st.f_frsize * st.f_blocks
    except OSError:
        return 0


def run(execute_steps=execute, facts=None):
    """Calamares entry point: None on success, (title, message) on failure."""
    cala = _calamares()
    gs = cala.globalstorage
    config = cala.job.configuration or {}
    root_mount = gs.value("rootMountPoint")
    try:
        if not isinstance(root_mount, str) or not root_mount:
            raise AdapterError("rootMountPoint is missing from globalstorage: the "
                               "mount module must run before glueinstall")
        from pathlib import Path
        from glue_installer.__main__ import find_catalog
        from glue_installer.catalog import load_catalog
        catalog = load_catalog(Path(find_catalog()))
        selection = parse_selection(_load_selection(gs, config, catalog), catalog)
        facts = dict(facts or _host_facts())
        stage_conf = facts.pop("stage_conf", None)
        firmware = _firmware(gs)
        root, boot, swap, fstype = _gs_partitions(gs)
        other_os, os_warnings = (None, [])
        if os.path.exists("/usr/bin/os-prober"):
            other_os, os_warnings = _other_os(gs, firmware, root, boot)
        pacman_conf = config.get("pacman_conf")
        if pacman_conf and not os.path.exists(pacman_conf):
            pacman_conf = None
        if pacman_conf and stage_conf is not None:
            pacman_conf = stage_conf(pacman_conf, facts.get("cpu_v3", False),
                                     selection.kernel_id)
        result = compile_adapter(
            selection, catalog, root_mount=root_mount, firmware=firmware,
            swap_partition=swap, root_device=root, boot_device=boot,
            root_bytes=_root_bytes(root_mount), root_fstype=fstype,
            pacman_conf=pacman_conf, other_os=other_os, **facts)
    except (AdapterError, OSError, ValueError) as exc:
        return ("Glue Linux configuration rejected", str(exc))
    for line in list(result.warnings) + list(os_warnings):
        cala.utils.warning(line)
    total = len(result.steps)

    def progress(i, n, description):
        cala.utils.debug(f"glueinstall [{i}/{n}] {description}")
        cala.job.setprogress(i / n if n else 1.0)

    try:
        execute_steps(result.steps, dry_run=False, progress=progress,
                      output_path=config.get("log_file", _DEFAULT_LOG))
    except ExecutorError as exc:
        return ("Glue Linux installation failed", str(exc))
    if result.swapfile_path:
        # Calamares' fstab job runs AFTER this module and only lists partitions:
        # record the swapfile line now; the fstab module appends its own lines.
        gs.insert("glue_swapfile_fstab_line", swapfile_fstab_line())
    cala.job.setprogress(1.0)
    return None
