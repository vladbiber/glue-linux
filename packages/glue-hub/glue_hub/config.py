"""Glue Hub configuration and theme validation."""

from __future__ import annotations

import configparser
import json
from dataclasses import dataclass
from pathlib import Path


THEMES = ("glue", "vitrina", "mozaic")


@dataclass
class HubConfig:
    theme: str = "glue"
    autostart: bool = True
    store: bool = True
    aur_warning_seen: bool = False

    @classmethod
    def load(cls, user_path: Path, system_path: Path | None = None) -> "HubConfig":
        parser = configparser.ConfigParser()
        paths = [str(path) for path in (system_path, user_path) if path and path.exists()]
        parser.read(paths, encoding="utf-8")
        section = parser["hub"] if parser.has_section("hub") else {}
        theme = str(section.get("theme", "glue"))
        return cls(
            theme=theme if theme in THEMES else "glue",
            autostart=str(section.get("autostart", "1")).lower() in {"1", "true", "yes"},
            store=str(section.get("store", "1")).lower() in {"1", "true", "yes"},
            aur_warning_seen=str(section.get("aur_warning_seen", "0")).lower()
            in {"1", "true", "yes"},
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = (
            "[hub]\n"
            f"theme={self.theme}\n"
            f"autostart={int(self.autostart)}\n"
            f"store={int(self.store)}\n"
            f"aur_warning_seen={int(self.aur_warning_seen)}\n"
        )
        temp = path.with_suffix(".tmp")
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)


def validate_themes(root: Path) -> list[str]:
    errors: list[str] = []
    for theme in THEMES:
        folder = root / theme
        css = folder / "theme.css"
        layout = folder / "layout.json"
        if not css.is_file() or not css.read_text(encoding="utf-8").strip():
            errors.append(f"{theme}: theme.css missing or empty")
        try:
            data = json.loads(layout.read_text(encoding="utf-8"))
            if not {"card_radius", "density", "hero", "sidebar"} <= data.keys():
                errors.append(f"{theme}: layout.json missing keys")
        except (OSError, ValueError):
            errors.append(f"{theme}: invalid layout.json")
    return errors
