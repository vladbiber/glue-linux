"""
Selection model and plan resolver for Glue Linux installer.

Pure logic layer: no I/O, no subprocess, no filesystem access.
Turns a Catalog + Selection into a deterministic InstallPlan.

Warning order (documented, fixed):
  1. "Multiple sessions installed — pick your session at the login screen"
     (only when >= 2 sessions selected)
  2. "GPU driver auto-detection will run on the target system during install"
     (only when gaming=True and catalog.gaming.gpu_autodetect is True)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from glue_installer.catalog import Catalog, Session


class PlanError(Exception):
    """Raised for any selection validation failure. Message names the offending field/id."""


@dataclass
class Selection:
    kernel_id: str
    init_id: str
    session_ids: List[str]
    shell_choice: Dict[str, str]  # session_id -> shell_id, required when shell_choices non-empty
    support_ids: List[str]
    gaming: bool
    minimal: bool


@dataclass
class PlannedFile:
    path: str
    content: str
    mode: int  # e.g. 0o644


@dataclass
class InstallPlan:
    packages: List[str]    # deduplicated, sorted alphabetically
    services: List[str]    # deduplicated, sorted alphabetically
    files: List[PlannedFile]  # sorted by path
    warnings: List[str]    # in the fixed order documented above


# ---------------------------------------------------------------------------
# Baseline file contents
# ---------------------------------------------------------------------------

_BASHRC_CONTENT = """\
# ~/.bashrc — Glue Linux
# Run fastfetch on interactive shell start
case $- in
    *i*) command -v fastfetch >/dev/null && fastfetch ;;
esac
"""

_ZSHRC_CONTENT = """\
# ~/.zshrc — Glue Linux
# Run fastfetch on interactive shell start
[[ -o interactive ]] && command -v fastfetch >/dev/null && fastfetch
"""

# The installed system's /etc/pacman.conf. basestrap -C uses the LIVE conf
# (which also has the local file:// [glue] repo — that path does not exist
# on the installed disk), so the target gets its own conf with the online
# repos only. cachyos-keyring/cachyos-mirrorlist are in _ALWAYS_PACKAGES so
# these Includes resolve and future `pacman -Syu` keeps seeing the kernel repo.
_TARGET_PACMAN_CONF = """\
# /etc/pacman.conf — Glue Linux (installed system)
[options]
HoldPkg     = pacman glibc
Architecture = auto
CheckSpace
ParallelDownloads = 5
SigLevel    = Required DatabaseOptional
LocalFileSigLevel = Optional

[system]
Include = /etc/pacman.d/mirrorlist

[world]
Include = /etc/pacman.d/mirrorlist

[galaxy]
Include = /etc/pacman.d/mirrorlist

[lib32]
Include = /etc/pacman.d/mirrorlist

[cachyos]
Include = /etc/pacman.d/cachyos-mirrorlist
"""

# Quiet the kernel console on the installed system (ported from the proven
# shell installer): stops kernel/udev log lines painting over the greeter's VT.
_SYSCTL_QUIET_CONTENT = "kernel.printk = 3 3 3 3\n"

# Gaming installs only. The NVIDIA driver's default shader disk cache is
# small and auto-purged, so big games (CS2/CS:GO most visibly) recompile
# their Vulkan/GL shaders on almost every launch — the endless "Processing
# Vulkan shaders" and in-game stutter. Keep the cache and never purge it.
# Harmless no-ops on AMD/Intel (mesa ignores __GL_* variables).
_GAMING_ENV_CONTENT = """\
# /etc/profile.d/glue-gaming.sh — Glue Linux gaming defaults.
export __GL_SHADER_DISK_CACHE=1
export __GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1
"""

# Hybrid (Optimus) laptops enumerate the Intel iGPU first, so Steam games
# default to it — slow shader compiles and unstable frames on the wrong GPU.
# Arch ships this wrapper as the nvidia-prime package; Artix has no such
# package, so the installer writes the identical script itself.
# Steam usage: set a game's Launch Options to:  prime-run %command%
_PRIME_RUN_CONTENT = """\
#!/bin/sh
# prime-run — run a program on the NVIDIA dGPU (written by the Glue
# Linux installer; Artix has no nvidia-prime package).
__NV_PRIME_RENDER_OFFLOAD=1
__NV_PRIME_RENDER_OFFLOAD_PROVIDER=NVIDIA-G0
__GLX_VENDOR_LIBRARY_NAME=nvidia
__VK_LAYER_NV_optimus=NVIDIA_only
export __NV_PRIME_RENDER_OFFLOAD __NV_PRIME_RENDER_OFFLOAD_PROVIDER \\
    __GLX_VENDOR_LIBRARY_NAME __VK_LAYER_NV_optimus
exec "$@"
"""

_BASELINE_FILES: List[PlannedFile] = [
    PlannedFile(path="/etc/pacman.conf", content=_TARGET_PACMAN_CONF, mode=0o644),
    PlannedFile(path="/etc/skel/.bashrc", content=_BASHRC_CONTENT, mode=0o644),
    PlannedFile(path="/etc/skel/.zshrc", content=_ZSHRC_CONTENT, mode=0o644),
    PlannedFile(
        path="/etc/sysctl.d/20-glue-quiet.conf",
        content=_SYSCTL_QUIET_CONTENT, mode=0o644,
    ),
]

# ---------------------------------------------------------------------------
# Greeter (greetd + tuigreet) — ported from the proven shell installer
# ---------------------------------------------------------------------------

# The greeter runs on a DEDICATED VT (tty7), away from the kernel console and
# boot messages (tty1) and from the login gettys (tty1-6). This is what makes
# tuigreet's input stay put instead of being overwritten — the gettys on
# tty1-6 stay untouched.
_GREETD_CONFIG_CONTENT = """\
# /etc/greetd/config.toml — written by the Glue Linux installer.
[terminal]
vt = 7

[default_session]
command = "tuigreet --remember --remember-session --time --sessions /usr/share/glue/sessions --theme 'border=yellow;text=white;prompt=yellow;time=yellow;action=yellow;button=yellow;container=black;input=white'"
user = "greeter"
"""

_SESSIONS_DIR = "/usr/share/glue/sessions"
_WRAPPER_DIR = "/usr/local/bin"

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
# (same trick as the packaged apeturewm-/nvwm-/atomwm-session wrappers).
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

# XWayland note (learned the hard way): NEVER export DISPLAY before the
# compositor starts — wlroots/dwl (mango) and niri both auto-detect a set
# DISPLAY as "run nested inside X11" and die with "Failed to open xcb
# connection / couldn't create backend" on a real VT. X11 apps work through
# each compositor's OWN integration instead: mango + sway have built-in
# XWayland (xorg-xwayland + xcb-util-wm installed), niri auto-spawns
# xwayland-satellite from PATH, mutter (GNOME) manages its own XWayland.


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
    desktop = session.desktop or session.id
    return f"""\
#!/bin/sh
# glue-session-{session.id} — generated by the Glue Linux installer.
# Brings up {'X, ' if session.session_type == 'x11' else ''}a D-Bus session bus and PipeWire audio, then starts the session.

if [ "${{1:-}}" = "--inner" ]; then
    command -v pipewire       >/dev/null 2>&1 && pipewire &
    command -v wireplumber    >/dev/null 2>&1 && wireplumber &
    command -v pipewire-pulse >/dev/null 2>&1 && pipewire-pulse &
{shell_block}    exec {cmd}
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
"""


def _greeter_files(
    sessions: List[Session], shell_choice: Dict[str, str], shell_map: Dict,
) -> List[PlannedFile]:
    """Greeter profile: /etc/greetd/config.toml plus, per selected session, a
    wrapper script and the session entry tuigreet lists from _SESSIONS_DIR."""
    files: List[PlannedFile] = [
        PlannedFile(
            path="/etc/greetd/config.toml",
            content=_GREETD_CONFIG_CONTENT, mode=0o644,
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
    return files

# Rule 11: every install gets a complete Artix base system. This mirrors the
# proven basestrap set of the original shell installer — without `base` the
# target has no coreutils/pacman, without linux-firmware no Wi-Fi/GPU blobs,
# without the keyrings/mirrorlists the installed system cannot update.
_ALWAYS_PACKAGES = frozenset({
    "base", "base-devel", "linux-firmware",
    "sudo", "nano", "vim", "git", "wget",
    "artix-keyring", "artix-mirrorlist",
    "cachyos-keyring", "cachyos-mirrorlist",
    "wpa_supplicant", "openresolv",
    # os-prober: dual-boot entries (Windows, other Linux) in the GRUB menu.
    # The live USB's own entries that it also clones are stripped afterwards
    # by the grub_filter bootloader step (see glue_installer.grub_filter).
    "os-prober",
    # NTP client: the live clock is synced before install (run_ui.sync_clock);
    # openntpd keeps the INSTALLED system's clock right from first boot on.
    "openntpd",
    "glue-branding",
    # pins the virtual `initramfs` dep (4 providers) to what our GRUB flow
    # and the artix boot chain actually use
    "mkinitcpio",
})

# Rule 12: sessions need a seat/session stack (dbus+elogind, enabled as
# services) plus X/Xwayland, audio, and fonts. Applied when any session is
# selected; a minimal install stays lean. seatd is deliberately ABSENT:
# elogind provides seat management and seatd-<init> CONFLICTS with
# elogind-<init> on Artix (verified: basestrap aborts on the pair).
_DESKTOP_PACKAGES = frozenset({
    "xorg-server", "xorg-xinit", "xorg-xrandr", "xorg-xsetroot",
    "xorg-xwayland",
    # explicit GL/EGL userspace — Wayland compositors hard-require it
    "mesa",
    # Qt apps (quickshell shells, KDE bits) must run natively on Wayland;
    # without the platform plugin they fall back to XCB or software paths
    "qt6-wayland",
    # RTKit: gives PipeWire its realtime priorities via D-Bus (activation
    # works without systemd) and silences the mod.rt warning spam on the VT
    "rtkit",
    # universal terminal: several WM default configs (niri notably) bind
    # alacritty out of the box — every session gets a working terminal keybind
    "alacritty",
    "pipewire", "wireplumber", "pipewire-pulse", "pipewire-alsa",
    "noto-fonts", "noto-fonts-emoji", "ttf-dejavu", "ttf-jetbrains-mono",
    "dunst", "dbus",
    # pins the virtual xdg-desktop-portal-impl dep (9 providers otherwise;
    # the gtk portal is the universal wlroots/X fallback implementation)
    "xdg-desktop-portal-gtk",
    # pins the virtual `jack` dep to the pipewire implementation
    "pipewire-jack",
    # performance/balanced/power-saver modes: KDE's battery widget and
    # GNOME's power settings surface these only when the daemon is present
    # (WMs get them via `powerprofilesctl`); pulls upower+polkit as deps
    "power-profiles-daemon",
})

# Maps a service name to the Artix package BASE that ships its init scripts;
# the init-specific package is f"{base}-{init_id}" (Rule 10).
_SERVICE_PKG_BASE = {
    "greetd": "greetd",
    "bluetoothd": "bluez",
    "NetworkManager": "networkmanager",
    "dbus": "dbus",
    "elogind": "elogind",
    "openntpd": "openntpd",
    # verified: -dinit/-runit/-openrc all ship a script named exactly
    # "power-profiles-daemon", so no _INIT_SERVICE_NAMES rename is needed
    "power-profiles-daemon": "power-profiles-daemon",
}


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

def resolve_plan(
    catalog: Catalog, selection: Selection, gpu_vendors=None,
) -> InstallPlan:
    """Resolve a Selection against a Catalog into a deterministic InstallPlan.

    gpu_vendors: detected GPU vendor set (gpu.detect_gpu_vendors()); used when
    gaming is enabled to pin explicit Vulkan drivers (catalog gpu_autodetect).
    None keeps the plan free of GPU packages (pure/unit-test contexts).

    Raises PlanError with a human-readable message naming the offending field/id
    for any validation failure.
    """
    packages: set = set()
    services: set = set()
    warnings: List[str] = []

    kernel_map = {k.id: k for k in catalog.kernels}
    init_map = {i.id: i for i in catalog.inits}
    session_map = {s.id: s for s in catalog.sessions}
    shell_map = {s.id: s for s in catalog.shells}
    support_map = {s.id: s for s in catalog.support}

    # Rule 1: kernel_id
    if selection.kernel_id not in kernel_map:
        raise PlanError(f"Unknown kernel_id: '{selection.kernel_id}'")
    packages.update(kernel_map[selection.kernel_id].packages)

    # Rule 1: init_id
    if selection.init_id not in init_map:
        raise PlanError(f"Unknown init_id: '{selection.init_id}'")
    packages.update(init_map[selection.init_id].packages)

    # Rule 3: minimal constraints — sessions, shell_choice, and gaming must be absent
    if selection.minimal:
        if selection.session_ids:
            raise PlanError("minimal install cannot include sessions")
        if selection.shell_choice:
            raise PlanError("minimal install cannot include sessions/gaming")
        if selection.gaming:
            raise PlanError("minimal install cannot include gaming")

    # Rule 2: duplicate session_ids
    seen_sessions: set = set()
    for sid in selection.session_ids:
        if sid in seen_sessions:
            raise PlanError(f"Duplicate session_id: '{sid}'")
        seen_sessions.add(sid)

    # Rule 2: all session_ids must exist in catalog
    for sid in selection.session_ids:
        if sid not in session_map:
            raise PlanError(f"Unknown session_id: '{sid}'")

    selected_set = set(selection.session_ids)

    # Rule 4 & 5: validate every (session_id, shell_id) entry in shell_choice
    for sc_session_id, sc_shell_id in selection.shell_choice.items():
        if sc_session_id not in session_map:
            raise PlanError(
                f"shell_choice references unknown session_id: '{sc_session_id}'"
            )
        if sc_session_id not in selected_set:
            raise PlanError(
                f"shell_choice references unselected session_id: '{sc_session_id}'"
            )
        if not session_map[sc_session_id].shell_choices:
            raise PlanError(
                f"shell_choice entry for session '{sc_session_id}' which has no shell_choices"
            )
        if sc_shell_id not in shell_map:
            raise PlanError(
                f"shell_choice for session '{sc_session_id}' references unknown shell_id: '{sc_shell_id}'"
            )
        if sc_shell_id not in session_map[sc_session_id].shell_choices:
            raise PlanError(
                f"shell_choice for session '{sc_session_id}': "
                f"shell '{sc_shell_id}' is not in that session's shell_choices"
            )

    # Rule 7 & 4: add session packages/services; require shell_choice for sessions that need it
    for sid in selection.session_ids:
        session = session_map[sid]
        packages.update(session.packages)
        services.update(session.services)
        if session.shell_choices:
            if sid not in selection.shell_choice:
                raise PlanError(
                    f"session '{sid}' requires a shell_choice but none was provided"
                )
            shell_id = selection.shell_choice[sid]
            packages.update(shell_map[shell_id].packages)

    # Rule 7: greetd + tuigreet when any session is selected; warning at 2+.
    # The greeter profile (config.toml on VT7 + per-session wrappers with
    # D-Bus and PipeWire) is generated below as planned files.
    if selection.session_ids:
        packages.add("greetd")
        packages.add("greetd-tuigreet")
        services.add("greetd")
        if len(selection.session_ids) >= 2:
            warnings.append(
                "Multiple sessions installed — pick your session at the login screen"
            )

    # Rule 5: support toggles
    for support_id in selection.support_ids:
        if support_id not in support_map:
            raise PlanError(f"Unknown support_id: '{support_id}'")
        toggle = support_map[support_id]
        packages.update(toggle.packages)
        services.update(toggle.services)

    # Rule 6: gaming
    if selection.gaming:
        packages.update(catalog.gaming.packages)
        services.update(catalog.gaming.services)
        if catalog.gaming.gpu_autodetect and gpu_vendors is not None:
            from glue_installer.gpu import gaming_gpu_packages
            gpu_pkgs = gaming_gpu_packages(gpu_vendors)
            packages.update(gpu_pkgs)
            detected = ", ".join(sorted(gpu_vendors)) if gpu_vendors else "none"
            warnings.append(
                f"GPU detected: {detected} — installing {', '.join(gpu_pkgs)}"
            )

    # Rule 8: fastfetch always
    packages.add("fastfetch")

    # Rule 9: the installed system always gets network connectivity
    packages.add("networkmanager")
    services.add("NetworkManager")

    # Rule 11: complete base system on every install
    packages.update(_ALWAYS_PACKAGES)

    # Rule 12: seat/session stack always (needed for login and any session);
    # the graphical desktop set only when a session was actually selected.
    # openntpd keeps the clock NTP-synced from first boot (every install).
    services.update({"dbus", "elogind", "openntpd"})
    if selection.session_ids:
        packages.update(_DESKTOP_PACKAGES)
        # performance modes need the daemon RUNNING, not just installed —
        # D-Bus activation alone is unreliable without systemd
        services.add("power-profiles-daemon")
        # Per-vendor GPU driver stack (mesa always; NVIDIA module+userspace
        # when detected) — sessions need working EGL, not just the gaming mode
        if gpu_vendors is not None:
            from glue_installer.gpu import session_gpu_packages
            packages.update(session_gpu_packages(gpu_vendors))

    # Rule 10: every enabled service needs its init-specific service package
    # (Artix ships service scripts separately: e.g. greetd-dinit, bluez-runit,
    # networkmanager-openrc). Without these, enabling the service in the chroot
    # points at files that do not exist.
    for svc in sorted(services):
        base = _SERVICE_PKG_BASE.get(svc)
        if base is not None:
            packages.add(f"{base}-{selection.init_id}")

    # Greeter profile files (config.toml + per-session wrapper/entry)
    files: List[PlannedFile] = list(_BASELINE_FILES)
    if selection.session_ids:
        selected_sessions = [session_map[sid] for sid in selection.session_ids]
        files.extend(_greeter_files(
            selected_sessions, selection.shell_choice, shell_map,
        ))
    if selection.gaming:
        files.append(PlannedFile(
            path="/etc/profile.d/glue-gaming.sh",
            content=_GAMING_ENV_CONTENT, mode=0o644,
        ))
    if gpu_vendors and "nvidia" in gpu_vendors and (
            selection.gaming or selection.session_ids):
        files.append(PlannedFile(
            path="/usr/local/bin/prime-run",
            content=_PRIME_RUN_CONTENT, mode=0o755,
        ))

    return InstallPlan(
        packages=sorted(packages),
        services=sorted(services),
        files=sorted(files, key=lambda f: f.path),
        warnings=warnings,
    )
