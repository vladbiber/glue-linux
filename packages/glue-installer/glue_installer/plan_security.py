"""Security level (installer Security page) in the plan.

Pure module. Every install gets the glue-security package; the level itself
is applied on the target by `glue-security level <level>` (security_steps),
which also writes its kernel parameters to /etc/glue/cmdline.d before
glue-boot-update builds limine.conf. Super hardened swaps Firefox for
LibreWolf here (packages and the WM sessions' default browser).
"""

from __future__ import annotations

from typing import Iterable, List, Set

from glue_installer.plan_types import PlanError, PlannedFile

LEVELS = ("basic", "hardened", "super")
DEFAULT_LEVEL = "basic"
PACKAGE = "glue-security"

_SUPER_GAMING = ("Super hardened disables what games need: Steam, Flatpak apps and "
                 "anti-cheat games may stop working, and games run 10-30% slower")
_SUPER_NVIDIA = ("NVIDIA graphics card: Super hardened keeps its driver and skips "
                 "kernel lockdown, so the level shows as custom")
# glue-security installs it inside the chroot; boot.conf names it from the start
HARDENED_KERNEL = "linux-hardened"


def apply_security(level: str, packages: Set[str], files: List[PlannedFile],
                   gaming: bool, gpu_vendors: Iterable[str] = ()) -> List[str]:
    """Adjust packages/files in place for the level; return its warnings."""
    if level not in LEVELS:
        raise PlanError(f"Unknown security_level: '{level}'")
    packages.add(PACKAGE)
    if level != "super":
        return []
    if "firefox" in packages:
        packages.discard("firefox")
        packages.add("librewolf")
    for i, pf in enumerate(files):
        if pf.path.endswith("-mimeapps.list") and "firefox.desktop" in pf.content:
            files[i] = PlannedFile(pf.path, pf.content.replace(
                "firefox.desktop", "librewolf.desktop"), pf.mode)
    warnings = []
    if gaming:
        warnings.append(_SUPER_GAMING)
    if gpu_vendors and "nvidia" in gpu_vendors:
        warnings.append(_SUPER_NVIDIA)
    return warnings


def boot_kernel(level: str, kernel: str) -> str:
    """Kernel package for /etc/glue/boot.conf: linux-hardened for super."""
    return HARDENED_KERNEL if level == "super" else kernel


def security_steps(level: str, target: str):
    """Chroot step: `glue-security level <level>` on the installed system.
    Runs after the plan files and services, before the bootloader step."""
    if level not in LEVELS:
        raise PlanError(f"Unknown security_level: '{level}'")
    from glue_installer.executor import RunCommand
    return [RunCommand(
        argv=["artix-chroot", target.rstrip("/"), "glue-security", "level", level],
        description=f"Apply the security level ({level}) with glue-security")]
