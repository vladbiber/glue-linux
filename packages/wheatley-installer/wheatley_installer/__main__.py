"""
Entry point: python -m wheatley_installer

Wires catalog -> wizard (curses TUI) -> resolve_plan -> compile_steps ->
execute. Headless flags (--validate-catalog, --help) never touch curses.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wheatley_installer.catalog import CatalogError, load_catalog
from wheatley_installer.executor import ExecutorError, compile_steps, execute
from wheatley_installer.plan import PlanError, resolve_plan

_DEFAULT_CATALOG = Path(__file__).resolve().parent.parent / "catalog" / "catalog.json"

EXIT_OK = 0
EXIT_CANCELLED = 1
EXIT_CATALOG = 2
EXIT_INSTALL = 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wheatley-installer",
        description="Wheatley Linux installer — compose your own system.",
    )
    parser.add_argument(
        "--catalog", type=Path, default=_DEFAULT_CATALOG, metavar="PATH",
        help=f"path to catalog.json (default: {_DEFAULT_CATALOG})",
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
    return parser


def _fail(message: str, code: int) -> int:
    print(message, file=sys.stderr)
    return code


def _print_summary(plan) -> None:
    print(
        f"Install plan: {len(plan.packages)} packages, "
        f"{len(plan.services)} services, {len(plan.files)} files to write."
    )
    for warning in plan.warnings:
        print(f"note: {warning}")


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

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

    # Imported lazily so headless modes work even where curses is unusable.
    from wheatley_installer.tui import run_tui

    try:
        selection = run_tui(catalog)
    except KeyboardInterrupt:
        selection = None
    if selection is None:
        print("Cancelled.")
        return EXIT_CANCELLED

    try:
        plan = resolve_plan(catalog, selection)
    except PlanError as exc:
        return _fail(f"Plan error: {exc}", EXIT_INSTALL)

    try:
        steps = compile_steps(plan, target=args.target, init_id=selection.init_id)
    except ExecutorError as exc:
        return _fail(f"Executor error: {exc}", EXIT_INSTALL)

    _print_summary(plan)

    if args.dry_run:
        execute(steps, dry_run=True)
        return EXIT_OK

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
