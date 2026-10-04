"""Parsing of the `screenshot` / `screenshots` catalog fields."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import List, Optional, Tuple

SHOT_KEYS = frozenset({"screenshot", "screenshots"})


def _error(msg: str):
    from glue_installer.catalog import CatalogError
    return CatalogError(msg)


def _check_path(path: object, ctx: str, key: str) -> str:
    if not isinstance(path, str) or not path:
        raise _error(f"{ctx}: '{key}' entries must be non-empty strings")
    if path.startswith("/") or "\\" in path:
        raise _error(f"{ctx}: '{key}' path must be relative to catalog/: {path!r}")
    if ".." in PurePosixPath(path).parts:
        raise _error(f"{ctx}: '{key}' path must not contain '..': {path!r}")
    return path


def parse_shots(raw: dict, ctx: str) -> Tuple[List[str], Optional[str]]:
    """Return (screenshots, first) from legacy `screenshot` and/or `screenshots`."""
    if "screenshot" not in raw and "screenshots" not in raw:
        raise _error(f"{ctx}: missing required keys: ['screenshots']")
    legacy = raw.get("screenshot")
    if legacy is not None and not isinstance(legacy, str):
        raise _error(f"{ctx}: 'screenshot' must be a string or null")
    if "screenshots" not in raw:
        if legacy is None:
            return [], None
        return [_check_path(legacy, ctx, "screenshot")], legacy
    shots = raw["screenshots"]
    if not isinstance(shots, list) or not shots:
        raise _error(f"{ctx}: 'screenshots' must be a non-empty list of strings")
    seen = set()
    for path in shots:
        _check_path(path, ctx, "screenshots")
        if path in seen:
            raise _error(f"{ctx}: duplicate screenshot {path!r}")
        seen.add(path)
    if "screenshot" in raw and legacy != shots[0]:
        raise _error(f"{ctx}: 'screenshot' ({legacy!r}) must equal "
                     f"screenshots[0] ({shots[0]!r})")
    return list(shots), shots[0]


def shot_fields(raw: dict, ctx: str) -> dict:
    """Keyword arguments `screenshot` + `screenshots` for Session/Shell."""
    shots, first = parse_shots(raw, ctx)
    return {"screenshot": first, "screenshots": shots}
