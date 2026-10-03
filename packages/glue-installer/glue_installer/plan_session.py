"""
Runtime packages of a window-manager session's bar (roadmap 5.9).

A WM (gluewc, nvwm) ships no desktop environment, so the battery, brightness,
media, clipboard and portal plumbing its bar relies on must come from the
installer. Pure function, no I/O; called from plan.py's Rule 12 block.
Every name below was verified in the real Artix [world] db (STATE.md).
"""

from __future__ import annotations

from typing import Iterable, Set

from glue_installer.catalog import Session

# Any WM session, Wayland or X11: battery/AC via UPower (D-Bus activated),
# backlight writes without sudo (brightnessctl ships the udev rule granting
# group `video`), media keys and MPRIS (playerctl), notify-send (libnotify).
_WM_COMMON = frozenset({"upower", "brightnessctl", "playerctl", "libnotify"})

# Wayland WM only: the performance/balanced/power-saver buttons of the bar
# (power-profiles-daemon; its init service is enabled by plan.py whenever a
# session exists), clipboard CLI (wl-clipboard) and portals: the wlr backend
# for screencast/screenshot, the gtk one for file dialogs, both D-Bus
# activated on demand through xdg-desktop-portal, never permanent daemons.
_WM_WAYLAND = frozenset({
    "power-profiles-daemon", "wl-clipboard",
    "xdg-desktop-portal", "xdg-desktop-portal-wlr", "xdg-desktop-portal-gtk",
})


def session_runtime_packages(sessions: Iterable[Session]) -> Set[str]:
    """Return the bar runtime packages for the WM sessions in `sessions`.

    Desktop environments (kind "de") contribute nothing: KDE/GNOME/XFCE/
    Cinnamon ship their own power, brightness and portal stacks. Raises
    TypeError if an element is not a Session, ValueError on unknown kinds.
    """
    packages: Set[str] = set()
    for session in sessions:
        if not isinstance(session, Session):
            raise TypeError(f"expected Session, got {type(session).__name__}")
        if session.kind == "de":
            continue
        if session.kind != "wm":
            raise ValueError(f"session '{session.id}': unknown kind '{session.kind}'")
        packages.update(_WM_COMMON)
        if session.session_type == "wayland":
            packages.update(_WM_WAYLAND)
    return packages
