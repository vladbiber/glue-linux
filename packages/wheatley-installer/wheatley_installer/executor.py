"""
Executor layer for Wheatley Linux installer.

ADR-4: pure step compiler (compile_steps) + thin runner (execute).
No subprocess or filesystem access at import time or inside compile_steps.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Union

from wheatley_installer.plan import InstallPlan


class ExecutorError(Exception):
    """Raised when a step fails. Message includes the failing step's description."""


# ---------------------------------------------------------------------------
# Step types
# ---------------------------------------------------------------------------

@dataclass
class RunCommand:
    argv: List[str]
    description: str


@dataclass
class WriteTargetFile:
    # Full absolute host path to write, e.g. /mnt/etc/skel/.bashrc
    path: str
    content: str
    mode: int
    description: str


Step = Union[RunCommand, WriteTargetFile]

_SUPPORTED_INITS = frozenset({"dinit", "runit"})


# ---------------------------------------------------------------------------
# Pure compiler
# ---------------------------------------------------------------------------

def compile_steps(plan: InstallPlan, *, target: str = "/mnt", init_id: str) -> List[Step]:
    """Compile an InstallPlan into a deterministic ordered list of Steps.

    Pure function: same inputs always produce identical output; no I/O or
    subprocess access at any point.
    Raises ExecutorError for unsupported init_id.
    """
    if init_id not in _SUPPORTED_INITS:
        raise ExecutorError(
            f"Unsupported init_id: '{init_id}'; supported: {sorted(_SUPPORTED_INITS)}"
        )

    t = target.rstrip("/")
    steps: List[Step] = []

    # 1. Install all packages in a single basestrap call
    n = len(plan.packages)
    steps.append(RunCommand(
        argv=["basestrap", t] + plan.packages,
        description=f"Install {n} package{'s' if n != 1 else ''} with basestrap",
    ))

    # 2. Generate fstab (stdout redirected via shell)
    steps.append(RunCommand(
        argv=["sh", "-c", f"fstabgen -U {t} >> {t}/etc/fstab"],
        description=f"Generate fstab → {t}/etc/fstab",
    ))

    # 3. Write each planned file (plan.files already sorted by path)
    for pf in plan.files:
        steps.append(WriteTargetFile(
            path=t + pf.path,
            content=pf.content,
            mode=pf.mode,
            description=f"Write {pf.path}",
        ))

    # 4. Enable services — argv depends on init_id
    for svc in plan.services:
        if init_id == "dinit":
            argv = [
                "artix-chroot", t,
                "ln", "-sf",
                f"/etc/dinit.d/{svc}",
                f"/etc/dinit.d/boot.d/{svc}",
            ]
        else:  # runit
            argv = [
                "artix-chroot", t,
                "ln", "-sf",
                f"/etc/runit/sv/{svc}",
                f"/etc/runit/runsvdir/default/{svc}",
            ]
        steps.append(RunCommand(
            argv=argv,
            description=f"Enable service {svc} ({init_id})",
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
) -> None:
    """Execute steps in order.

    dry_run=True  — logs 'DRY-RUN: <description>' per step; zero subprocess
                    calls and zero filesystem writes.
    dry_run=False — runs each step; raises ExecutorError on first failure,
                    chaining the original exception and embedding the step
                    description in the message.
    """
    for step in steps:
        if dry_run:
            log(f"DRY-RUN: {step.description}")
            continue

        if isinstance(step, RunCommand):
            result = subprocess.run(step.argv)
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
