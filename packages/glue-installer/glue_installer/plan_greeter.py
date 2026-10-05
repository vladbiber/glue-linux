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
skip_selection = false

[background]
path = "/usr/share/backgrounds/glue/wallpaper.png"
fit = "Cover"

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

"""

_REGREET_CSS_CONTENT = """\
/* Glue Linux ReGreet theme — neutral glass card over the default wallpaper. */
* {
    color: #25272B;
}

window,
.background {
    background-color: transparent;
}

.view {
    background-color: #FFFFFF;
}

frame {
    background-color: rgba(255, 255, 255, 0.92);
    border: 1px solid #D5D7DA;
    border-radius: 16px;
}

button,
button * {
    color: #FFFFFF;
}

button {
    background: #B5484D;
    border: 1px solid #96363C;
    border-radius: 10px;
}

button:hover,
button:focus {
    background: #96363C;
}

entry,
combobox button {
    color: #25272B;
    background: #FFFFFF;
    border: 1px solid #C9CDD2;
    border-radius: 10px;
}

combobox button * {
    color: #25272B;
}

entry:focus,
combobox button:focus {
    border-color: #B5484D;
}
"""

_TUIGREET_COMMAND = (
    "/usr/bin/tuigreet --remember --remember-session --time "
    "--greeting 'Glue Linux' --sessions /usr/share/glue/sessions "
    "--theme 'border=gray;text=white;prompt=red;time=white;"
    "action=red;button=red;container=black;input=white'"
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
_WALLPAPER_INIT = "/usr/bin/glue-wallpaper-init"

# A socket counts only while a compositor holds its .lock: one left over by a
# crashed session would get "Connection refused" and kill the client.
# Poll for the compositor's Wayland socket, then start the chosen shell/bar
# inside the same D-Bus session. Runs in the background of the wrapper's
# inner (post-dbus) stage; gives up quietly after ~30s.
_SHELL_AUTOSTART_TEMPLATE = """\
    (
        tries=0
        while [ "$tries" -lt 150 ]; do
            for s in "${{XDG_RUNTIME_DIR:-/run/user/$(id -u)}}"/wayland-*; do
                if [ -S "$s" ] && ! flock -n "$s.lock" true 2>/dev/null; then
                    WAYLAND_DISPLAY="${{s##*/}}"; export WAYLAND_DISPLAY
                    find_display
                    # a shell that dies within 10 s is started again (5 times)
                    sleep 1
                    fails=0
                    while [ "$fails" -lt 5 ]; do
                        started=$(date +%s)
                        {shell_cmd}
                        if [ $(($(date +%s) - started)) -ge 10 ]; then fails=0
                        else fails=$((fails + 1)); fi
                        sleep 1
                    done
                    exit 0
                fi
            done
            sleep 0.2
            tries=$((tries + 1))
        done
    ) &
"""

_WAIT_WAYLAND_FUNC = """\
# The compositor's Xwayland display, from the X lock file holding its pid:
# apps started from the bar or Welcome need it to open X11 windows.
find_display() {
    for l in /tmp/.X*-lock; do
        [ -f "$l" ] || continue
        p=$(tr -cd 0-9 < "$l")
        [ "$(cat "/proc/$p/comm" 2>/dev/null)" = gluewc ] && [ -O "/proc/$p" ] || continue
        n=${l#/tmp/.X}
        DISPLAY=":${n%-lock}"; export DISPLAY
        return 0
    done
    return 1
}

wait_wayland() {
    tries=0
    while [ "$tries" -lt 150 ]; do
        for s in "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"/wayland-*; do
            if [ -S "$s" ] && ! flock -n "$s.lock" true 2>/dev/null; then
                WAYLAND_DISPLAY="${s##*/}"; export WAYLAND_DISPLAY
                find_display
                return 0
            fi
        done
        sleep 0.2
        tries=$((tries + 1))
    done
    return 1
}

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
# fall back to llvmpipe on machines whose GPU merely mis-probed once - the
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


# Default apps for the bare WM sessions, read only when XDG_CURRENT_DESKTOP
# is that desktop: KDE, GNOME and Cinnamon installed next to them otherwise
# decide (Nemo for folders, feh for pictures, nothing for videos).
_MIME_DEFAULTS = (
    ("org.gnome.Nautilus.desktop", ("inode/directory",)),
    ("org.gnome.Loupe.desktop", tuple(f"image/{t}" for t in (
        "png", "jpeg", "gif", "webp", "bmp", "tiff", "svg+xml", "avif", "heic"))),
    ("io.github.celluloid_player.Celluloid.desktop", tuple(f"video/{t}" for t in (
        "mp4", "x-matroska", "webm", "quicktime", "x-msvideo", "mpeg", "ogg"))),
    ("org.gnome.Papers.desktop", ("application/pdf",)),
    ("firefox.desktop", ("text/html", "x-scheme-handler/http", "x-scheme-handler/https")),
)


def _mimeapps_file(session: Session) -> Optional[PlannedFile]:
    if session.kind != "wm":
        return None
    desktop = (session.desktop or session.id).lower()
    lines = ["# Glue Linux default apps for this session", "[Default Applications]"]
    for app, types in _MIME_DEFAULTS:
        lines += [f"{t}={app}" for t in types]
    return PlannedFile(path=f"/etc/xdg/{desktop}-mimeapps.list",
                       content="\n".join(lines) + "\n", mode=0o644)


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
# compositor starts - wlroots compositors and mutter auto-detect a set
# DISPLAY as "run nested inside X11" and die with "Failed to open xcb
# connection / couldn't create backend" on a real VT. X11 apps work through
# each compositor's OWN integration instead: built-in XWayland (gluewc,
# via xorg-xwayland) or the compositor managing its own (mutter/GNOME).


def _session_wrapper_content(
    session: Session, shell_cmd: Optional[str], shell_id: Optional[str] = None,
) -> str:
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
    # Wayland clients started before the compositor has no display to open:
    # wait for its socket first (X11 sessions already run inside startx)
    wait = "wait_wayland && " if session.session_type == "wayland" else ""
    # the agent is a GTK window too: started before the compositor it exits
    # at once and every pkexec (Glue Apps, updates) fails without a prompt
    polkit_block = (
        f"    ({wait}[ -x {_POLKIT_AGENT} ] && exec {_POLKIT_AGENT}) &\n"
        if session.kind == "wm" else ""
    )
    welcome_block = (
        f"    ({wait}command -v glue-welcome >/dev/null 2>&1 && "
        "exec glue-welcome --autostart) &\n"
        if session.kind == "wm" else ""
    )
    wallpaper_block = (
        f"    ({wait}[ -x {_WALLPAPER_INIT} ] && "
        f"exec {_WALLPAPER_INIT} {session.id} {shell_id or 'none'}) &\n"
    )
    wait_func = _WAIT_WAYLAND_FUNC if session.session_type == "wayland" else ""
    desktop = session.desktop or session.id
    return f"""\
#!/bin/sh
# glue-session-{session.id} — generated by the Glue Linux installer.
# Brings up {'X, ' if session.session_type == 'x11' else ''}a D-Bus session bus and PipeWire audio, then starts the session.

{wait_func}if [ "${{1:-}}" = "--inner" ]; then
    command -v pipewire       >/dev/null 2>&1 && pipewire &
    command -v wireplumber    >/dev/null 2>&1 && wireplumber &
    command -v pipewire-pulse >/dev/null 2>&1 && pipewire-pulse &
{polkit_block}{welcome_block}{wallpaper_block}{shell_block}    exec {cmd}
fi

{x11_block}export GLUE_SESSION={session.id}
export XDG_CURRENT_DESKTOP={desktop}
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
                shell.id if shell else None,
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
        mimeapps = _mimeapps_file(session)
        if mimeapps is not None and all(f.path != mimeapps.path for f in files):
            files.append(mimeapps)
        portal = _portal_file(session)
        # gluewc and gluewc-noctalia share one desktop name and one file
        if portal is not None and all(f.path != portal.path for f in files):
            files.append(portal)
    return files
