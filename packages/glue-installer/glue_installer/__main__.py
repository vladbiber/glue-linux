"""
Entry point: python -m glue_installer

Wires catalog -> wizard (curses TUI) -> resolve_plan -> compile_steps ->
execute. Headless flags (--validate-catalog, --help) never touch curses.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

from glue_installer.catalog import CatalogError, load_catalog
from glue_installer.disk_swap import (
    plan_disk_with_swap, plan_existing_with_swap, read_ram_bytes, resolve_disk,
    staged_pacman_conf,
)
from glue_installer.clone import CLONE_INIT, CLONE_KERNEL, CloneError, clone_steps
from glue_installer.disks import (
    DiskError, discover, discover_partitions,
    plan_disk, plan_existing_partition,
)
from glue_installer.executor import (
    ExecutorError, compile_steps, execute, keyring_steps,
)
from glue_installer.identity import IdentityError, IdentitySpec, identity_steps
from glue_installer.limine import BootSpec, kernel_name, resume_wanted
from glue_installer.osdetect import (
    BOOT_D, boot_d_files, detect_other_os, summary_lines,
)
from glue_installer.plan_types import PlannedFile
from glue_installer.plan import PlanError, Selection, resolve_plan
from glue_installer.swap import swap_plan
from glue_installer.preview import make_show_screenshot
from glue_installer.run_ui import (  # noqa: F401 (re-exported host helpers)
    _INSTALL_LOG, _TZ_ENDPOINTS, _capture, _firmware, _net_check,
    _open_net_tool, _repartition, autodetect_spec_timezone, detect_firmware,
    detect_timezone, make_progress, print_log_tail, prompt_reboot, sync_clock,
)

_REPO_CATALOG = Path(__file__).resolve().parent.parent / "catalog" / "catalog.json"

EXIT_OK = 0


def find_catalog(*, base_prefix: str = "/") -> Path:
    """Locate catalog.json using search order.

    1. $GLUE_CATALOG env var (explicit override)
    2. {base_prefix}/usr/share/glue-installer/catalog/catalog.json (packaged)
    3. Repo-relative path (dev checkout fallback)
    """
    env_path = os.environ.get("GLUE_CATALOG")
    if env_path:
        return Path(env_path)
    # No rstrip("/"): Path("") would make the candidate relative to the CWD
    # (the packaged catalog was never found on the live ISO that way).
    packaged = (
        Path(base_prefix) / "usr/share/glue-installer/catalog/catalog.json"
    )
    if packaged.exists():
        return packaged
    return _REPO_CATALOG
EXIT_CANCELLED = 1
EXIT_CATALOG = 2
EXIT_INSTALL = 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="glue-installer",
        description="Glue Linux installer — compose your own system.",
    )
    parser.add_argument("--catalog", type=Path, default=None, metavar="PATH",
                        help="path to catalog.json (default: search the installed and source locations)")
    parser.add_argument("--target", default="/mnt", metavar="PATH",
                        help="mount point of the target system (default: /mnt)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the install steps instead of running them")
    parser.add_argument("--validate-catalog", action="store_true",
                        help="headless: load and validate the catalog, print counts, and exit")
    parser.add_argument(
        "--disk", metavar="PATH", default=None,
        help="disk to partition/format/mount (e.g. /dev/sda); "
             "WILL BE ERASED. Omit to install to an already-prepared --target",
    )
    parser.add_argument(
        "--firmware", choices=("auto", "uefi", "bios"), default="auto",
        help="boot firmware type (default: auto = detect /sys/firmware/efi)",
    )
    # Identity flags
    parser.add_argument("--hostname", default="glue", metavar="NAME",
                        help="system hostname (default: glue)")
    parser.add_argument("--username", default=None, metavar="NAME",
                        help="primary user account to create (skipped if omitted)")
    parser.add_argument("--password", default=None, metavar="SECRET",
                        help="password for the new user and root (only meaningful with --username)")
    parser.add_argument("--locale", default="en_US.UTF-8", metavar="LOCALE",
                        help="system locale in ll_CC.UTF-8 form (default: en_US.UTF-8)")
    parser.add_argument("--timezone", default="UTC", metavar="TZ",
                        help="system timezone in Area/City form (default: UTC)")
    parser.add_argument(
        "--offline", action="store_true",
        help="headless/dry-run: offline install = clone the live system "
             "(stock linux kernel, runit) instead of basestrap",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="skip the interactive TUI and use a minimal default selection "
             "(useful for scripted/dry-run use)",
    )
    return parser


def _headless_selection(catalog, offline: bool = False) -> Selection:
    """Build a minimal Selection from catalog defaults without running the TUI.
    offline: the clone keeps the live init (runit); kernel stays the catalog
    default for plan resolution, the clone itself boots CLONE_KERNEL."""
    kernel_id = catalog.kernels[0].id if catalog.kernels else "linux-cachyos"
    init_id = next(
        (i.id for i in catalog.inits if "dinit" in i.id),
        catalog.inits[0].id if catalog.inits else "dinit",
    )
    if offline:
        init_id = CLONE_INIT
    return Selection(
        offline=offline,
        kernel_id=kernel_id,
        init_id=init_id,
        session_ids=[],
        shell_choice={},
        support_ids=[],
        gaming=False,
        minimal=True,
    )


def _fail(message: str, code: int) -> int:
    print(message, file=sys.stderr)
    return code


# -- root auto-elevation ------------------------------------------------------

def build_elevation_argv(argv, *, which, executable) -> list:
    """Argv to re-exec this installer under sudo (pure; testable).

    Prefers the packaged /usr/bin/glue-install launcher (it restores the
    PYTHONPATH that sudo's env_reset strips); falls back to `sudo <python> -m
    glue_installer` for dev checkouts run from the repo root.
    """
    launcher = which("glue-install")
    if launcher:
        return ["sudo", launcher] + list(argv)
    return ["sudo", executable, "-m", "glue_installer"] + list(argv)


def _ensure_root(argv) -> None:
    """Re-exec under sudo when a REAL install could follow (never for
    --dry-run / --validate-catalog). Everything disk-touching (sgdisk, mkfs,
    basestrap, chroot) needs root - failing at step 1 of the install after
    the whole wizard was filled in is the worst possible moment."""
    import shutil
    if os.geteuid() == 0:
        return
    sudo_argv = build_elevation_argv(
        argv, which=shutil.which, executable=sys.executable,
    )
    print("Installer needs root — re-running with sudo...")
    try:
        os.execvp(sudo_argv[0], sudo_argv)
    except OSError:
        sys.exit("This installer must run as root: sudo glue-install")


def _print_summary(plan, os_lines=(), offline: bool = False) -> None:
    if offline:
        print(f"Offline install: clone of the live system (kernel {CLONE_KERNEL}, "
              f"{CLONE_INIT}), {len(plan.services)} services, "
              f"{len(plan.files)} files to write.")
    else:
        print(
            f"Install plan: {len(plan.packages)} packages, "
            f"{len(plan.services)} services, {len(plan.files)} files to write."
        )
    for line in os_lines:
        print(line)
    for warning in plan.warnings:
        print(f"note: {warning}")


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    # Root is mandatory for a real install; elevate BEFORE the wizard.
    if not args.dry_run and not args.validate_catalog:
        _ensure_root(argv if argv is not None else sys.argv[1:])

    if args.catalog is None:
        args.catalog = find_catalog()

    try:
        catalog = load_catalog(args.catalog)
    except CatalogError as exc:
        return _fail(f"Catalog error: {exc}", EXIT_CATALOG)

    if args.validate_catalog:
        print(
            f"Catalog OK: {len(catalog.kernels)} kernels, "
            f"{len(catalog.inits)} inits, {len(catalog.sessions)} sessions, "
            f"{len(catalog.shells)} shells"
        )
        return EXIT_OK

    # GeoIP timezone when none was passed: prefilled, still editable.
    if args.timezone == "UTC" and not args.dry_run:
        detected_tz = detect_timezone()
        if detected_tz:
            args.timezone = detected_tz

    wizard_result = None
    if args.headless:
        selection = _headless_selection(catalog, offline=args.offline)
    else:
        # Imported lazily so headless modes work even where curses is unusable.
        from glue_installer.tui import run_tui

        tui_disks = None
        tui_partitions = None
        if args.disk is None:
            try:
                found = discover()
                tui_disks = found or None
                tui_partitions = discover_partitions()
            except DiskError:
                tui_disks = None  # no disk screen; install to prepared --target
            else:
                if not found:  # discovery worked but saw no disk: say why
                    from glue_installer.hw_compat import detect_hardware
                    from glue_installer.run_ui import report_no_disks
                    report_no_disks(detect_hardware())
        try:
            from glue_installer.cpu import detect_cpu_v3 as _detect_cpu_v3
            from glue_installer.laptop import detect_laptop as _detect_laptop
            wizard_result = run_tui(
                catalog,
                disks=tui_disks,
                partitions=tui_partitions,
                ask_identity=args.username is None,
                identity_defaults={
                    "hostname": args.hostname,
                    "locale": args.locale,
                    "timezone": args.timezone,
                },
                net_check=_net_check,
                open_net_tool=_open_net_tool,
                repartition=_repartition,
                # runs as soon as the network screen confirms connectivity
                # (installs require the network), so the identity form shows
                # the real local timezone instead of the UTC placeholder
                detect_timezone=detect_timezone,
                cpu_v3=_detect_cpu_v3(),
                is_laptop=_detect_laptop(),
                show_screenshot=make_show_screenshot(args.catalog.parent),
            )
        except KeyboardInterrupt:
            wizard_result = None
        if wizard_result is None:
            print("Cancelled.")
            return EXIT_CANCELLED
        selection = wizard_result.selection
        if args.disk is None:
            args.disk = wizard_result.device_path

    disk_mode = wizard_result.disk_mode if wizard_result is not None else "erase"
    ram_bytes = read_ram_bytes()
    swap = None
    disk_plan = None
    if disk_mode != "erase" and wizard_result is not None \
            and wizard_result.partition_path is not None:
        # Install INTO an existing partition (chosen or made via cfdisk):
        # only that partition is formatted; the table stays untouched.
        firmware = _firmware(args)
        try:
            parts = discover_partitions()
            root = next(
                (p for p in parts if p.path == wizard_result.partition_path),
                None,
            )
            if root is None:
                raise DiskError(
                    f"Partition {wizard_result.partition_path} no longer exists"
                )
            disk_plan = plan_existing_partition(root, firmware, parts)
            if ram_bytes > 0:
                swap = swap_plan(ram_bytes, root.size_bytes, selection.hibernate,
                                 selection.swap_mode)
                disk_plan = plan_existing_with_swap(disk_plan, swap)
        except DiskError as exc:
            return _fail(f"Disk error: {exc}", EXIT_INSTALL)
    elif args.disk is not None:
        firmware = _firmware(args)
        try:
            device = resolve_disk(args.disk, dry_run=args.dry_run)
            if ram_bytes > 0:
                swap = swap_plan(ram_bytes, device.size_bytes,
                                 selection.hibernate, selection.swap_mode)
                disk_plan = plan_disk_with_swap(
                    device, firmware, swap,
                    swap_uuid=str(uuid.uuid4()) if swap.hibernate else "")
            else:
                disk_plan = plan_disk(device, firmware)
        except DiskError as exc:
            return _fail(f"Disk error: {exc}", EXIT_INSTALL)

    try:
        from glue_installer.cpu import detect_cpu_v3
        from glue_installer.gpu import detect_gpu_vendors
        from glue_installer.hw_compat import detect_hardware
        from glue_installer.laptop import (
            detect_laptop, detect_cpu_vendor, detect_amd_pstate_active,
        )
        _cpu_v3 = detect_cpu_v3()
        plan = resolve_plan(
            catalog, selection, gpu_vendors=detect_gpu_vendors(),
            cpu_v3=_cpu_v3,
            is_laptop=detect_laptop(),
            cpu_vendor_id=detect_cpu_vendor(),
            amd_pstate_active=detect_amd_pstate_active(),
            swap=swap, ram_bytes=ram_bytes or None, hw=detect_hardware(),
        )
    except PlanError as exc:
        return _fail(f"Plan error: {exc}", EXIT_INSTALL)
    if swap is not None:
        plan.warnings.extend(swap.warnings)
    os_lines = []
    if disk_plan is not None:
        plan.warnings.extend(disk_plan.warnings)
        # Other OSes -> /etc/glue/boot.d/*.conf, written with the plan files
        # (before the bootloader step; foreign PARTUUIDs exist already).
        detected, os_warnings = detect_other_os(
            disk_plan, env=os.environ, capture=_capture)
        plan.warnings.extend(os_warnings)
        for name, text in boot_d_files(detected):
            plan.files.append(PlannedFile(f"{BOOT_D}/{name}", text, 0o644))
        plan.files.sort(key=lambda f: f.path)
        os_lines = summary_lines(detected)

    # Resolve pacman.conf path: env override (for tests) or installed location.
    pacman_conf_path = os.environ.get("GLUE_PACMAN_CONF")
    if pacman_conf_path is None and os.path.exists("/usr/share/glue/pacman.conf"):
        pacman_conf_path = "/usr/share/glue/pacman.conf"
    # When v3 is active patch the staged conf so basestrap can resolve [cachyos-v3].
    pacman_conf_path = staged_pacman_conf(
        pacman_conf_path, _cpu_v3, selection.kernel_id)

    # Limine entry: the selected kernel, plan extras, resume= only for a new
    # swap partition (erase mode) when hibernation was requested.
    kernel = next(k for k in catalog.kernels if k.id == selection.kernel_id)
    boot = BootSpec(
        kernel=kernel_name(kernel.packages),
        cmdline_extra=tuple(plan.cmdline_extra),
        resume=resume_wanted(disk_plan, selection.hibernate),
    )
    try:
        if selection.offline:
            # Offline clone: rsync of the live system, no basestrap and
            # no host keyring; boots the live kernel through glue-boot-update.
            if disk_plan is None:
                return _fail("Offline install needs a disk or partition to "
                             "clone onto (--disk).", EXIT_INSTALL)
            boot = BootSpec(kernel=CLONE_KERNEL, cmdline_extra=boot.cmdline_extra,
                            resume=boot.resume)
            steps = clone_steps(plan, disk_plan, boot=boot, target=args.target)
        else:
            steps = compile_steps(
                plan, target=args.target, init_id=selection.init_id,
                disk_plan=disk_plan, pacman_conf=pacman_conf_path, boot=boot,
            )
            # Host keyring prep first: basestrap verifies [cachyos] against
            # the HOST keyring (executor.keyring_steps).
            steps = keyring_steps() + steps
    except (ExecutorError, CloneError) as exc:
        return _fail(f"Executor error: {exc}", EXIT_INSTALL)

    # Append identity steps: wizard-collected spec wins, else --username flags
    spec = wizard_result.identity if wizard_result is not None else None
    if spec is None and args.username is not None:
        password = args.password or ""
        try:
            spec = IdentitySpec(
                hostname=args.hostname,
                username=args.username,
                password=password,
                locale=args.locale,
                timezone=args.timezone,
            )
        except IdentityError as exc:
            return _fail(f"Identity error: {exc}", EXIT_INSTALL)
    # Timezone still "UTC" (GeoIP offline before the wizard)? Retry now,
    # the network screen usually just brought Wi-Fi up.
    if not args.dry_run:
        retried = autodetect_spec_timezone(spec)
        if retried is not spec and retried is not None:
            print(f"Timezone auto-detected: {retried.timezone}")
            spec = retried
    if spec is not None:
        steps = steps + identity_steps(spec, target=args.target)

    _print_summary(plan, os_lines, selection.offline)

    if args.dry_run:
        execute(steps, dry_run=True)
        return EXIT_OK

    # Last line of defense (normally unreachable: _ensure_root re-execs at
    # startup) - never start real steps without root.
    if os.geteuid() != 0:
        return _fail(
            "Installer is not running as root — the install cannot proceed. "
            "Run it as: sudo glue-install", EXIT_INSTALL,
        )

    if disk_plan is not None and disk_plan.mode == "existing":
        root_part = next(p for p in disk_plan.partitions if p.mountpoint == "/")
        print(f"About to FORMAT {root_part.path} (rest of the disk untouched) "
              f"and install to {args.target}.")
    elif disk_plan is not None:
        print(f"About to ERASE {disk_plan.device_path} and install to {args.target}.")
    else:
        print(f"About to install to {args.target}. This will modify the target system.")
    try:
        answer = input("Type 'yes' to begin the installation: ")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer.strip() != "yes":
        print("Cancelled.")
        return EXIT_CANCELLED

    # Fix the live clock from the network BEFORE identity_steps runs
    # `hwclock --systohc`, or a wrong RTC time gets burned in for good.
    corrected = sync_clock()
    if corrected is not None:
        print(f"Clock was off by {corrected:+d}s — synced from the network.")

    # Console shows only a progress bar; subprocess output goes to the log.
    try:
        with open(_INSTALL_LOG, "w", encoding="utf-8") as fh:
            fh.write("glue-install log\n")
    except OSError:
        pass
    try:
        execute(
            steps, dry_run=False,
            progress=make_progress(steps), output_path=_INSTALL_LOG,
        )
    except ExecutorError as exc:
        print()
        print_log_tail(_INSTALL_LOG)
        return _fail(f"Install failed: {exc}", EXIT_INSTALL)

    print(f"\nInstall complete. Full log: {_INSTALL_LOG}")
    prompt_reboot()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
