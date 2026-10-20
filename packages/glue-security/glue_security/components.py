"""Security components and the levels made of them (texts shown in Settings)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

LEVELS = ("basic", "hardened", "super")


@dataclass(frozen=True)
class Component:
    id: str
    name: str
    description: str
    protects: str
    breaks: str
    cost: str
    needs_reboot: bool
    min_level: str = ""     # first level that includes it; "" = in no level

    @property
    def levels(self) -> Tuple[str, ...]:
        if not self.min_level:
            return ()
        return LEVELS[LEVELS.index(self.min_level):]

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "protects": self.protects, "breaks": self.breaks, "cost": self.cost,
            "needs_reboot": self.needs_reboot, "levels": list(self.levels),
        }


COMPONENTS: Tuple[Component, ...] = (
    Component(
        "firewall", "Firewall",
        "Blocks connections that other devices start toward this computer. "
        "Replies to your own traffic still come in and outgoing traffic is never filtered.",
        "Programs listening on the network (file sharing, debug ports, a game "
        "server left running) from other devices on the same Wi-Fi or LAN.",
        "Anything that must accept incoming connections: hosting a game or LAN "
        "server, KDE Connect, Steam Remote Play as the host, SSH into this "
        "computer. Open a port with a rule file in /etc/glue/firewall.d.",
        "None measurable.", False, "basic"),
    Component(
        "apparmor", "AppArmor",
        "Kernel security module that limits what some programs may do, with "
        "the default profiles from the apparmor package only.",
        "Limits the damage a hacked program with a profile can do.",
        "Rarely anything. A program with a strict profile may be refused a "
        "file it needs; the denial shows in the system log.",
        "Negligible.", True, "basic"),
    Component(
        "sysctl-base", "Basic kernel hardening",
        "Hides kernel messages and kernel addresses from normal users, turns "
        "off loading a new kernel at runtime (kexec), and blocks file and link "
        "tricks in shared folders such as /tmp.",
        "Makes local exploits harder and stops /tmp file swap attacks.",
        "dmesg needs sudo. kexec and kdump stay off until the next restart "
        "after turning this off.",
        "None.", False, "basic"),
    Component(
        "updates-notify", "Update reminder",
        "Checks for system updates after login and every 6 hours and shows a "
        "notification that opens Glue Apps. It never installs anything itself.",
        "Running old software with known security holes.",
        "Nothing.",
        "One small check after login and every 6 hours.", False, "basic"),
    Component(
        "ptrace", "Process snooping block",
        "A program can only read or change the memory of its own child "
        "processes (kernel.yama.ptrace_scope=1). Most kernels already start "
        "this way; this keeps it from being lowered.",
        "Malware running as you reading passwords out of your browser or "
        "other apps.",
        "Game trainers, Cheat Engine, and attaching gdb or strace to a program "
        "that is already running (without sudo).",
        "None.", False, "hardened"),
    Component(
        "mac-random", "Private Wi-Fi address",
        "Uses a different random hardware (MAC) address for every Wi-Fi "
        "network. It stays the same for one network, so captive portals and "
        "router reservations keep working.",
        "Networks and shops tracking the same device across different Wi-Fi "
        "networks.",
        "Router rules tied to the real address (MAC filters, parental "
        "controls) see a new device once on networks you already joined. "
        "Applies from the next Wi-Fi connection.",
        "None.", False, "hardened"),
    Component(
        "kernel-harden-alloc", "Harder memory layout",
        "Keeps each kind of kernel object in its own memory cache and hands "
        "out memory pages in random order (slab_nomerge page_alloc.shuffle=1).",
        "Makes kernel memory bugs harder to exploit: no cache merging, "
        "randomized page allocation.",
        "Nothing known.",
        "Under 1% (estimate).", True, "hardened"),
    Component(
        "lockdown", "Kernel lockdown",
        "Even root cannot change the running kernel: no unsigned modules, no "
        "/dev/mem, no hibernation (lockdown=integrity).",
        "Malware with root rights hiding inside the kernel.",
        "The NVIDIA proprietary driver and every other DKMS module (VirtualBox, "
        "ZFS, some Wi-Fi drivers), hibernation, undervolting and fan tools "
        "that write CPU registers. Refused while DKMS packages are installed, "
        "unless forced.",
        "None.", True, "super"),
    Component(
        "usbguard", "USB device guard",
        "Allows the USB devices plugged in when it is turned on and blocks "
        "every new USB device until you allow it (usbguard).",
        "BadUSB attacks: a cable or stick that pretends to be a keyboard.",
        "A new USB device (mouse, stick, phone, game controller) does nothing "
        "until allowed: sudo usbguard list-devices, then "
        "sudo usbguard allow-device -p <number> to keep it allowed.",
        "A small background service, no effect on speed.", False, "super"),
    Component(
        "kernel-hardened", "Hardened kernel",
        "Installs linux-hardened next to the current kernel and makes it the "
        "default boot entry. The other kernels stay in the boot menu.",
        "Many kernel exploit techniques, through extra checks the normal "
        "kernel skips.",
        "Slower than linux-cachyos in games and heavy work. No hibernation. "
        "NVIDIA needs a DKMS driver (nvidia-open-dkms or nvidia-dkms).",
        "Noticeably lower performance while booted into it.", True, "super"),
    Component(
        "userns-restrict", "Block user namespaces",
        "Nobody can create user namespaces any more "
        "(user.max_user_namespaces=0, plus kernel.unprivileged_userns_clone=0 "
        "on kernels that have it).",
        "Removes a large part of the kernel attack surface used by local "
        "privilege escalation exploits.",
        "BREAKS STEAM GAMES (Steam Linux Runtime / pressure-vessel), ALL "
        "FLATPAK APPS, Chrome, Chromium and Electron apps (Discord, VS Code), "
        "rootless Podman and Docker, bubblewrap. Firefox and LibreWolf keep "
        "working.",
        "None.", False, "super"),
    Component(
        "librewolf", "LibreWolf browser",
        "Installs LibreWolf (Firefox with tracking protection on and "
        "telemetry off) and makes it the system default browser. Turning it "
        "off keeps it installed and puts the previous default back.",
        "Tracking and fingerprinting while browsing.",
        "Sites that need DRM (Netflix, Spotify web) until DRM is turned on in "
        "its settings. Someone who picked their own default browser keeps it.",
        "Disk space for one more browser.", False, "super"),
    Component(
        "nosmt", "Turn off SMT (Hyper-Threading)",
        "Runs one thread per CPU core (nosmt). Not part of any level.",
        "Side-channel attacks between two threads sharing a core (MDS, L1TF "
        "and similar).",
        "Nothing stops working, but programs get half the CPU threads.",
        "Up to about 30% less multithreaded performance; most games change "
        "little.", True),
)

BY_ID = {c.id: c for c in COMPONENTS}


def level_ids(level: str) -> frozenset:
    """Component ids that a level turns on."""
    if level not in LEVELS:
        raise ValueError(f"unknown level: {level}")
    return frozenset(c.id for c in COMPONENTS if level in c.levels)


def level_of(enabled) -> str:
    """basic/hardened/super when `enabled` matches one exactly, none or custom."""
    enabled = frozenset(enabled)
    if not enabled:
        return "none"
    for level in LEVELS:
        if enabled == level_ids(level):
            return level
    return "custom"
