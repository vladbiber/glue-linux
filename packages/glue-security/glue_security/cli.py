"""glue-security command line: list, status, level, enable, disable."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from . import actions
from .components import BY_ID, COMPONENTS, LEVELS, level_ids, level_of
from .target import BOOT_CONF, SecurityError, Target

ME = "glue-security"
CRYPT_TOKENS = ("cryptdevice=", "rd.luks", "luks.")


def component_states(t: Target) -> Dict[str, bool]:
    return {c.id: actions.is_on(t, c.id) for c in COMPONENTS}


def encrypted_root(t: Target) -> bool:
    boot = t.parse_kv(t.read(BOOT_CONF))
    if boot.get("GLUE_BOOT_CRYPT_UUID"):
        return True
    if any(tok.startswith(CRYPT_TOKENS) for tok in boot.get("GLUE_BOOT_CMDLINE", "").split()):
        return True
    for line in (t.read("etc/crypttab") or "").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            return True
    return t.live and _live_root_is_crypt()


def _live_root_is_crypt() -> bool:
    try:
        source = next(l.split()[0] for l in Path("/proc/self/mounts").read_text().splitlines()
                      if l.split()[1] == "/")
        name = Path(os.path.realpath(source)).name
    except (OSError, StopIteration, IndexError):
        return False
    return _dm_crypt(Path("/sys/class/block") / name)


def _dm_crypt(dev: Path, depth: int = 0) -> bool:
    try:
        if (dev / "dm/uuid").read_text().startswith("CRYPT-"):
            return True
    except OSError:
        pass
    slaves = dev / "slaves"
    return depth < 4 and slaves.is_dir() and any(
        _dm_crypt(s, depth + 1) for s in slaves.iterdir())


def save_state(t: Target, base: Optional[str] = None) -> Dict[str, bool]:
    """Record the level and the components that differ from its base."""
    states = component_states(t)
    enabled = {k for k, v in states.items() if v}
    state = {k: v for k, v in t.state().items()
             if k in ("kernel_prev", "kernel_hardened")}
    base = base or t.state().get("base", "")
    level = level_of(enabled)
    if level in LEVELS:
        base = level
    state["level"] = level
    if base in LEVELS:
        state["base"] = base
        want = level_ids(base)
        for cid, on in states.items():
            if on != (cid in want):
                state[f"component.{cid}"] = "on" if on else "off"
    else:
        for cid, on in states.items():
            if on:
                state[f"component.{cid}"] = "on"
    t.save_state(state)
    return states


def finish(t: Target, changed: List[str]) -> None:
    if t.live and t.boot_dirty and t.exists(BOOT_CONF):
        if not t.live_run(["glue-boot-update"]):
            t.note("could not run glue-boot-update; run it before restarting")
    if any(BY_ID[c].needs_reboot for c in changed):
        t.note("restart the computer to finish")


def cmd_list(args) -> int:
    if args.json:
        print(json.dumps([c.as_dict() for c in COMPONENTS], indent=2))
        return 0
    for c in COMPONENTS:
        levels = ", ".join(c.levels) or "opt-in only"
        print(f"{c.id:16} {c.name}  [{levels}]{'  (restart)' if c.needs_reboot else ''}")
        print(f"{'':16} {c.description}")
        print(f"{'':16} Breaks: {c.breaks}")
    return 0


def cmd_status(args, t: Target) -> int:
    states = component_states(t)
    level = level_of(k for k, v in states.items() if v)
    crypt = encrypted_root(t)
    if args.json:
        print(json.dumps({"level": level, "components": states,
                          "encrypted_root": crypt}, indent=2))
        return 0
    print(f"Security level: {level}")
    print(f"Encrypted root: {'yes' if crypt else 'no'}")
    for c in COMPONENTS:
        print(f"  [{'x' if states[c.id] else ' '}] {c.id:16} {c.name}")
    return 0


def apply(t: Target, want: Dict[str, bool], force: bool, base: Optional[str]) -> int:
    """Turn components on or off; disables first, packages in one pacman run.
    While applying a level a safety refusal skips that component (warning)."""
    errors: List[str] = []
    warnings: List[str] = []
    changed: List[str] = []
    current = component_states(t)
    for c in reversed(COMPONENTS):
        if c.id in want and not want[c.id] and current[c.id]:
            try:
                actions.disable(t, c.id)
                changed.append(c.id)
            except (SecurityError, OSError) as e:
                errors.append(f"{c.id}: {e}")
    to_on = [c.id for c in COMPONENTS if want.get(c.id) and not current[c.id]]
    batch = [p for cid in to_on if cid != "kernel-hardened"
             for p in actions.packages(t, cid)]
    if batch:
        try:
            t.install(batch)
        except SecurityError as e:
            errors.append(f"packages: {e}")
            skipped = [cid for cid in to_on if cid != "kernel-hardened"
                       and actions.packages(t, cid)]
            errors += [f"{cid}: skipped, its packages are missing" for cid in skipped]
            to_on = [cid for cid in to_on if cid not in skipped]
    for cid in to_on:
        try:
            actions.enable(t, cid, force=force)
            changed.append(cid)
        except actions.Refused as e:
            (warnings if base else errors).append(f"{cid}: {e}")
        except (SecurityError, OSError) as e:
            errors.append(f"{cid}: {e}")
    finish(t, changed)
    states = save_state(t, base)
    for cid in changed:
        print(f"{ME}: {cid} {'on' if states[cid] else 'off'}")
    if not changed and not errors:
        print(f"{ME}: nothing to change")
    for n in t.notes:
        print(f"{ME}: {n}")
    for w in warnings:
        print(f"{ME}: warning: {w}, left off", file=sys.stderr)
    for e in errors:
        print(f"{ME}: error: {e}", file=sys.stderr)
    return 1 if errors else 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=ME, description="Glue Linux security levels")
    sub = p.add_subparsers(dest="cmd", required=True)

    def root_opt(sp):
        sp.add_argument("--root", metavar="DIR",
                        help="operate on the system mounted at DIR (files only)")

    sp = sub.add_parser("list", help="components and the levels that use them")
    sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("status", help="current level and components")
    sp.add_argument("--json", action="store_true")
    root_opt(sp)
    sp = sub.add_parser("level", help="apply basic, hardened or super")
    sp.add_argument("level", choices=LEVELS)
    sp.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    root_opt(sp)
    for name in ("enable", "disable"):
        sp = sub.add_parser(name, help=f"{name} one component")
        sp.add_argument("id", choices=[c.id for c in COMPONENTS], metavar="ID")
        if name == "enable":
            sp.add_argument("--force", action="store_true",
                            help="lockdown even with DKMS modules installed")
        root_opt(sp)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = parser().parse_args(argv)
    if args.cmd == "list":
        return cmd_list(args)
    root = getattr(args, "root", None)
    if root is not None and not Path(root).is_dir():
        print(f"{ME}: error: no such directory: {root}", file=sys.stderr)
        return 1
    t = Target(root)
    try:
        if args.cmd == "status":
            return cmd_status(args, t)
        if not t.outside and os.geteuid() != 0:
            print(f"{ME}: error: run it as root: sudo {ME} {args.cmd} ...", file=sys.stderr)
            return 1
        if args.cmd == "level":
            want = {c.id: c.id in level_ids(args.level) for c in COMPONENTS}
            return apply(t, want, args.force, args.level)
        return apply(t, {args.id: args.cmd == "enable"},
                     getattr(args, "force", False), None)
    except SecurityError as e:
        print(f"{ME}: error: {e}", file=sys.stderr)
        return 1
