"""Calamares adaptor (roadmap 10.3, ADR-023): Glue steps over a mounted root.

Pure module: no subprocess, no filesystem access, no libcalamares import.
Calamares owns partitioning, mkfs, mounting, fstab, users, locale and the
hostname; this module turns a JSON-like selection into the executor Steps
that bootstrap (basestrap), configure (plan files/skel/services) and boot
(Limine via glue-boot-update) the target Calamares has already mounted at
rootMountPoint. The `glueinstall` job module
(packages/glue-calamares-config/modules/glueinstall/main.py) runs them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, List, Mapping, Optional

from glue_installer.disks import DiskPlan, PartitionSpec
from glue_installer.executor import (
    RunCommand, Step, bootstrap_steps, config_steps, keyring_steps,
)
from glue_installer.limine import (
    BootSpec, bootloader_packages, bootloader_steps, kernel_name,
)
from glue_installer.osdetect import BOOT_D, DetectResult, boot_d_files
from glue_installer.plan import PlanError, Selection, resolve_plan
from glue_installer.plan_types import PlannedFile
from glue_installer.swap import SwapPlan, swap_plan


class AdapterError(ValueError):
    """Selection or target rejected; the message is safe to show (no secrets)."""


INITS = ("dinit", "runit", "openrc")
SWAP_MODES = ("auto", "zram", "none")
SCHEDULERS = ("scx_lavd", "scx_bpfland", "none")
FIRMWARES = ("uefi", "bios")
# Catalog shell ids; the package name noctalia-shell is accepted as an alias.
SHELLS = {"glueqs": "glueqs", "noctalia": "noctalia", "noctalia-shell": "noctalia"}
# Selection keys this adaptor reads; anything else (users, passwords…) is
# Calamares' business and is never touched or logged.
SELECTION_KEYS = ("kernel", "init", "sessions", "shell", "gaming", "scheduler",
                  "app_store", "bluetooth", "swap_mode", "hibernate")
SWAPFILE = "/swapfile"
# Calamares did the partitioning: bootloader_steps only checks the device is
# set (glue-boot-update resolves the BIOS disk from the root's PKNAME).
_CALAMARES_DEVICE = "calamares-managed"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
# Tools Calamares owns; an adaptor step may never call them.
_FORBIDDEN_RE = re.compile(
    r"(?<![\w./-])(fstabgen|mkfs(\.\w+)?|parted|sgdisk|wipefs|mount|umount|"
    r"useradd|passwd|chpasswd|locale-gen|hostnamectl)(?![\w-])")


@dataclass
class AdapterResult:
    steps: List[Step]
    swapfile_path: Optional[str] = None   # host path of the swapfile created
    warnings: List[str] = field(default_factory=list)
    packages: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def _bool(d: Mapping, key: str, default: bool) -> bool:
    value = d.get(key, default)
    if not isinstance(value, bool):
        raise AdapterError(f"selection.{key} must be true or false")
    return value


def _choice(d: Mapping, key: str, allowed, default: str) -> str:
    value = d.get(key, default)
    if not isinstance(value, str) or value not in allowed:
        raise AdapterError(
            f"selection.{key}: unknown value {value!r}; expected one of {list(allowed)}")
    return value


def _ids(value, what: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and _ID_RE.match(v)
                                              for v in value):
        raise AdapterError(f"selection.{what} must be a list of catalog ids")
    if len(set(value)) != len(value):
        raise AdapterError(f"selection.{what} has duplicates")
    return list(value)


def parse_selection(d: Mapping, catalog=None) -> Selection:
    """Validate a selection dict (the `glue_selection` globalstorage value or
    /etc/glue/selection.json) into a plan.Selection.

    Keys: kernel (catalog id, default linux-cachyos), init (dinit|runit|
    openrc), sessions (list of catalog session ids), shell (glueqs|
    noctalia[-shell], for sessions with shell_choices), gaming (bool),
    scheduler (scx_lavd|scx_bpfland|none), app_store/bluetooth (bool support
    toggles), swap_mode (auto|zram|none), hibernate (bool). Unknown keys are
    ignored (never read). With a catalog, kernel/sessions/shell are checked
    against it here; without one resolve_plan does it later.
    Raises AdapterError for a value outside its allowed set.
    """
    if not isinstance(d, Mapping):
        raise AdapterError("selection must be a JSON object")
    kernel = d.get("kernel", "linux-cachyos")
    if not isinstance(kernel, str) or not _ID_RE.match(kernel):
        raise AdapterError("selection.kernel must be a catalog kernel id")
    init = _choice(d, "init", INITS, "dinit")
    sessions = _ids(d.get("sessions", []), "sessions")
    shell = d.get("shell")
    if shell is not None and shell not in SHELLS:
        raise AdapterError(
            f"selection.shell: unknown value {shell!r}; expected one of {sorted(SHELLS)}")
    gaming = _bool(d, "gaming", False)
    scheduler = _choice(d, "scheduler", SCHEDULERS, "scx_lavd")
    support = []
    if _bool(d, "app_store", True):
        support.append("app-store")
    if _bool(d, "bluetooth", True):
        support.append("bluetooth")
    swap_mode = _choice(d, "swap_mode", SWAP_MODES, "auto")
    hibernate = _bool(d, "hibernate", False)

    shell_choice = {}
    if catalog is not None:
        if kernel not in {k.id for k in catalog.kernels}:
            raise AdapterError(f"selection.kernel: unknown kernel {kernel!r}")
        session_map = {s.id: s for s in catalog.sessions}
        for sid in sessions:
            if sid not in session_map:
                raise AdapterError(f"selection.sessions: unknown session {sid!r}")
        needs_shell = [sid for sid in sessions if session_map[sid].shell_choices]
        if needs_shell and shell is None:
            raise AdapterError(
                f"selection.shell is required for {needs_shell}")
        for sid in needs_shell:
            shell_id = SHELLS[shell]
            if shell_id not in session_map[sid].shell_choices:
                raise AdapterError(
                    f"selection.shell: {shell!r} is not a shell of session {sid!r}")
            shell_choice[sid] = shell_id
    elif shell is not None and "gluewc" in sessions:
        shell_choice["gluewc"] = SHELLS[shell]

    return Selection(
        kernel_id=kernel, init_id=init, session_ids=sessions,
        shell_choice=shell_choice, support_ids=support, gaming=gaming,
        minimal=not sessions and not gaming, scheduler=scheduler,
        swap_mode=swap_mode, hibernate=hibernate,
    )


# ---------------------------------------------------------------------------
# Target checks
# ---------------------------------------------------------------------------

def check_root_mount(root_mount: str,
                     is_mount: Optional[Callable[[str], bool]] = None) -> str:
    """The mounted Calamares root: absolute, no '..', not '/', a mountpoint.
    is_mount: None = os.path.ismount (resolved at call time, injectable).
    Returns the path without its trailing slash."""
    if is_mount is None:
        is_mount = os.path.ismount
    if not isinstance(root_mount, str) or not root_mount.startswith("/"):
        raise AdapterError(f"rootMountPoint must be an absolute path, got {root_mount!r}")
    if any(part == ".." for part in root_mount.split("/")):
        raise AdapterError(f"rootMountPoint must not contain '..': {root_mount!r}")
    t = root_mount.rstrip("/")
    if t == "":
        raise AdapterError("refusing to install onto / (the live system itself)")
    if not is_mount(t):
        raise AdapterError(f"rootMountPoint {t!r} is not a mountpoint")
    return t


def _check_device(path: Optional[str], what: str) -> Optional[str]:
    if path is None:
        return None
    if not isinstance(path, str) or not path.startswith("/dev/") or ".." in path \
            or any(c.isspace() for c in path):
        raise AdapterError(f"{what} must be a /dev path, got {path!r}")
    return path


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _disk_plan(firmware: str, root_device: Optional[str],
               boot_device: Optional[str], swap_partition: Optional[str]) -> DiskPlan:
    """Shape of what Calamares mounted, for bootloader_steps/osdetect
    (mode 'existing': nothing is formatted, no disk is erased)."""
    parts: List[PartitionSpec] = []
    if firmware == "uefi":
        parts.append(PartitionSpec(number=0, path=boot_device or "", type_code="ef00",
                                   size="0", filesystem="vfat", mountpoint="/boot"))
    if swap_partition:
        parts.append(PartitionSpec(number=0, path=swap_partition, type_code="8200",
                                   size="0", filesystem="swap", mountpoint="swap"))
    parts.append(PartitionSpec(number=0, path=root_device or "", type_code="8300",
                               size="0", filesystem="ext4", mountpoint="/"))
    return DiskPlan(device_path=_CALAMARES_DEVICE, firmware=firmware,
                    partitions=tuple(parts), mode="existing")


def swapfile_steps(target: str, size_mib: int, root_fstype: str = "ext4") -> List[Step]:
    """Create <target>/swapfile without touching fstab or swapon: the Calamares
    fstab job runs later and the glueinstall module appends
    swapfile_fstab_line() itself (AdapterResult.swapfile_path says where).
    btrfs: the file must be empty and NOCOW before it is allocated."""
    if size_mib <= 0:
        return []
    f = f"{target.rstrip('/')}{SWAPFILE}"
    steps: List[Step] = [RunCommand(argv=["truncate", "-s", "0", f],
                                    description="Create an empty swapfile")]
    if root_fstype == "btrfs":
        steps.append(RunCommand(argv=["chattr", "+C", f],
                                description="Disable copy-on-write on the swapfile (btrfs)"))
    steps += [
        RunCommand(argv=["fallocate", "-l", f"{size_mib}M", f],
                   description=f"Allocate {size_mib} MiB swapfile"),
        RunCommand(argv=["chmod", "600", f], description="Restrict swapfile permissions"),
        RunCommand(argv=["mkswap", f], description="Format swapfile"),
    ]
    return steps


def swapfile_fstab_line() -> str:
    """fstab line for the swapfile (root-relative path)."""
    return f"{SWAPFILE} none swap defaults 0 0\n"


def _no_forbidden(steps: List[Step]) -> None:
    """Defensive self-check: Calamares owns disks, fstab, users and locale."""
    for step in steps:
        for arg in getattr(step, "argv", ()):
            m = _FORBIDDEN_RE.search(arg)
            if m:
                raise AdapterError(
                    f"adaptor step would run {m.group(1)!r} (owned by Calamares): "
                    f"{step.description}")


def compile_adapter(
    selection: Selection, catalog, *, root_mount: str, ram_bytes: int,
    is_laptop: bool, firmware: str, is_mount: Optional[Callable[[str], bool]] = None,
    swap_partition: Optional[str] = None, boot_spec: Optional[BootSpec] = None,
    root_device: Optional[str] = None, boot_device: Optional[str] = None,
    root_bytes: int = 0, root_fstype: str = "ext4", pacman_conf: Optional[str] = None,
    gpu_vendors=None, cpu_v3: bool = False, cpu_vendor_id: str = "other",
    amd_pstate_active: bool = False, other_os: Optional[DetectResult] = None,
) -> AdapterResult:
    """Compile the Glue part of a Calamares install (pure).

    Order: keyring_steps (host) → bootstrap_steps (basestrap) → config_steps
    (plan files, skel, services, only_if_present=False) → swapfile_steps
    (swap_mode auto, no swap partition from Calamares, root size known) →
    bootloader_steps LAST (boot.d fragments from other_os are plan files;
    glue-boot-update --deploy). Never: fstabgen, mkfs, parted/sgdisk/wipefs,
    mount/umount, useradd/passwd/chpasswd, locale-gen, hostnamectl.
    swap_partition: a swap partition Calamares created (resume= when
    hibernate is on; no swapfile). root_bytes: size of the root filesystem,
    0 = unknown → no swapfile. boot_spec: overrides the kernel/cmdline
    derived from the catalog + plan. other_os: osdetect result from the
    live side (main.py runs os-prober); None = no foreign entries.
    Raises AdapterError (also for PlanError) — messages carry no secrets.
    """
    t = check_root_mount(root_mount, is_mount)
    if firmware not in FIRMWARES:
        raise AdapterError(f"firmware must be one of {list(FIRMWARES)}, got {firmware!r}")
    if selection.init_id not in INITS:
        raise AdapterError(f"init must be one of {list(INITS)}, got {selection.init_id!r}")
    root_device = _check_device(root_device, "root device")
    boot_device = _check_device(boot_device, "boot device")
    swap_partition = _check_device(swap_partition, "swap partition")
    warnings: List[str] = []

    swap: Optional[SwapPlan] = None
    if ram_bytes > 0:
        mode = selection.swap_mode
        want_resume = selection.hibernate and swap_partition is not None
        if mode == "auto" and swap_partition is not None:
            mode = "zram"   # Calamares already made the disk swap
        if selection.hibernate and swap_partition is None:
            warnings.append("Hibernation disabled: it needs a swap partition "
                            "(a swapfile cannot be resumed from)")
        swap = swap_plan(ram_bytes, max(0, root_bytes), False, mode)
        if mode == "zram" and selection.swap_mode == "auto":
            swap.hibernate = want_resume
        warnings.extend(swap.warnings)
    else:
        want_resume = False

    try:
        plan = resolve_plan(
            catalog, selection, gpu_vendors=gpu_vendors, cpu_v3=cpu_v3,
            is_laptop=is_laptop, cpu_vendor_id=cpu_vendor_id,
            amd_pstate_active=amd_pstate_active, swap=swap,
            ram_bytes=ram_bytes or None)
    except PlanError as exc:
        raise AdapterError(f"plan error: {exc}") from exc
    warnings.extend(plan.warnings)

    disk_plan = _disk_plan(firmware, root_device, boot_device, swap_partition)
    if other_os is not None:
        for name, text in boot_d_files(other_os):
            plan.files.append(PlannedFile(f"{BOOT_D}/{name}", text, 0o644))
        plan.files.sort(key=lambda f: f.path)
    if boot_spec is None:
        kernel = next(k for k in catalog.kernels if k.id == selection.kernel_id)
        boot_spec = BootSpec(kernel=kernel_name(kernel.packages),
                             cmdline_extra=tuple(plan.cmdline_extra),
                             resume=want_resume)

    packages = sorted(set(plan.packages) | set(bootloader_packages(disk_plan)))
    steps: List[Step] = []
    steps.extend(keyring_steps())
    steps.extend(bootstrap_steps(plan, target=t, pacman_conf=pacman_conf,
                                 extra_packages=bootloader_packages(disk_plan)))
    steps.extend(config_steps(plan, target=t, init_id=selection.init_id,
                              only_if_present=False))
    swapfile_path = None
    if swap is not None and swap.disk_mib > 0 and swap_partition is None:
        steps.extend(swapfile_steps(t, swap.disk_mib, root_fstype))
        swapfile_path = f"{t}{SWAPFILE}"
    steps.extend(bootloader_steps(disk_plan, boot_spec, target=t))
    _no_forbidden(steps)
    return AdapterResult(steps=steps, swapfile_path=swapfile_path,
                         warnings=warnings, packages=packages)


def adapter_steps(selection: Selection, catalog, *, root_mount: str, ram_bytes: int,
                  is_laptop: bool, firmware: str, **kwargs) -> List[Step]:
    """Steps only (see compile_adapter for the keyword arguments)."""
    return compile_adapter(selection, catalog, root_mount=root_mount,
                           ram_bytes=ram_bytes, is_laptop=is_laptop,
                           firmware=firmware, **kwargs).steps
