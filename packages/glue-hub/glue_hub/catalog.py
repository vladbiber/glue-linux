"""AppStream, Flathub and AUR parsing plus local catalog search."""

from __future__ import annotations

import gzip
import json
import re
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path
from typing import Iterable

from .model import App, AppSource, merge_apps


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join(" ".join(node.itertext()).split())


def _children(node: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in node.iter() if child.tag.rsplit("}", 1)[-1] == name]


def _first(node: ET.Element, name: str) -> ET.Element | None:
    return next(iter(_children(node, name)), None)


def _component_app(
    component: ET.Element, installed_set: set[str], icon_root: Path | None = None,
) -> App | None:
    app_id = _text(_first(component, "id"))
    name = _text(_first(component, "name"))
    package = _text(_first(component, "pkgname"))
    if not app_id or not name or not package:
        return None
    icons = _children(component, "icon")
    cached = [node for node in icons if node.attrib.get("type") == "cached"]
    icon_node = max(cached, key=lambda n: int(n.attrib.get("width", "0")), default=None)
    if icon_node is None:
        icon_node = next(iter(icons), None)
    icon = _text(icon_node)
    if icon_root and icon_node is not None and icon_node.attrib.get("type") == "cached":
        size = icon_node.attrib.get("width", "128") + "x" + icon_node.attrib.get("height", "128")
        candidate = icon_root / size / icon
        if candidate.is_file():
            icon = str(candidate)
    shots: list[str] = []
    for shot in _children(component, "screenshot"):
        images = _children(shot, "image")
        if images:
            best = max(images, key=lambda n: int(n.attrib.get("width", "0")))
            if _text(best):
                shots.append(_text(best))
    categories = tuple(_text(n) for n in _children(component, "category") if _text(n))
    return App(
        app_id=app_id,
        name=name,
        summary=_text(_first(component, "summary")),
        description=_text(_first(component, "description")),
        developer=_text(_first(component, "developer_name")),
        icon=icon,
        screenshots=tuple(shots),
        categories=categories,
        license=_text(_first(component, "project_license")),
        sources=(AppSource("repo", package, installed=package in installed_set),),
    )


def parse_appstream(data: bytes | str, installed: Iterable[str] = ()) -> list[App]:
    """Parse freedesktop AppStream XML without importing GI/AppStream."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    root = ET.fromstring(data)
    installed_set = set(installed)
    result: list[App] = []
    components = [n for n in root.iter() if n.tag.rsplit("}", 1)[-1] == "component"]
    for component in components:
        app = _component_app(component, installed_set)
        if app:
            result.append(app)
    return result


def load_appstream(paths: Iterable[Path], installed: Iterable[str] = ()) -> list[App]:
    """Stream AppStream catalogs so a large repository XML is never held twice."""
    installed_set = set(installed)
    apps: list[App] = []
    for path in paths:
        try:
            stream = gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb")
            origin = ""
            with closing(stream):
                for event, element in ET.iterparse(stream, events=("start", "end")):
                    tag = element.tag.rsplit("}", 1)[-1]
                    if event == "start" and tag == "components":
                        origin = element.attrib.get("origin", "")
                    if event == "end" and tag == "component":
                        icon_root = (Path("/usr/share/swcatalog/icons") / origin
                                     if origin else None)
                        app = _component_app(element, installed_set, icon_root)
                        if app:
                            apps.append(app)
                        element.clear()
        except (OSError, ET.ParseError, UnicodeError):
            continue
    return merge_apps(apps)


def system_appstream_paths() -> list[Path]:
    roots = (Path("/usr/share/swcatalog/xml"), Path("/usr/share/app-info/xmls"))
    return sorted(path for root in roots for pattern in ("*.xml", "*.xml.gz")
                  for path in root.glob(pattern))


def parse_flathub_search(payload: str | bytes | dict, installed: Iterable[str] = ()) -> list[App]:
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    hits = payload.get("hits", payload.get("data", [])) if isinstance(payload, dict) else []
    installed_set = set(installed)
    result: list[App] = []
    for hit in hits:
        app_id = str(hit.get("app_id") or hit.get("id") or "")
        name = str(hit.get("name") or "")
        if not app_id or not name:
            continue
        screenshots = hit.get("screenshots") or []
        shot_urls: list[str] = []
        for shot in screenshots:
            if isinstance(shot, str):
                shot_urls.append(shot)
            elif isinstance(shot, dict):
                sizes = shot.get("sizes") or []
                if sizes:
                    best = sizes[-1] if isinstance(sizes[-1], dict) else {}
                    if best.get("src"):
                        shot_urls.append(str(best["src"]))
        result.append(App(
            app_id=app_id,
            name=name,
            summary=str(hit.get("summary") or hit.get("description") or ""),
            description=str(hit.get("description") or ""),
            developer=str(hit.get("developer_name") or hit.get("developer") or ""),
            icon=str(hit.get("icon") or ""),
            screenshots=tuple(shot_urls),
            categories=tuple(hit.get("categories") or ()),
            license=str(hit.get("project_license") or hit.get("license") or ""),
            sources=(AppSource("flatpak", app_id, str(hit.get("version") or ""),
                               app_id in installed_set),),
        ))
    return result


_AUR_HEADER = re.compile(r"^aur/(?P<name>[A-Za-z0-9@._+:-]+)\s+(?P<version>\S+)")


def parse_yay_search(output: str, installed: Iterable[str] = ()) -> list[App]:
    """Parse `yay -Ssa --aur` output; ANSI color and vote suffixes are tolerated."""
    clean = re.sub(r"\x1b\[[0-9;]*m", "", output)
    installed_set = set(installed)
    result: list[App] = []
    current: tuple[str, str] | None = None
    description = ""
    for line in clean.splitlines():
        header = _AUR_HEADER.match(line.strip())
        if header:
            if current:
                name, version = current
                result.append(_aur_app(name, version, description, name in installed_set))
            current = (header.group("name"), header.group("version"))
            description = ""
        elif current and line.strip():
            description = line.strip()
    if current:
        name, version = current
        result.append(_aur_app(name, version, description, name in installed_set))
    return result


def _aur_app(name: str, version: str, description: str, installed: bool) -> App:
    return App(
        app_id=f"aur.{name}", name=name, summary=description,
        description=description, categories=("AUR",),
        sources=(AppSource("aur", name, version, installed),),
    )


def parse_yay_info(output: str, installed: Iterable[str] = ()) -> App | None:
    fields: dict[str, str] = {}
    for line in re.sub(r"\x1b\[[0-9;]*m", "", output).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    name = fields.get("Name", "")
    if not name:
        return None
    description = fields.get("Description", "")
    return App(
        app_id=f"aur.{name}", name=name, summary=description,
        description=description, developer=fields.get("Maintainer", ""),
        categories=tuple(fields.get("Keywords", "").split()),
        license=fields.get("Licenses", ""),
        sources=(AppSource("aur", name, fields.get("Version", ""),
                           name in set(installed)),),
    )


class CatalogIndex:
    def __init__(self, apps: Iterable[App] = ()) -> None:
        self._apps = merge_apps(apps)

    @property
    def apps(self) -> tuple[App, ...]:
        return tuple(self._apps)

    def extend(self, apps: Iterable[App]) -> None:
        self._apps = merge_apps([*self._apps, *apps])

    def search(self, query: str, source: str = "all", limit: int = 80) -> list[App]:
        words = [word.casefold() for word in query.split() if word]
        found: list[tuple[int, App]] = []
        for app in self._apps:
            if source != "all" and not any(s.kind == source for s in app.sources):
                continue
            name = app.name.casefold()
            haystack = " ".join((name, app.summary, app.description, " ".join(app.categories))).casefold()
            if words and not all(word in haystack for word in words):
                continue
            score = sum(10 if name.startswith(word) else 2 if word in name else 1 for word in words)
            found.append((-score, app))
        found.sort(key=lambda item: (item[0], item[1].name.casefold()))
        return [app for _, app in found[:limit]]

    def featured(self, names: Iterable[str]) -> list[App]:
        """Return curated apps in curator order, ignoring unavailable entries."""
        result: list[App] = []
        for wanted in names:
            key = "".join(ch for ch in wanted.casefold() if ch.isalnum())
            match = next((app for app in self._apps
                          if key in "".join(ch for ch in app.name.casefold()
                                            if ch.isalnum())), None)
            if match and match not in result:
                result.append(match)
        return result
