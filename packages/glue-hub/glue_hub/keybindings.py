"""Select friendly keybindings for the current session and shell."""

from __future__ import annotations

import configparser
from pathlib import Path


def load_shell_choices(path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
        return dict(parser["session"]) if parser.has_section("session") else {}
    except (OSError, configparser.Error):
        return {}


def binding_rows(data: dict, desktop: str, choices: dict[str, str]) -> tuple[str, list[tuple[str, str]]]:
    sessions = data.get("sessions", [])
    session = next((item for item in sessions
                    if str(item.get("id", "")).casefold() in desktop.casefold()),
                   sessions[0] if sessions else {})
    session_id = str(session.get("id", desktop.casefold()))
    bindings = list(session.get("keybindings", []))
    shell_id = choices.get(f"shell.{session_id}")
    shell = next((item for item in data.get("shells", [])
                  if item.get("id") == shell_id), None)
    if shell:
        bindings.extend(shell.get("keybindings", []))
    rows = [(str(item.get("keys", "")), str(item.get("action", "")))
            for item in bindings if item.get("keys") and item.get("action")]
    return session_id, rows
