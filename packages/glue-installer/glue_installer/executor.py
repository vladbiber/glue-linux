"""
Executor layer for Glue Linux installer.

ADR-4: pure step compiler (compile_steps) + thin runner (execute).
No subprocess or filesystem access at import time or inside compile_steps.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, List, Optional, Union

from glue_installer.plan import InstallPlan

if TYPE_CHECKING:  # pragma: no cover - limine imports Step from this module
    from glue_installer.limine import BootSpec


class ExecutorError(Exception):
    """Raised when a step fails. Message includes the failing step's description."""


# ---------------------------------------------------------------------------
# Step types
# ---------------------------------------------------------------------------

@dataclass
class RunCommand:
    argv: List[str]
    description: str
    stdin: Optional[str] = field(default=None)


@dataclass
class WriteTargetFile:
    # Full absolute host path to write, e.g. /mnt/etc/skel/.bashrc
    path: str
    content: str
    mode: int
    description: str


Step = Union[RunCommand, WriteTargetFile]

_SUPPORTED_INITS = frozenset({"dinit", "runit", "openrc"})

# A few services are named differently per init than our canonical plan name
# (verified against the Artix service-script packages: bluez-openrc ships
# /etc/init.d/bluetooth; openntpd-dinit ships /etc/dinit.d/ntpd and
# openntpd-openrc ships /etc/init.d/ntpd, while openntpd-runit keeps
# /etc/runit/sv/openntpd).
_INIT_SERVICE_NAMES = {
    "dinit": {"openntpd": "ntpd"},
    "runit": {},
    "openrc": {"bluetoothd": "bluetooth", "openntpd": "ntpd"},
}


# ---------------------------------------------------------------------------
# Pure compiler
# ---------------------------------------------------------------------------

def keyring_steps() -> List[Step]:
    """Host-side pacman keyring preparation; must run BEFORE basestrap.

    The live ISO ships the cachyos-keyring PACKAGE, but installing it only
    drops the keys under /usr/share/pacman/keyrings — nothing imports them
    into /etc/pacman.d/gnupg. basestrap verifies the [cachyos] database
    signature with the HOST keyring, so without this populate step every
    online install fails with:
        error: cachyos: key "882DCFE4...DB35A47" is unknown
    Both commands are idempotent; --init is a no-op on an initialized keyring.
    """
    return [
        RunCommand(
            argv=["pacman-key", "--init"],
            description="Initialize host pacman keyring",
        ),
        RunCommand(
            argv=["pacman-key", "--populate", "artix", "cachyos"],
            description="Populate host pacman keyring (artix + cachyos)",
        ),
    ]


def bootstrap_steps(
    plan: InstallPlan, *, target: str = "/mnt",
    pacman_conf: Optional[str] = None,
    extra_packages: Optional[List[str]] = None,
) -> List[Step]:
    """The Glue "pacstrap": ONE basestrap of plan.packages into target (pure).

    No disk steps, no fstabgen: compile_steps wraps this between the disk
    preparation and the fstab step; the Calamares adaptor (10.3,
    calamares_adapter.py) calls it on the root Calamares already mounted.
    pacman_conf: staged live pacman.conf for `basestrap -C` (None: default).
    extra_packages: merged (sorted, deduplicated) into the set, e.g. the
    bootloader packages; None keeps plan.packages exactly as given.
    """
    t = target.rstrip("/")
    packages = list(plan.packages)
    if extra_packages is not None:
        packages = sorted(set(packages) | set(extra_packages))
    n = len(packages)
    basestrap_argv = ["basestrap"]
    if pacman_conf is not None:
        basestrap_argv += ["-C", pacman_conf]
    # a reused EFI partition keeps the old system's /boot files (microcode,
    # kernels): pacman must replace them instead of aborting on a conflict;
    # with -r (basestrap) the glob is matched without the leading slash
    basestrap_argv += [t] + packages + ["--overwrite", "boot/*"]
    return [RunCommand(
        argv=basestrap_argv,
        description=f"Install {n} package{'s' if n != 1 else ''} with basestrap",
    )]


def compile_steps(
    plan: InstallPlan, *, target: str = "/mnt", init_id: str, disk_plan=None,
    pacman_conf: Optional[str] = None, boot: "Optional[BootSpec]" = None,
) -> List[Step]:
    """Compile an InstallPlan into a deterministic ordered list of Steps.

    Pure function: same inputs always produce identical output; no I/O or
    subprocess access at any point.
    Raises ExecutorError for unsupported init_id.

    disk_plan (a disks.DiskPlan, optional): when provided, disk-preparation
    steps (partition, mkfs, mount) come FIRST, Limine bootloader steps come
    LAST, and limine (+ efibootmgr on UEFI) is added to the basestrap set.
    Swap (mkswap/swapon) is enabled right after the mounts, BEFORE basestrap
    and fstabgen, so that fstabgen lists it in the new fstab.
    boot (limine.BootSpec, optional): kernel/cmdline/resume for limine.conf;
    None means the default linux-cachyos kernel without extras.
    """
    if init_id not in _SUPPORTED_INITS:
        raise ExecutorError(
            f"Unsupported init_id: '{init_id}'; supported: {sorted(_SUPPORTED_INITS)}"
        )

    # Deferred import: disks.py/limine.py import Step types from this module,
    # so a top-level import here would be circular.
    if disk_plan is not None:
        from glue_installer.disks import disk_steps
        from glue_installer.limine import (
            BootSpec, bootloader_packages, bootloader_steps,
        )
        if boot is None:
            boot = BootSpec(kernel="linux-cachyos")

    t = target.rstrip("/")
    steps: List[Step] = []

    # 0. Disk preparation (only when a DiskPlan is provided)
    extra_packages = None
    if disk_plan is not None:
        from glue_installer.disk_swap import swap_steps
        steps.extend(disk_steps(disk_plan, target=target))
        steps.extend(swap_steps(disk_plan, target=target))
        extra_packages = bootloader_packages(disk_plan)

    # 1. Install all packages in a single basestrap call
    steps.extend(bootstrap_steps(plan, target=target, pacman_conf=pacman_conf,
                                 extra_packages=extra_packages))

    # 2. Generate fstab (stdout redirected via shell)
    steps.append(RunCommand(
        argv=["sh", "-c", f"fstabgen -U {t} >> {t}/etc/fstab"],
        description=f"Generate fstab → {t}/etc/fstab",
    ))
    if disk_plan is not None:
        from glue_installer.disk_swap import swapfile_fstab_steps
        steps.extend(swapfile_fstab_steps(disk_plan, target=target))

    steps.extend(config_steps(plan, target=target, init_id=init_id))

    # 5b. Early CPU microcode needs the `microcode` hook (roadmap 10.8 b)
    from glue_installer.hw_compat import microcode_hook_steps
    steps.extend(microcode_hook_steps(plan.packages, t, plan.files))

    # 6. Bootloader (only when a DiskPlan is provided) — always last
    if disk_plan is not None:
        steps.extend(bootloader_steps(disk_plan, boot, target=target))

    return steps


_SERVICE_LINKS = {  # init -> (service script dir, enabled-link dir)
    "dinit": ("/etc/dinit.d", "/etc/dinit.d/boot.d"),
    "runit": ("/etc/runit/sv", "/etc/runit/runsvdir/default"),
    "openrc": ("/etc/init.d", "/etc/runlevels/default"),
}


def config_steps(
    plan: InstallPlan, *, target: str = "/mnt", init_id: str,
    only_if_present: bool = False,
) -> List[Step]:
    """Glue configuration of an already-populated target (pure).

    In order: WriteTargetFile for every plan.files entry, one enable step per
    plan.services entry (symlink per init), then the ModemManager D-Bus
    activation guard. compile_steps appends exactly these after fstab;
    clone.clone_steps reuses them on the rsync'd live system (roadmap 3.6)
    and the Calamares adaptor (10.3) will call them on its own target.

    only_if_present=True (clone): the service script may be missing on the
    target (nothing was basestrap'ed), so each enable becomes a guarded
    `sh -c '[ -e <src> ] && ln -sf <src> <dst> || true'` instead of a bare
    ln that would leave a dangling link.
    Raises ExecutorError for unsupported init_id.
    """
    if init_id not in _SUPPORTED_INITS:
        raise ExecutorError(
            f"Unsupported init_id: '{init_id}'; supported: {sorted(_SUPPORTED_INITS)}"
        )
    t = target.rstrip("/")
    steps: List[Step] = []

    # 3. Write each planned file (plan.files already sorted by path)
    for pf in plan.files:
        steps.append(WriteTargetFile(
            path=t + pf.path,
            content=pf.content,
            mode=pf.mode,
            description=f"Write {pf.path}",
        ))

    # 4. Enable services — argv depends on init_id
    src_dir, dst_dir = _SERVICE_LINKS[init_id]
    for svc in plan.services:
        isvc = _INIT_SERVICE_NAMES[init_id].get(svc, svc)
        src, dst = f"{src_dir}/{isvc}", f"{dst_dir}/{isvc}"
        if only_if_present:
            argv = ["artix-chroot", t, "sh", "-c",
                    f"[ -e {src} ] && ln -sf {src} {dst} || true"]
            what = f"Enable service {svc} ({init_id}) if present"
        else:
            argv = ["artix-chroot", t, "ln", "-sf", src, dst]
            what = f"Enable service {svc} ({init_id})"
        steps.append(RunCommand(argv=argv, description=what))

    # 5. Neutralise ModemManager's D-Bus activation (ported from the proven
    # shell installer): NetworkManager D-Bus-activates it and its log spam
    # paints over the greeter's VT; stopping/killing it just re-spawns it.
    steps.append(RunCommand(
        argv=[
            "artix-chroot", t, "sh", "-c",
            'f=/usr/share/dbus-1/system-services/org.freedesktop.ModemManager1.service; '
            '[ -e "$f" ] && mv -f "$f" "$f.glue-disabled"; true',
        ],
        description="Disable ModemManager D-Bus activation (console spam)",
    ))
    return steps


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _validate_path(path: str) -> None:
    """Reject paths that are relative or contain '..' traversal components."""
    if not path.startswith("/"):
        raise ExecutorError(
            f"WriteTargetFile path must be absolute (start with '/'), got: '{path}'"
        )
    for part in path.split("/"):
        if part == "..":
            raise ExecutorError(
                f"WriteTargetFile path must not contain '..', got: '{path}'"
            )


def execute(
    steps: List[Step],
    *,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
    progress: Optional[Callable[[int, int, str], None]] = None,
    output_path: Optional[str] = None,
) -> None:
    """Execute steps in order.

    dry_run=True  — logs 'DRY-RUN: <description>' per step; zero subprocess
                    calls and zero filesystem writes.
    dry_run=False — runs each step; raises ExecutorError on first failure,
                    chaining the original exception and embedding the step
                    description in the message.
    progress      — optional callback(step_index, total, description), invoked
                    before each step (real runs only). Lets the UI draw a
                    progress bar instead of raw subprocess output.
    output_path   — optional log file path; when set, each RunCommand's
                    stdout/stderr is appended there instead of inheriting the
                    console (so a progress bar isn't painted over).
    """
    total = len(steps)
    out_fh = None
    if output_path is not None and not dry_run:
        out_fh = open(output_path, "a", encoding="utf-8", errors="replace")
    try:
        for i, step in enumerate(steps):
            if dry_run:
                log(f"DRY-RUN: {step.description}")
                continue
            if progress is not None:
                progress(i, total, step.description)
            _run_step(step, out_fh)
        if progress is not None and not dry_run:
            progress(total, total, "Done")
    finally:
        if out_fh is not None:
            out_fh.close()


def _run_step(step: Step, out_fh) -> None:
    """Run a single step (real run). Raises ExecutorError on failure."""
    if isinstance(step, RunCommand):
        kwargs = {}
        if step.stdin is not None:
            kwargs["input"] = step.stdin
            kwargs["text"] = True
        if out_fh is not None:
            out_fh.write(f"\n=== {step.description} ===\n")
            out_fh.flush()
            kwargs["stdout"] = out_fh
            kwargs["stderr"] = subprocess.STDOUT
        try:
            result = subprocess.run(step.argv, **kwargs)
        except OSError as exc:
            # e.g. FileNotFoundError for a missing binary — fail with the
            # step description instead of an unhandled traceback
            raise ExecutorError(
                f"Command could not start ({exc}): {step.description}"
            ) from exc
        if result.returncode != 0:
            raise ExecutorError(
                f"Command failed (exit {result.returncode}): {step.description}"
            )

    elif isinstance(step, WriteTargetFile):
        _validate_path(step.path)
        parent = os.path.dirname(step.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(step.path, "w", encoding="utf-8") as fh:
            fh.write(step.content)
        os.chmod(step.path, step.mode)

    else:
        raise ExecutorError(f"Unknown step type: {type(step).__name__}")
