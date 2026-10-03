"""
Greeter profile for the installed system: greetd config on VT7 plus one login
wrapper and one session entry per selected session. Pure string building, no
I/O. Split out of plan.py to keep that module under 500 lines.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from glue_installer.catalog import Session
from glue_installer.plan_types import PlannedFile

# The greeter runs on a dedicated VT (tty7), away from the kernel console and
# boot messages (tty1) and from the login gettys (tty1-6). The launcher uses
# ReGreet under Cage when KMS is available and falls back to Tuigreet on
# machines without a DRM card (notably simple VMs).
_GREETD_CONFIG_CONTENT = """\
# /etc/greetd/config.toml — written by the Glue Linux installer.
[terminal]
vt = 7

[default_session]
command = "/usr/local/bin/glue-greeter"
user = "greeter"
"""

_REGREET_CONFIG_CONTENT = """\
# /etc/greetd/regreet.toml — written by the Glue Linux installer.
# No [background] section: the default is Glue's solid CSS background.
skip_selection = false

[GTK]
application_prefer_dark_theme = false
cursor_theme_name = "Adwaita"
cursor_blink = true
font_name = "Cantarell 14"
icon_theme_name = "Adwaita"
theme_name = "Adwaita"

[commands]
reboot = ["loginctl", "reboot"]
poweroff = ["loginctl", "poweroff"]
x11_prefix = ["startx", "/usr/bin/env"]

[appearance]
greeting_msg = "Glue Linux"

[widget.clock]
format = "%a %H:%M"
resolution = "1s"
"""

_REGREET_CSS_CONTENT = """\
/* Glue Linux ReGreet theme — clean, light and without a wallpaper. */
* {
    color: #172033;
}

window,
.background,
.view {
    background-color: #F4F6F8;
}

frame {
    background-color: #FFFFFF;
    border: 1px solid #D9DFE8;
    border-radius: 16px;
}

button,
button * {
    color: #FFFFFF;
}

button {
    background: #4465E9;
    border: 1px solid #3654C7;
    border-radius: 10px;
}

button:hover,
button:focus {
    background: #3654C7;
}

entry,
combobox button {
    color: #172033;
    background: #FFFFFF;
    border: 1px solid #C7D0DD;
    border-radius: 10px;
}

combobox button * {
    color: #172033;
}

entry:focus,
combobox button:focus {
    border-color: #4465E9;
}
"""

_TUIGREET_COMMAND = (
    "/usr/bin/tuigreet --remember --remember-session --time "
    "--greeting 'Glue Linux' --sessions /usr/share/glue/sessions "
    "--theme 'border=yellow;text=white;prompt=yellow;time=yellow;"
    "action=yellow;button=yellow;container=black;input=white'"
)

_GREETER_WRAPPER_CONTENT = f"""\
#!/bin/sh
# Choose the graphical ReGreet login screen when KMS is available. Tuigreet
# remains usable on VMs and unusual machines that expose no DRM card.
set -eu

fallback() {{
    exec {_TUIGREET_COMMAND}
}}

if [ "${{GLUE_GREETER_FORCE_REGREET:-0}}" != 1 ] && ! ls /dev/dri/card* >/dev/null 2>&1; then
    fallback
fi
command -v cage >/dev/null 2>&1 || fallback
command -v regreet >/dev/null 2>&1 || fallback

# ReGreet discovers $XDG_DATA_DIRS/{{x,wayland-}}sessions. Build that standard
# view from the installer's authoritative /usr/share/glue/sessions directory.
# The custom key records which list each generated entry belongs to.
if [ -z "${{XDG_RUNTIME_DIR:-}}" ]; then
    XDG_RUNTIME_DIR="/tmp/glue-greeter-runtime-$(id -u)"
    export XDG_RUNTIME_DIR
fi
install -d -m 700 "$XDG_RUNTIME_DIR"
session_data="$XDG_RUNTIME_DIR/glue-session-data"
rm -rf "$session_data"
install -d -m 700 "$session_data/xsessions" "$session_data/wayland-sessions"
# Preserve normal XDG data (GTK schemas, icons, D-Bus services, fonts) without
# exposing the session files shipped by desktop packages a second time.
for data in /usr/share/*; do
    [ -e "$data" ] || continue
    case "${{data##*/}}" in xsessions|wayland-sessions) continue ;; esac
    ln -s "$data" "$session_data/${{data##*/}}"
done
for entry in /usr/share/glue/sessions/*.desktop; do
    [ -f "$entry" ] || continue
    kind=wayland-sessions
    grep -qx 'X-Glue-SessionType=x11' "$entry" && kind=xsessions
    ln -s "$entry" "$session_data/$kind/${{entry##*/}}"
done

export XDG_DATA_DIRS="$session_data"
export GTK_USE_PORTAL=0
export GDK_DEBUG=no-portals
exec dbus-run-session cage -s -mlast -d -- regreet
"""

_SESSIONS_DIR = "/usr/share/glue/sessions"
_WRAPPER_DIR = "/usr/local/bin"

_POLKIT_AGENT = "/usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1"

# Poll for the compositor's Wayland socket, then start the chosen shell/bar
# inside the same D-Bus session. Runs in the background of the wrapper's
# inner (post-dbus) stage; gives up quietly after ~30s.
_SHELL_AUTOSTART_TEMPLATE = """\
    (
        tries=0
        while [ "$tries" -lt 150 ]; do
            for s in "${{XDG_RUNTIME_DIR:-/run/user/$(id -u)}}"/wayland-*; do
                if [ -S "$s" ]; then
                    WAYLAND_DISPLAY="${{s##*/}}"; export WAYLAND_DISPLAY
                    exec {shell_cmd}
                fi
            done
            sleep 0.2
            tries=$((tries + 1))
        done
    ) &
"""

# The startx bootstrap for X11 sessions: greetd/tuigreet is a TUI greeter and
# never starts an X server, so the wrapper re-execs itself under startx first
# (same trick as the packaged nvwm-session wrapper).
_X11_BOOTSTRAP = """\
if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ] && [ -z "${GLUE_XSTARTED:-}" ]; then
    GLUE_XSTARTED=1; export GLUE_XSTARTED
    exec startx "$0" -- "vt${XDG_VTNR:-1}"
fi
"""

# Software rendering is a LAST resort: unconditionally allowing it let wlroots
# fall back to llvmpipe on machines whose GPU merely mis-probed once — the
# session "worked" but every client (quickshell most visibly) burned half a
# core repainting on the CPU. Only allow the fallback when the machine truly
# has no DRM render node (VM without virgl, exotic GPU).
_WAYLAND_SOFTWARE_FALLBACK = """\
if ! ls /dev/dri/renderD* >/dev/null 2>&1; then
    export WLR_RENDERER_ALLOW_SOFTWARE=1
fi
"""

# xdg-desktop-portal picks its backends from
# /usr/share/xdg-desktop-portal/<desktop>-portals.conf, <desktop> being the
# wrapper's XDG_CURRENT_DESKTOP lowercased. A bare Wayland WM has no such
# file, so screen sharing and file dialogs silently fail; gtk covers the
# general interfaces, wlr the screencast/screenshot ones. DEs ship their own.
_PORTAL_CONF_DIR = "/usr/share/xdg-desktop-portal"
_PORTAL_CONF_CONTENT = """\
# {desktop}-portals.conf — written by the Glue Linux installer.
[preferred]
default=gtk
org.freedesktop.impl.portal.ScreenCast=wlr
org.freedesktop.impl.portal.Screenshot=wlr
"""


def _portal_file(session: Session) -> Optional[PlannedFile]:
    """Portal backend selection for a Wayland WM session, None otherwise.
    The file name uses the exact XDG_CURRENT_DESKTOP of the wrapper."""
    if session.kind != "wm" or session.session_type != "wayland":
        return None
    desktop = session.desktop or session.id
    if desktop != desktop.lower():
        raise ValueError(
            f"session '{session.id}': portal config needs a lowercase desktop "
            f"name, got '{desktop}'")
    return PlannedFile(
        path=f"{_PORTAL_CONF_DIR}/{desktop}-portals.conf",
        content=_PORTAL_CONF_CONTENT.format(desktop=desktop), mode=0o644,
    )


# XWayland note (learned the hard way): NEVER export DISPLAY before the
# compositor starts — wlroots compositors and mutter auto-detect a set
# DISPLAY as "run nested inside X11" and die with "Failed to open xcb
# connection / couldn't create backend" on a real VT. X11 apps work through
# each compositor's OWN integration instead: built-in XWayland (gluewc,
# via xorg-xwayland) or the compositor managing its own (mutter/GNOME).


def _session_wrapper_content(session: Session, shell_cmd: Optional[str]) -> str:
    """Login-session wrapper: D-Bus session bus + PipeWire audio + the WM/DE
    (plus startx for X11 sessions). One generated file per selected session."""
    cmd = session.exec or session.id
    shell_block = (
        _SHELL_AUTOSTART_TEMPLATE.format(shell_cmd=shell_cmd)
        if shell_cmd else ""
    )
    x11_block = _X11_BOOTSTRAP if session.session_type == "x11" else ""
    wayland_env = (
        _WAYLAND_SOFTWARE_FALLBACK if session.session_type == "wayland" else ""
    )
    # Only WMs: KDE/GNOME start their own polkit agent, XFCE/Cinnamon get one
    # via xdg autostart; a bare WM has nothing unless the wrapper starts it.
    polkit_block = (
        f"    [ -x {_POLKIT_AGENT} ] && {_POLKIT_AGENT} &\n"
        if session.kind == "wm" else ""
    )
    welcome_block = (
        "    command -v glue-welcome >/dev/null 2>&1 && glue-welcome --autostart &\n"
        if session.kind == "wm" else ""
    )
    desktop = session.desktop or session.id
    return f"""\
#!/bin/sh
# glue-session-{session.id} — generated by the Glue Linux installer.
# Brings up {'X, ' if session.session_type == 'x11' else ''}a D-Bus session bus and PipeWire audio, then starts the session.

if [ "${{1:-}}" = "--inner" ]; then
    command -v pipewire       >/dev/null 2>&1 && pipewire &
    command -v wireplumber    >/dev/null 2>&1 && wireplumber &
    command -v pipewire-pulse >/dev/null 2>&1 && pipewire-pulse &
{polkit_block}{welcome_block}{shell_block}    exec {cmd}
fi

{x11_block}export XDG_CURRENT_DESKTOP={desktop}
export XDG_SESSION_DESKTOP={desktop}
export XDG_SESSION_TYPE={session.session_type}
export XCURSOR_THEME=Adwaita
export XCURSOR_SIZE=24
{wayland_env}exec dbus-run-session -- sh -l "$0" --inner
"""


def _session_desktop_content(session: Session, shell_name: Optional[str]) -> str:
    name = session.name if not shell_name else f"{session.name} + {shell_name}"
    return f"""\
[Desktop Entry]
Name={name}
Comment={session.description}
Exec={_WRAPPER_DIR}/glue-session-{session.id}
Type=Application
DesktopNames={session.desktop or session.id}
X-Glue-SessionType={session.session_type}
"""


def _greeter_files(
    sessions: List[Session], shell_choice: Dict[str, str], shell_map: Dict,
) -> List[PlannedFile]:
    """ReGreet profile plus one login wrapper and entry per selected session."""
    files: List[PlannedFile] = [
        PlannedFile(
            path="/etc/greetd/config.toml",
            content=_GREETD_CONFIG_CONTENT, mode=0o644,
        ),
        PlannedFile(
            path="/etc/greetd/regreet.toml",
            content=_REGREET_CONFIG_CONTENT, mode=0o644,
        ),
        PlannedFile(
            path="/etc/greetd/regreet.css",
            content=_REGREET_CSS_CONTENT, mode=0o644,
        ),
        PlannedFile(
            path=f"{_WRAPPER_DIR}/glue-greeter",
            content=_GREETER_WRAPPER_CONTENT, mode=0o755,
        ),
        PlannedFile(
            path="/etc/glue/session.conf",
            content=("[session]\n" + "".join(
                f"shell.{session_id}={shell_id}\n"
                for session_id, shell_id in sorted(shell_choice.items()))),
            mode=0o644,
        ),
    ]
    for session in sessions:
        shell = shell_map.get(shell_choice.get(session.id))
        files.append(PlannedFile(
            path=f"{_WRAPPER_DIR}/glue-session-{session.id}",
            content=_session_wrapper_content(
                session, shell.exec if shell else None,
            ),
            mode=0o755,
        ))
        files.append(PlannedFile(
            path=f"{_SESSIONS_DIR}/{session.id}.desktop",
            content=_session_desktop_content(
                session, shell.name if shell else None,
            ),
            mode=0o644,
        ))
        portal = _portal_file(session)
        if portal is not None:
            files.append(portal)
    return files
