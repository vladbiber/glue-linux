"""
Entry point: python -m wheatley_installer

Wires catalog -> wizard (curses TUI) -> resolve_plan -> compile_steps ->
execute. Headless flags (--validate-catalog, --help) never touch curses.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from wheatley_installer.catalog import CatalogError, load_catalog
from wheatley_installer.disks import (
    BlockDevice, DiskError, discover, discover_partitions,
    plan_disk, plan_existing_partition,
)
from wheatley_installer.executor import ExecutorError, compile_steps, execute
from wheatley_installer.identity import IdentityError, IdentitySpec, identity_steps
from wheatley_installer.plan import PlanError, Selection, resolve_plan

_REPO_CATALOG = Path(__file__).resolve().parent.parent / "catalog" / "catalog.json"

EXIT_OK = 0


def find_catalog(*, base_prefix: str = "/") -> Path:
    """Locate catalog.json using ADR-9 search order.

    1. $WHEATLEY_CATALOG env var (explicit override)
    2. {base_prefix}/usr/share/wheatley-installer/catalog/catalog.json (packaged)
    3. Repo-relative path (dev checkout fallback)
    """
    env_path = os.environ.get("WHEATLEY_CATALOG")
    if env_path:
        return Path(env_path)
    # NOTE: no rstrip("/") here — Path("/".rstrip("/")) is Path("") which makes
    # the candidate RELATIVE to the CWD, so the packaged catalog was never found
    # on the live ISO. Path() itself normalizes any trailing slash.
    packaged = (
        Path(base_prefix) / "usr/share/wheatley-installer/catalog/catalog.json"
    )
    if packaged.exists():
        return packaged
    return _REPO_CATALOG
EXIT_CANCELLED = 1
EXIT_CATALOG = 2
EXIT_INSTALL = 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wheatley-installer",
        description="Wheatley Linux installer — compose your own system.",
    )
    parser.add_argument(
        "--catalog", type=Path, default=None, metavar="PATH",
        help="path to catalog.json (default: auto-detected via ADR-9 search order)",
    )
    parser.add_argument(
        "--target", default="/mnt", metavar="PATH",
        help="mount point of the target system (default: /mnt)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the install steps instead of running them",
    )
    parser.add_argument(
        "--validate-catalog", action="store_true",
        help="headless: load and validate the catalog, print counts, and exit",
    )
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
    parser.add_argument(
        "--hostname", default="wheatley", metavar="NAME",
        help="system hostname (default: wheatley)",
    )
    parser.add_argument(
        "--username", default=None, metavar="NAME",
        help="primary user account to create (skipped if omitted)",
    )
    parser.add_argument(
        "--password", default=None, metavar="SECRET",
        help="password for the new user and root (only meaningful with --username)",
    )
    parser.add_argument(
        "--locale", default="en_US.UTF-8", metavar="LOCALE",
        help="system locale in ll_CC.UTF-8 form (default: en_US.UTF-8)",
    )
    parser.add_argument(
        "--timezone", default="UTC", metavar="TZ",
        help="system timezone in Area/City form (default: UTC)",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="skip the interactive TUI and use a minimal default selection "
             "(useful for scripted/dry-run use)",
    )
    return parser


def detect_firmware(efi_dir_exists: bool) -> str:
    """Pure helper: map /sys/firmware/efi presence to a firmware id."""
    return "uefi" if efi_dir_exists else "bios"


_DRY_RUN_DISK_BYTES = 32 * 1024 ** 3


def _resolve_disk(path: str, *, dry_run: bool) -> BlockDevice:
    """Find the BlockDevice for --disk. In dry-run, a path that is not a real
    block device (or a machine without lsblk) yields a synthetic 32 GiB disk
    so the step list can be previewed anywhere."""
    try:
        devices = discover()
    except DiskError:
        if not dry_run:
            raise
        devices = []
    for device in devices:
        if device.path == path:
            return device
    if dry_run:
        return BlockDevice(
            name=os.path.basename(path), path=path,
            size_bytes=_DRY_RUN_DISK_BYTES, model="dry-run synthetic disk",
            is_removable=False, has_mounted_partitions=False,
        )
    raise DiskError(f"No such disk: {path}")


def _headless_selection(catalog) -> Selection:
    """Build a minimal Selection from catalog defaults without running the TUI."""
    kernel_id = catalog.kernels[0].id if catalog.kernels else "linux-cachyos"
    init_id = next(
        (i.id for i in catalog.inits if "dinit" in i.id),
        catalog.inits[0].id if catalog.inits else "dinit",
    )
    return Selection(
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


# -- TUI I/O hooks (the tui module itself never touches subprocess) ----------

def _net_check() -> str:
    """Best-effort connectivity check for the network screen."""
    import subprocess
    try:
        ok = subprocess.run(
            ["ping", "-c", "1", "-W", "2", "artixlinux.org"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    return "connected" if ok else "OFFLINE"


def _open_net_tool() -> None:
    """Run nmtui full-screen (curses is suspended by the tui driver)."""
    import subprocess
    try:
        subprocess.call(["nmtui"])
    except OSError:
        pass  # nmtui missing: the notice already explains the situation


def _repartition(disk_path):
    """Run cfdisk on disk_path, then return the fresh partition list."""
    import subprocess
    try:
        subprocess.call(["cfdisk", disk_path])
    except OSError:
        pass
    try:
        return discover_partitions()
    except DiskError:
        return None


def _print_summary(plan) -> None:
    print(
        f"Install plan: {len(plan.packages)} packages, "
        f"{len(plan.services)} services, {len(plan.files)} files to write."
    )
    for warning in plan.warnings:
        print(f"note: {warning}")


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

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

    wizard_result = None
    if args.headless:
        selection = _headless_selection(catalog)
    else:
        # Imported lazily so headless modes work even where curses is unusable.
        from wheatley_installer.tui import run_tui

        tui_disks = None
        tui_partitions = None
        if args.disk is None:
            try:
                tui_disks = discover() or None
                tui_partitions = discover_partitions()
            except DiskError:
                tui_disks = None  # no disk screen; install to prepared --target
        try:
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
            )
        except KeyboardInterrupt:
            wizard_result = None
        if wizard_result is None:
            print("Cancelled.")
            return EXIT_CANCELLED
        selection = wizard_result.selection
        if args.disk is None:
            args.disk = wizard_result.device_path

    try:
        plan = resolve_plan(catalog, selection)
    except PlanError as exc:
        return _fail(f"Plan error: {exc}", EXIT_INSTALL)

    disk_mode = wizard_result.disk_mode if wizard_result is not None else "erase"
    disk_plan = None
    if disk_mode != "erase" and wizard_result is not None \
            and wizard_result.partition_path is not None:
        # Install INTO an existing partition (chosen or made via cfdisk):
        # only that partition is formatted; the table stays untouched.
        firmware = args.firmware
        if firmware == "auto":
            firmware = detect_firmware(os.path.exists("/sys/firmware/efi"))
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
        except DiskError as exc:
            return _fail(f"Disk error: {exc}", EXIT_INSTALL)
    elif args.disk is not None:
        firmware = args.firmware
        if firmware == "auto":
            firmware = detect_firmware(os.path.exists("/sys/firmware/efi"))
        try:
            device = _resolve_disk(args.disk, dry_run=args.dry_run)
            disk_plan = plan_disk(device, firmware)
        except DiskError as exc:
            return _fail(f"Disk error: {exc}", EXIT_INSTALL)

    # Resolve pacman.conf path: env override (for tests) or installed location.
    pacman_conf_path = os.environ.get("WHEATLEY_PACMAN_CONF")
    if pacman_conf_path is None and os.path.exists("/usr/share/wheatley/pacman.conf"):
        pacman_conf_path = "/usr/share/wheatley/pacman.conf"

    try:
        steps = compile_steps(
            plan, target=args.target, init_id=selection.init_id,
            disk_plan=disk_plan, pacman_conf=pacman_conf_path,
        )
    except ExecutorError as exc:
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
    if spec is not None:
        steps = steps + identity_steps(spec, target=args.target)

    _print_summary(plan)

    if args.dry_run:
        execute(steps, dry_run=True)
        return EXIT_OK

    if disk_plan is not None:
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

    try:
        execute(steps, dry_run=False)
    except ExecutorError as exc:
        return _fail(f"Install failed: {exc}", EXIT_INSTALL)

    print("Install complete.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
