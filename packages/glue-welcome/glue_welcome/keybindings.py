"""Find the running window manager and order its shortcuts for newcomers."""

from __future__ import annotations

import configparser
import os
from pathlib import Path
from typing import Iterable, Mapping

# XDG desktop names and process names that identify each catalog session.
DESKTOP_NAMES = {
    "gluewc": ("gluewc",), "nvwm": ("nvwm",), "kde-plasma": ("kde", "plasma"),
    "xfce": ("xfce",), "gnome": ("gnome",), "cinnamon": ("cinnamon", "x-cinnamon"),
}
PROCESS_NAMES = {
    "gluewc": ("gluewc",), "nvwm": ("nvwm",),
    "kde-plasma": ("kwin_wayland", "kwin_x11", "plasmashell"),
    "xfce": ("xfwm4", "xfce4-session"), "gnome": ("gnome-shell",),
    "cinnamon": ("cinnamon",),
}
FIRST = (("launcher", "menu", "show all applications", "activities"),
         ("close",), ("overview",))


def load_shell_choices(path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
        return dict(parser["session"]) if parser.has_section("session") else {}
    except (OSError, configparser.Error):
        return {}


def running_processes(proc: Path = Path("/proc")) -> set[str]:
    """Names of this user's processes (comm), read straight from /proc."""
    names: set[str] = set()
    uid = os.getuid()
    for entry in proc.iterdir() if proc.is_dir() else ():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            names.add((entry / "comm").read_text().strip())
        except OSError:
            continue
    return names


def detect_session(session_ids: Iterable[str], env: Mapping[str, str],
                   processes: set[str]) -> str | None:
    """The session environment wins; otherwise look for a running WM process."""
    ids = list(session_ids)
    # set by the login wrapper: tells gluewc and gluewc-noctalia apart
    if env.get("GLUE_SESSION") in ids:
        return env["GLUE_SESSION"]
    for key in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION"):
        words = {word.casefold() for word in env.get(key, "").replace(":", " ").split()}
        for session in ids:
            if words & set(DESKTOP_NAMES.get(session, (session,))):
                return session
    for session in ids:
        if processes & set(PROCESS_NAMES.get(session, (session,))):
            return session
    return None


def order_rows(rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Open apps first, then close a window, then the overview, then the rest."""
    def rank(row: tuple[str, str]) -> int:
        action = row[1].casefold()
        return next((index for index, words in enumerate(FIRST)
                     if any(word in action for word in words)), len(FIRST))
    return sorted(rows, key=rank)


def binding_rows(data: dict, desktop: str, choices: dict[str, str]) -> tuple[str, list[tuple[str, str]]]:
    sessions = data.get("sessions", [])
    session = next((item for item in sessions
                    if str(item.get("id", "")).casefold() == desktop.casefold()),
                   None) or next((item for item in sessions
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
    return session_id, order_rows(rows)
