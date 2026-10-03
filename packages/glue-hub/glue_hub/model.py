"""Pure application model shared by every Glue Hub source."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable


SOURCE_ORDER = {"repo": 0, "flatpak": 1, "aur": 2}


@dataclass(frozen=True)
class AppSource:
    kind: str
    ref: str
    version: str = ""
    installed: bool = False

    def __post_init__(self) -> None:
        if self.kind not in SOURCE_ORDER:
            raise ValueError(f"unknown application source: {self.kind}")
        if not self.ref:
            raise ValueError("application source needs a reference")


@dataclass(frozen=True)
class App:
    app_id: str
    name: str
    summary: str = ""
    description: str = ""
    developer: str = ""
    icon: str = ""
    screenshots: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    license: str = ""
    sources: tuple[AppSource, ...] = ()

    @property
    def preferred_source(self) -> AppSource | None:
        return min(self.sources, key=lambda s: SOURCE_ORDER[s.kind], default=None)

    @property
    def installed(self) -> bool:
        return any(source.installed for source in self.sources)

    def with_source(self, source: AppSource) -> "App":
        by_kind = {item.kind: item for item in self.sources}
        by_kind[source.kind] = source
        ordered = tuple(sorted(by_kind.values(), key=lambda s: SOURCE_ORDER[s.kind]))
        return replace(self, sources=ordered)


def canonical_key(app: App) -> str:
    app_id = app.app_id.casefold()
    for suffix in (".desktop", ".appdata", ".metainfo"):
        if app_id.endswith(suffix):
            app_id = app_id[:-len(suffix)]
    generic_ids = {"firefox", "vlc", "steam", "gimp", "libreoffice"}
    tail = app_id.rsplit(".", 1)[-1]
    if tail in generic_ids:
        return tail
    return "".join(ch for ch in app.name.casefold() if ch.isalnum()) or app_id


def merge_apps(apps: Iterable[App]) -> list[App]:
    """Deduplicate apps while retaining every available installation source."""
    merged: dict[str, App] = {}
    for app in apps:
        key = canonical_key(app)
        if key not in merged:
            merged[key] = app
            continue
        old = merged[key]
        sources = old.sources
        combined = old
        for source in app.sources:
            combined = combined.with_source(source)
        combined = replace(
            combined,
            summary=old.summary or app.summary,
            description=old.description or app.description,
            developer=old.developer or app.developer,
            icon=old.icon or app.icon,
            screenshots=old.screenshots or app.screenshots,
            categories=tuple(dict.fromkeys(old.categories + app.categories)),
            license=old.license or app.license,
        )
        if not sources and app.sources:
            combined = replace(combined, sources=app.sources)
        merged[key] = combined
    return sorted(merged.values(), key=lambda item: item.name.casefold())
