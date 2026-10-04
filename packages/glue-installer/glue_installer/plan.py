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

from typing import List

from glue_installer.catalog import Catalog
from glue_installer.plan_files import (  # noqa: F401
    _GAMING_ENV_CONTENT, _PRIME_RUN_CONTENT, _TARGET_PACMAN_CONF,
    _build_target_pacman_conf)
from glue_installer.plan_greeter import _greeter_files
from glue_installer.plan_session import session_runtime_packages
from glue_installer.swap import ZRAMEN_CONF, SwapPlan, zramen_conf
# Re-exported: callers import these from glue_installer.plan.
from glue_installer.plan_types import (  # noqa: F401
    InstallPlan, PlanError, PlannedFile, Selection)


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

# Quiet the kernel console on the installed system (ported from the proven
# shell installer): stops kernel/udev log lines painting over the greeter's VT.
# Written ONLY on server-like installs (no sessions, no gaming): everywhere
# else glue-settings ships the same key in /usr/lib/sysctl.d/70-glue.conf and
# no sysctl key may be defined in both places at once.
_SYSCTL_QUIET_CONTENT = "kernel.printk = 3 3 3 3\n"

_BASELINE_FILES: List[PlannedFile] = [
    PlannedFile(path="/etc/skel/.bashrc", content=_BASHRC_CONTENT, mode=0o644),
    PlannedFile(path="/etc/skel/.zshrc", content=_ZSHRC_CONTENT, mode=0o644),
]


# Rule 11: complete Artix base on every install (the proven basestrap set:
# base = coreutils/pacman, linux-firmware = Wi-Fi/GPU blobs, keyrings/
# mirrorlists so the installed system can update).
_ALWAYS_PACKAGES = frozenset({
    "base", "base-devel", "linux-firmware",
    "sudo", "nano", "vim", "git", "wget",
    "artix-keyring", "artix-mirrorlist",
    "cachyos-keyring", "cachyos-mirrorlist",
    "wpa_supplicant", "openresolv",
    # os-prober is NOT installed on the target: it runs on the live side only
    # (iso-profile) and its results become /etc/glue/boot.d/*.conf
    # (glue_installer.osdetect, roadmap 3.4) that glue-boot-update keeps.
    # NTP client: the live clock is synced before install (run_ui.sync_clock);
    # openntpd keeps the INSTALLED system's clock right from first boot on.
    "openntpd",
    "glue-branding",
    # pins the virtual `initramfs` dep (4 providers) to what our GRUB flow
    # and the artix boot chain actually use
    "mkinitcpio",
})

# Rule 12: sessions need a seat/session stack (dbus+elogind as services) plus
# X/Xwayland, audio and fonts; a minimal install stays lean. seatd is ABSENT:
# elogind does seats and seatd-<init> CONFLICTS with elogind-<init> on Artix.
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
    # universal terminal: several WM default configs (gluewc notably) bind
    # alacritty out of the box — every session gets a working terminal keybind
    "alacritty", "ttf-liberation",   # liberation = the font of the shipped alacritty.toml
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
    # Glue Apps (store + system updates) and Glue Welcome (shortcuts, system,
    # settings) are on every graphical installation; the store backends
    # follow the app-store support toggle.
    "glue-apps",
    "glue-welcome",
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
    "thermald": "thermald",
    "zramen": "zramen",
}

# Per-init zramen config paths live in swap.py (single definition).
_ZRAMEN_CONF = ZRAMEN_CONF

# Rule 15 (roadmap 1.4): sched_ext CPU scheduler on gaming installs.
# scx-scheds is a vendored [glue] package that ships its own dinit/runit/
# openrc `scx` service scripts (like glue-settings), so it deliberately has
# NO _SERVICE_PKG_BASE entry. The package default (/etc/default/scx) is
# scx_lavd --performance; any other scheduler overrides that file below.
# "none" keeps the kernel's built-in EEVDF scheduler: no package, no service.
_SCX_DEFAULT_SCHEDULER = "scx_lavd"
_SCX_KNOWN_SCHEDULERS = frozenset({"scx_lavd", "scx_bpfland", "none"})
_SCX_CONF_TEMPLATE = """\
# /etc/default/scx — written by the Glue Linux installer.
SCX_SCHEDULER={scheduler}
SCX_FLAGS=
"""


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

def resolve_plan(
    catalog: Catalog, selection: Selection, gpu_vendors=None, cpu_v3: bool = False,
    is_laptop: bool = False, cpu_vendor_id: str = "other",
    amd_pstate_active: bool = False,
    swap: SwapPlan | None = None, ram_bytes: int | None = None,
    hw=None,
) -> InstallPlan:
    """Resolve a Selection against a Catalog into a deterministic InstallPlan.

    gpu_vendors: detected GPU vendor set (gpu.detect_gpu_vendors()); used when
    gaming is enabled to pin explicit Vulkan drivers (catalog gpu_autodetect).
    None keeps the plan free of GPU packages (pure/unit-test contexts).

    hw: optional hw_compat.HardwareProfile (NVIDIA generation, Broadcom wl,
    firmware, microcode); None leaves the plan exactly as without it.

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

    # Rule 7: ReGreet under Cage, with Tuigreet fallback when there is no KMS.
    # The greeter profile (config.toml on VT7 + per-session wrappers with
    # D-Bus and PipeWire) is generated below as planned files.
    if selection.session_ids:
        packages.add("accountsservice")
        packages.add("cage")
        packages.add("greetd")
        packages.add("greetd-regreet")
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

    # Rule 15: sched_ext scheduler (roadmap 1.4) — gaming installs only
    if selection.gaming:
        allowed = ({o.id for o in catalog.gaming.schedulers}
                   if catalog.gaming.schedulers else _SCX_KNOWN_SCHEDULERS)
        if selection.scheduler not in allowed:
            raise PlanError(f"Unknown scheduler: '{selection.scheduler}'")
        if selection.scheduler != "none":
            packages.add("scx-scheds")
            services.add("scx")

    # Rule 17 (roadmap 1.11): thermal management and laptop power tuning.
    # thermald: Intel-only thermal daemon (AMD has its own firmware path).
    # power-profiles-daemon + lm_sensors + glue-sensors-detect: on any laptop
    # (BAT* present), even without sessions or gaming (Rule 10 handles init pkgs).
    if cpu_vendor_id == "intel":
        packages.add("thermald")
        services.add("thermald")
    if is_laptop:
        packages.add("power-profiles-daemon")
        services.add("power-profiles-daemon")
        packages.add("lm_sensors")
        services.add("glue-sensors-detect")

    # Rule 13: glue-settings (CachyOS tuning without systemd) on every desktop
    # or gaming install, with its glue-tuning oneshot (THP defaults at boot).
    # The package is arch=any and ships the dinit/runit/openrc scripts itself,
    # so it deliberately has NO _SERVICE_PKG_BASE entry (Rule 10 skips it).
    tuned = bool(selection.session_ids) or selection.gaming
    if tuned:
        packages.add("glue-settings")
        services.add("glue-tuning")

    # Rule 14: zram via zramen on every install (roadmap 1.2).
    # zramen-{init_id} is pulled in automatically by Rule 10 below.
    packages.add("zramen")
    services.add("zramen")

    # Rule 8: fastfetch always
    packages.add("fastfetch")

    # Rule 9: the installed system always gets network connectivity
    packages.add("networkmanager")
    services.add("NetworkManager")

    # Rule 11: complete base system on every install
    packages.update(_ALWAYS_PACKAGES)

    # Rule 12: seat/session stack always; the graphical desktop set only
    # with a session. openntpd keeps the clock NTP-synced from first boot.
    services.update({"dbus", "elogind", "openntpd"})
    if selection.session_ids:
        packages.update(_DESKTOP_PACKAGES)
        # performance modes need the daemon RUNNING, not just installed —
        # D-Bus activation alone is unreliable without systemd
        services.add("power-profiles-daemon")
        # bar runtime of WM sessions (roadmap 5.9): upower, brightnessctl,
        # playerctl, libnotify; wl-clipboard + wlr/gtk portals on Wayland
        packages.update(session_runtime_packages(
            session_map[sid] for sid in selection.session_ids))
        # Per-vendor GPU driver stack (mesa always; NVIDIA module+userspace
        # when detected) — sessions need working EGL, not just the gaming mode
        if gpu_vendors is not None:
            from glue_installer.gpu import session_gpu_packages
            packages.update(session_gpu_packages(gpu_vendors))

    # Rule 18 (roadmap 10.8): hardware compatibility packages and warnings
    if hw is not None:
        from glue_installer.hw_compat import apply_hw
        warnings.extend(apply_hw(
            packages, hw, bool(selection.session_ids), selection.gaming))

    # Rule 10: every enabled service needs its init-specific service package
    # (Artix ships scripts separately: greetd-dinit, bluez-runit, ...).
    for svc in sorted(services):
        base = _SERVICE_PKG_BASE.get(svc)
        if base is not None:
            packages.add(f"{base}-{selection.init_id}")

    # Rule 16 (roadmap 1.7): [cachyos-v3] repo when CPU supports x86-64-v3
    # and the CachyOS kernel is selected (the v3 packages are built for it).
    v3_active = cpu_v3 and selection.kernel_id == "linux-cachyos"
    if v3_active:
        packages.add("cachyos-v3-mirrorlist")

    # Greeter profile files (config.toml + per-session wrapper/entry)
    files: List[PlannedFile] = [
        PlannedFile(path="/etc/pacman.conf",
                    content=_build_target_pacman_conf(v3_active), mode=0o644),
    ] + list(_BASELINE_FILES)
    if not tuned:
        # server-like install has no glue-settings — keep the quiet console
        # sysctl as a standalone fallback file
        files.append(PlannedFile(
            path="/etc/sysctl.d/20-glue-quiet.conf",
            content=_SYSCTL_QUIET_CONTENT, mode=0o644,
        ))
    if selection.session_ids:
        selected_sessions = [session_map[sid] for sid in selection.session_ids]
        files.extend(_greeter_files(
            selected_sessions, selection.shell_choice, shell_map,
        ))
        store_enabled = "app-store" in selection.support_ids
        files.append(PlannedFile(
            path="/etc/glue/apps.conf",
            content=("[apps]\n"
                     "theme=glue\n"
                     "autostart=1\n"
                     f"store={int(store_enabled)}\n"
                     "aur_warning_seen=0\n"
                     "language=en\n"),
            mode=0o644,
        ))
    if selection.gaming:
        files.append(PlannedFile(
            path="/etc/profile.d/glue-gaming.sh",
            content=_GAMING_ENV_CONTENT, mode=0o644,
        ))
    if hw is not None:
        from glue_installer.hw_compat import hw_files, proprietary_nvidia
        files.extend(hw_files(hw))
    if gpu_vendors and "nvidia" in gpu_vendors and (
            selection.gaming or selection.session_ids) and (
            hw is None or proprietary_nvidia(hw)):
        files.append(PlannedFile(
            path="/usr/local/bin/prime-run",
            content=_PRIME_RUN_CONTENT, mode=0o755,
        ))

    if (selection.gaming and selection.scheduler != "none"
            and selection.scheduler != _SCX_DEFAULT_SCHEDULER):
        files.append(PlannedFile(
            path="/etc/default/scx",
            content=_SCX_CONF_TEMPLATE.format(scheduler=selection.scheduler),
            mode=0o644,
        ))

    zramen = None
    if swap is not None and ram_bytes:
        zramen = zramen_conf(swap, ram_bytes, selection.init_id)
    if zramen is None and selection.init_id in _ZRAMEN_CONF:
        zramen = _ZRAMEN_CONF[selection.init_id]
    if zramen is not None:
        zramen_path, zramen_content = zramen
        files.append(PlannedFile(path=zramen_path, content=zramen_content, mode=0o644))

    # Kernel cmdline extras land in /boot/limine.conf (limine.py)
    cmdline_extra = {"amd_pstate=active"} if amd_pstate_active else set()

    return InstallPlan(
        packages=sorted(packages),
        services=sorted(services),
        files=sorted(files, key=lambda f: f.path),
        warnings=warnings,
        cmdline_extra=sorted(cmdline_extra),
    )
