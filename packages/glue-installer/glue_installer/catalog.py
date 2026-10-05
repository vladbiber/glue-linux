"""
Catalog data model, loader, and validator for Glue Linux installer.

catalog.json top-level contract:
  {"version": 1, "kernels": [...], "inits": [...], "sessions": [...],
   "shells": [...], "support": [...], "gaming": {...}, "minimal": {...}}
Load with: load_catalog(Path("catalog/catalog.json"))
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from glue_installer.catalog_shots import SHOT_KEYS, shot_fields


class CatalogError(Exception):
    """Raised for any catalog validation failure. Message names the offending entry."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Keybinding:
    keys: str
    action: str


@dataclass
class Kernel:
    id: str
    name: str
    description: str
    packages: List[str]
    primary: bool


@dataclass
class Init:
    id: str
    name: str
    description: str
    packages: List[str]
    recommended: bool


@dataclass
class Session:
    id: str
    name: str
    kind: str  # "wm" or "de"
    description: str
    ease: int
    lightness: int
    keybindings: List[Keybinding]
    screenshot: Optional[str]
    packages: List[str]
    services: List[str]
    shell_choices: List[str]
    # Greeter integration (optional in JSON): how the login-session wrapper
    # starts this session. session_type "x11" sessions get a startx bootstrap
    # (greetd/tuigreet never starts X); "wayland" ones exec the compositor.
    session_type: str = "x11"  # "x11" or "wayland"
    exec: Optional[str] = None  # command to exec; defaults to the session id
    # XDG_CURRENT_DESKTOP/DesktopNames value when it must differ from the id
    # (GNOME components match the exact string "GNOME"). None = use the id.
    desktop: Optional[str] = None
    screenshots: List[str] = field(default_factory=list)
    # rough idle memory shown by the installer, e.g. "about 400 MB"
    idle_ram: Optional[str] = None


@dataclass
class Shell:
    id: str
    name: str
    description: str
    ease: int
    lightness: int
    keybindings: List[Keybinding]
    screenshot: Optional[str]
    packages: List[str]
    # Optional command the session wrapper autostarts once the compositor's
    # Wayland socket is up (e.g. "qs -c glue-bar"). None = no autostart.
    exec: Optional[str] = None
    screenshots: List[str] = field(default_factory=list)


@dataclass
class SupportToggle:
    id: str
    name: str
    description: str
    packages: List[str]
    services: List[str]
    default: bool


@dataclass
class SchedulerOption:
    """One CPU scheduler choice on the Gaming screen."""
    id: str
    name: str
    description: str
    default: bool


@dataclass
class Gaming:
    name: str
    description: str
    packages: List[str]
    services: List[str]
    gpu_autodetect: bool
    schedulers: List[SchedulerOption] = field(default_factory=list)


@dataclass
class Minimal:
    name: str
    description: str


@dataclass
class Catalog:
    version: int
    kernels: List[Kernel]
    inits: List[Init]
    sessions: List[Session]
    shells: List[Shell]
    support: List[SupportToggle]
    gaming: Gaming
    minimal: Minimal


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_REQUIRED_TOP = {"version", "kernels", "inits", "sessions", "shells", "support", "gaming", "minimal"}
_VALID_KINDS = {"wm", "de"}


def _require_keys(
    d: dict, required: set, ctx: str, *, optional: frozenset = frozenset(),
) -> None:
    missing = required - d.keys()
    if missing:
        raise CatalogError(f"{ctx}: missing required keys: {sorted(missing)}")
    unknown = d.keys() - required - optional
    if unknown:
        raise CatalogError(f"{ctx}: unknown keys: {sorted(unknown)}")


def _check_rating(value: object, field_name: str, ctx: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CatalogError(f"{ctx}: '{field_name}' must be an integer, got {type(value).__name__}")
    if not (1 <= value <= 5):
        raise CatalogError(f"{ctx}: '{field_name}' must be 1-5, got {value}")
    return value


def _parse_keybindings(
    raw: object, ctx: str, *, minimum: int = 5,
) -> List[Keybinding]:
    if not isinstance(raw, list):
        raise CatalogError(f"{ctx}: 'keybindings' must be a list")
    result: List[Keybinding] = []
    for i, kb in enumerate(raw):
        kb_ctx = f"{ctx}.keybindings[{i}]"
        if not isinstance(kb, dict):
            raise CatalogError(f"{kb_ctx}: must be an object")
        _require_keys(kb, {"keys", "action"}, kb_ctx)
        if not isinstance(kb["keys"], str) or not kb["keys"]:
            raise CatalogError(f"{kb_ctx}: 'keys' must be a non-empty string")
        if not isinstance(kb["action"], str) or not kb["action"]:
            raise CatalogError(f"{kb_ctx}: 'action' must be a non-empty string")
        result.append(Keybinding(keys=kb["keys"], action=kb["action"]))
    if len(result) < minimum:
        raise CatalogError(
            f"{ctx}: needs at least {minimum} keybindings, found {len(result)}"
        )
    return result


def _str_field(d: dict, key: str, ctx: str) -> str:
    val = d[key]
    if not isinstance(val, str):
        raise CatalogError(f"{ctx}: '{key}' must be a string, got {type(val).__name__}")
    return val


def _str_list(d: dict, key: str, ctx: str) -> List[str]:
    val = d[key]
    if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
        raise CatalogError(f"{ctx}: '{key}' must be a list of strings")
    return val


def _bool_field(d: dict, key: str, ctx: str) -> bool:
    val = d[key]
    if not isinstance(val, bool):
        raise CatalogError(f"{ctx}: '{key}' must be a boolean, got {type(val).__name__}")
    return val


def _optional_str(d: dict, key: str, ctx: str) -> Optional[str]:
    val = d[key]
    if val is not None and not isinstance(val, str):
        raise CatalogError(f"{ctx}: '{key}' must be a string or null")
    return val


# ---------------------------------------------------------------------------
# Entity parsers
# ---------------------------------------------------------------------------

_KERNEL_KEYS = {"id", "name", "description", "packages", "primary"}


def _parse_kernel(raw: dict, idx: int) -> Kernel:
    ctx = f"kernels[{idx}]"
    if not isinstance(raw, dict):
        raise CatalogError(f"{ctx}: must be an object")
    _require_keys(raw, _KERNEL_KEYS, ctx)
    eid = _str_field(raw, "id", ctx)
    ctx = f"kernel '{eid}'"
    return Kernel(
        id=eid,
        name=_str_field(raw, "name", ctx),
        description=_str_field(raw, "description", ctx),
        packages=_str_list(raw, "packages", ctx),
        primary=_bool_field(raw, "primary", ctx),
    )


_INIT_KEYS = {"id", "name", "description", "packages", "recommended"}


def _parse_init(raw: dict, idx: int) -> Init:
    ctx = f"inits[{idx}]"
    if not isinstance(raw, dict):
        raise CatalogError(f"{ctx}: must be an object")
    _require_keys(raw, _INIT_KEYS, ctx)
    eid = _str_field(raw, "id", ctx)
    ctx = f"init '{eid}'"
    return Init(
        id=eid,
        name=_str_field(raw, "name", ctx),
        description=_str_field(raw, "description", ctx),
        packages=_str_list(raw, "packages", ctx),
        recommended=_bool_field(raw, "recommended", ctx),
    )


_SESSION_KEYS = {"id", "name", "kind", "description", "ease", "lightness",
                 "keybindings", "packages", "services", "shell_choices"}
_SESSION_OPTIONAL_KEYS = frozenset({"session_type", "exec", "desktop", "idle_ram"}) | SHOT_KEYS
_VALID_SESSION_TYPES = {"x11", "wayland"}


def _parse_session(raw: dict, idx: int) -> Session:
    ctx = f"sessions[{idx}]"
    if not isinstance(raw, dict):
        raise CatalogError(f"{ctx}: must be an object")
    _require_keys(raw, _SESSION_KEYS, ctx, optional=_SESSION_OPTIONAL_KEYS)
    eid = _str_field(raw, "id", ctx)
    ctx = f"session '{eid}'"
    kind = _str_field(raw, "kind", ctx)
    if kind not in _VALID_KINDS:
        raise CatalogError(f"{ctx}: 'kind' must be one of {sorted(_VALID_KINDS)}, got '{kind}'")
    session_type = raw.get("session_type", "x11")
    if session_type not in _VALID_SESSION_TYPES:
        raise CatalogError(
            f"{ctx}: 'session_type' must be one of {sorted(_VALID_SESSION_TYPES)}, "
            f"got '{session_type}'"
        )
    exec_cmd = _optional_str(raw, "exec", ctx) if "exec" in raw else None
    if exec_cmd is not None and not exec_cmd:
        raise CatalogError(f"{ctx}: 'exec' must be a non-empty string or null")
    desktop = _optional_str(raw, "desktop", ctx) if "desktop" in raw else None
    if desktop is not None and not desktop:
        raise CatalogError(f"{ctx}: 'desktop' must be a non-empty string or null")
    idle_ram = _optional_str(raw, "idle_ram", ctx) if "idle_ram" in raw else None
    return Session(
        id=eid,
        name=_str_field(raw, "name", ctx),
        kind=kind,
        description=_str_field(raw, "description", ctx),
        ease=_check_rating(raw["ease"], "ease", ctx),
        lightness=_check_rating(raw["lightness"], "lightness", ctx),
        keybindings=_parse_keybindings(raw["keybindings"], ctx),
        **shot_fields(raw, ctx),
        packages=_str_list(raw, "packages", ctx),
        services=_str_list(raw, "services", ctx),
        shell_choices=_str_list(raw, "shell_choices", ctx),
        session_type=session_type,
        exec=exec_cmd,
        desktop=desktop,
        idle_ram=idle_ram,
    )


_SHELL_KEYS = {"id", "name", "description", "ease", "lightness", "keybindings", "packages"}
_SHELL_OPTIONAL_KEYS = frozenset({"exec"}) | SHOT_KEYS


def _parse_shell(raw: dict, idx: int) -> Shell:
    ctx = f"shells[{idx}]"
    if not isinstance(raw, dict):
        raise CatalogError(f"{ctx}: must be an object")
    _require_keys(raw, _SHELL_KEYS, ctx, optional=_SHELL_OPTIONAL_KEYS)
    eid = _str_field(raw, "id", ctx)
    ctx = f"shell '{eid}'"
    return Shell(
        id=eid,
        name=_str_field(raw, "name", ctx),
        description=_str_field(raw, "description", ctx),
        ease=_check_rating(raw["ease"], "ease", ctx),
        lightness=_check_rating(raw["lightness"], "lightness", ctx),
        # A shell/bar has no hotkeys of its own (the compositor owns keybinds),
        # so unlike sessions, shells may list none.
        keybindings=_parse_keybindings(raw["keybindings"], ctx, minimum=0),
        **shot_fields(raw, ctx),
        packages=_str_list(raw, "packages", ctx),
        exec=(_optional_str(raw, "exec", ctx) if "exec" in raw else None),
    )


_SUPPORT_KEYS = {"id", "name", "description", "packages", "services", "default"}


def _parse_support(raw: dict, idx: int) -> SupportToggle:
    ctx = f"support[{idx}]"
    if not isinstance(raw, dict):
        raise CatalogError(f"{ctx}: must be an object")
    _require_keys(raw, _SUPPORT_KEYS, ctx)
    eid = _str_field(raw, "id", ctx)
    ctx = f"support '{eid}'"
    return SupportToggle(
        id=eid,
        name=_str_field(raw, "name", ctx),
        description=_str_field(raw, "description", ctx),
        packages=_str_list(raw, "packages", ctx),
        services=_str_list(raw, "services", ctx),
        default=_bool_field(raw, "default", ctx),
    )


_GAMING_KEYS = {"name", "description", "packages", "services", "gpu_autodetect"}
_GAMING_OPTIONAL_KEYS = frozenset({"schedulers"})
_SCHEDULER_KEYS = {"id", "name", "description", "default"}


def _parse_schedulers(raw: object, ctx: str) -> List[SchedulerOption]:
    if not isinstance(raw, list):
        raise CatalogError(f"{ctx}: 'schedulers' must be a list")
    result: List[SchedulerOption] = []
    for i, s in enumerate(raw):
        s_ctx = f"{ctx}.schedulers[{i}]"
        if not isinstance(s, dict):
            raise CatalogError(f"{s_ctx}: must be an object")
        _require_keys(s, _SCHEDULER_KEYS, s_ctx)
        result.append(SchedulerOption(
            id=_str_field(s, "id", s_ctx),
            name=_str_field(s, "name", s_ctx),
            description=_str_field(s, "description", s_ctx),
            default=_bool_field(s, "default", s_ctx),
        ))
    if result:
        ids = [o.id for o in result]
        if len(ids) != len(set(ids)):
            raise CatalogError(f"{ctx}: duplicate scheduler ids")
        defaults = [o for o in result if o.default]
        if len(defaults) != 1:
            raise CatalogError(
                f"{ctx}: exactly one scheduler must have default=true, "
                f"found {len(defaults)}"
            )
    return result


def _parse_gaming(raw: object) -> Gaming:
    ctx = "gaming"
    if not isinstance(raw, dict):
        raise CatalogError(f"{ctx}: must be an object")
    _require_keys(raw, _GAMING_KEYS, ctx, optional=_GAMING_OPTIONAL_KEYS)
    if not isinstance(raw["gpu_autodetect"], bool):
        raise CatalogError(f"{ctx}: 'gpu_autodetect' must be a boolean")
    return Gaming(
        name=_str_field(raw, "name", ctx),
        description=_str_field(raw, "description", ctx),
        packages=_str_list(raw, "packages", ctx),
        services=_str_list(raw, "services", ctx),
        gpu_autodetect=raw["gpu_autodetect"],
        schedulers=_parse_schedulers(raw.get("schedulers", []), ctx),
    )


_MINIMAL_KEYS = {"name", "description"}


def _parse_minimal(raw: object) -> Minimal:
    ctx = "minimal"
    if not isinstance(raw, dict):
        raise CatalogError(f"{ctx}: must be an object")
    _require_keys(raw, _MINIMAL_KEYS, ctx)
    return Minimal(
        name=_str_field(raw, "name", ctx),
        description=_str_field(raw, "description", ctx),
    )


# ---------------------------------------------------------------------------
# Cross-entity validation
# ---------------------------------------------------------------------------

def _check_unique_ids(items: list, section: str) -> set:
    seen: set = set()
    for item in items:
        if item.id in seen:
            raise CatalogError(f"{section}: duplicate id '{item.id}'")
        seen.add(item.id)
    return seen


def _validate_cross(catalog: Catalog) -> None:
    kernel_ids = _check_unique_ids(catalog.kernels, "kernels")
    init_ids = _check_unique_ids(catalog.inits, "inits")
    session_ids = _check_unique_ids(catalog.sessions, "sessions")
    shell_ids = _check_unique_ids(catalog.shells, "shells")
    _check_unique_ids(catalog.support, "support")

    # All ids across kernels/inits/sessions/shells must be globally unique
    all_ids = kernel_ids | init_ids | session_ids | shell_ids
    # (no cross-section dupe check required by contract, but check within each section - done above)

    # Exactly one primary kernel
    primaries = [k for k in catalog.kernels if k.primary]
    if len(primaries) != 1:
        raise CatalogError(
            f"kernels: exactly one kernel must have primary=true, found {len(primaries)}"
        )

    # At least one recommended init
    recommended_inits = [i for i in catalog.inits if i.recommended]
    if not recommended_inits:
        raise CatalogError("inits: at least one init must have recommended=true")

    # shell_choices references
    for session in catalog.sessions:
        for sc in session.shell_choices:
            if sc not in shell_ids:
                raise CatalogError(
                    f"session '{session.id}': shell_choices references unknown shell id '{sc}'"
                )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_catalog(path: Path) -> Catalog:
    """Parse and validate catalog.json; CatalogError on any violation.

    Does NOT require screenshot files to exist on disk.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogError(f"Cannot read catalog file '{path}': {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CatalogError(f"catalog.json is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise CatalogError("catalog.json root must be a JSON object")

    _require_keys(data, _REQUIRED_TOP, "catalog root")

    if data["version"] != 1:
        raise CatalogError(f"catalog root: unsupported version {data['version']!r}, expected 1")

    if not isinstance(data["kernels"], list):
        raise CatalogError("catalog root: 'kernels' must be a list")
    kernels = [_parse_kernel(k, i) for i, k in enumerate(data["kernels"])]

    if not isinstance(data["inits"], list):
        raise CatalogError("catalog root: 'inits' must be a list")
    inits = [_parse_init(k, i) for i, k in enumerate(data["inits"])]

    if not isinstance(data["sessions"], list):
        raise CatalogError("catalog root: 'sessions' must be a list")
    sessions = [_parse_session(s, i) for i, s in enumerate(data["sessions"])]

    if not isinstance(data["shells"], list):
        raise CatalogError("catalog root: 'shells' must be a list")
    shells = [_parse_shell(s, i) for i, s in enumerate(data["shells"])]

    if not isinstance(data["support"], list):
        raise CatalogError("catalog root: 'support' must be a list")
    support = [_parse_support(s, i) for i, s in enumerate(data["support"])]

    gaming = _parse_gaming(data["gaming"])
    minimal = _parse_minimal(data["minimal"])

    catalog = Catalog(
        version=data["version"],
        kernels=kernels,
        inits=inits,
        sessions=sessions,
        shells=shells,
        support=support,
        gaming=gaming,
        minimal=minimal,
    )

    _validate_cross(catalog)
    return catalog


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: python3 -m glue_installer.catalog <path-to-catalog.json>", file=sys.stderr)
        sys.exit(1)

    catalog_path = Path(sys.argv[1])
    try:
        cat = load_catalog(catalog_path)
    except CatalogError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(
        f"OK: {len(cat.sessions)} sessions, {len(cat.shells)} shells, {len(cat.kernels)} kernels"
    )
