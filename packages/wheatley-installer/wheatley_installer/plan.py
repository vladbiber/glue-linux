"""
Selection model and plan resolver for Wheatley Linux installer.

Pure logic layer: no I/O, no subprocess, no filesystem access.
Turns a Catalog + Selection into a deterministic InstallPlan.

Warning order (documented, fixed):
  1. "Multiple sessions installed — pick your session at the login screen"
     (only when >= 2 sessions selected)
  2. "GPU driver auto-detection will run on the target system during install"
     (only when gaming=True and catalog.gaming.gpu_autodetect is True)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from wheatley_installer.catalog import Catalog


class PlanError(Exception):
    """Raised for any selection validation failure. Message names the offending field/id."""


@dataclass
class Selection:
    kernel_id: str
    init_id: str
    session_ids: List[str]
    shell_choice: Dict[str, str]  # session_id -> shell_id, required when shell_choices non-empty
    support_ids: List[str]
    gaming: bool
    minimal: bool


@dataclass
class PlannedFile:
    path: str
    content: str
    mode: int  # e.g. 0o644


@dataclass
class InstallPlan:
    packages: List[str]    # deduplicated, sorted alphabetically
    services: List[str]    # deduplicated, sorted alphabetically
    files: List[PlannedFile]  # sorted by path
    warnings: List[str]    # in the fixed order documented above


# ---------------------------------------------------------------------------
# Baseline file contents
# ---------------------------------------------------------------------------

_BASHRC_CONTENT = """\
# ~/.bashrc — Wheatley Linux
# Run fastfetch on interactive shell start
case $- in
    *i*) command -v fastfetch >/dev/null && fastfetch ;;
esac
"""

_ZSHRC_CONTENT = """\
# ~/.zshrc — Wheatley Linux
# Run fastfetch on interactive shell start
[[ -o interactive ]] && command -v fastfetch >/dev/null && fastfetch
"""

_BASELINE_FILES: List[PlannedFile] = [
    PlannedFile(path="/etc/skel/.bashrc", content=_BASHRC_CONTENT, mode=0o644),
    PlannedFile(path="/etc/skel/.zshrc", content=_ZSHRC_CONTENT, mode=0o644),
]

# Maps a service name to the Artix package BASE that ships its init scripts;
# the init-specific package is f"{base}-{init_id}" (Rule 10).
_SERVICE_PKG_BASE = {
    "greetd": "greetd",
    "bluetoothd": "bluez",
    "NetworkManager": "networkmanager",
}


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

def resolve_plan(catalog: Catalog, selection: Selection) -> InstallPlan:
    """Resolve a Selection against a Catalog into a deterministic InstallPlan.

    Raises PlanError with a human-readable message naming the offending field/id
    for any validation failure.
    """
    packages: set = set()
    services: set = set()
    warnings: List[str] = []

    kernel_map = {k.id: k for k in catalog.kernels}
    init_map = {i.id: i for i in catalog.inits}
    session_map = {s.id: s for s in catalog.sessions}
    shell_map = {s.id: s for s in catalog.shells}
    support_map = {s.id: s for s in catalog.support}

    # Rule 1: kernel_id
    if selection.kernel_id not in kernel_map:
        raise PlanError(f"Unknown kernel_id: '{selection.kernel_id}'")
    packages.update(kernel_map[selection.kernel_id].packages)

    # Rule 1: init_id
    if selection.init_id not in init_map:
        raise PlanError(f"Unknown init_id: '{selection.init_id}'")
    packages.update(init_map[selection.init_id].packages)

    # Rule 3: minimal constraints — sessions, shell_choice, and gaming must be absent
    if selection.minimal:
        if selection.session_ids:
            raise PlanError("minimal install cannot include sessions")
        if selection.shell_choice:
            raise PlanError("minimal install cannot include sessions/gaming")
        if selection.gaming:
            raise PlanError("minimal install cannot include gaming")

    # Rule 2: duplicate session_ids
    seen_sessions: set = set()
    for sid in selection.session_ids:
        if sid in seen_sessions:
            raise PlanError(f"Duplicate session_id: '{sid}'")
        seen_sessions.add(sid)

    # Rule 2: all session_ids must exist in catalog
    for sid in selection.session_ids:
        if sid not in session_map:
            raise PlanError(f"Unknown session_id: '{sid}'")

    selected_set = set(selection.session_ids)

    # Rule 4 & 5: validate every (session_id, shell_id) entry in shell_choice
    for sc_session_id, sc_shell_id in selection.shell_choice.items():
        if sc_session_id not in session_map:
            raise PlanError(
                f"shell_choice references unknown session_id: '{sc_session_id}'"
            )
        if sc_session_id not in selected_set:
            raise PlanError(
                f"shell_choice references unselected session_id: '{sc_session_id}'"
            )
        if not session_map[sc_session_id].shell_choices:
            raise PlanError(
                f"shell_choice entry for session '{sc_session_id}' which has no shell_choices"
            )
        if sc_shell_id not in shell_map:
            raise PlanError(
                f"shell_choice for session '{sc_session_id}' references unknown shell_id: '{sc_shell_id}'"
            )
        if sc_shell_id not in session_map[sc_session_id].shell_choices:
            raise PlanError(
                f"shell_choice for session '{sc_session_id}': "
                f"shell '{sc_shell_id}' is not in that session's shell_choices"
            )

    # Rule 7 & 4: add session packages/services; require shell_choice for sessions that need it
    for sid in selection.session_ids:
        session = session_map[sid]
        packages.update(session.packages)
        services.update(session.services)
        if session.shell_choices:
            if sid not in selection.shell_choice:
                raise PlanError(
                    f"session '{sid}' requires a shell_choice but none was provided"
                )
            shell_id = selection.shell_choice[sid]
            packages.update(shell_map[shell_id].packages)

    # Rule 7: greetd when any session is selected; warning at 2+
    if selection.session_ids:
        packages.add("greetd")
        services.add("greetd")
        if len(selection.session_ids) >= 2:
            warnings.append(
                "Multiple sessions installed — pick your session at the login screen"
            )

    # Rule 5: support toggles
    for support_id in selection.support_ids:
        if support_id not in support_map:
            raise PlanError(f"Unknown support_id: '{support_id}'")
        toggle = support_map[support_id]
        packages.update(toggle.packages)
        services.update(toggle.services)

    # Rule 6: gaming
    if selection.gaming:
        packages.update(catalog.gaming.packages)
        services.update(catalog.gaming.services)
        if catalog.gaming.gpu_autodetect:
            warnings.append(
                "GPU driver auto-detection will run on the target system during install"
            )

    # Rule 8: fastfetch always
    packages.add("fastfetch")

    # Rule 9: the installed system always gets network connectivity
    packages.add("networkmanager")
    services.add("NetworkManager")

    # Rule 10: every enabled service needs its init-specific service package
    # (Artix ships service scripts separately: e.g. greetd-dinit, bluez-runit,
    # networkmanager-openrc). Without these, enabling the service in the chroot
    # points at files that do not exist.
    for svc in sorted(services):
        base = _SERVICE_PKG_BASE.get(svc)
        if base is not None:
            packages.add(f"{base}-{selection.init_id}")

    return InstallPlan(
        packages=sorted(packages),
        services=sorted(services),
        files=sorted(_BASELINE_FILES, key=lambda f: f.path),
        warnings=warnings,
    )
