"""What turning each component on or off does to the files of a system."""

from __future__ import annotations

from typing import Dict, List, Optional

from .target import BOOT_CONF, CMDLINE_DROPIN, SecurityError, Target


class Refused(SecurityError):
    """A safety refusal: an error for enable, a warning while applying a level."""

SHARE = "/usr/share/glue-security"
FIREWALL_TABLE = "glue_firewall"

# kernel command line, in the order written to the drop-in
CMDLINE_TOKENS = {
    "apparmor": ("lsm=landlock,lockdown,yama,integrity,apparmor,bpf",),
    "kernel-harden-alloc": ("slab_nomerge", "page_alloc.shuffle=1"),
    "lockdown": ("lockdown=integrity",),
    "nosmt": ("nosmt",),
}
# written by older versions (init-on-alloc component); dropped on rewrite
LEGACY_TOKENS = ("init_on_alloc=1",)

SYSCTL_FILES = {
    "sysctl-base": ("etc/sysctl.d/90-glue-security-base.conf", """\
# glue-security: sysctl-base
kernel.dmesg_restrict=1
# same as glue-settings' 70-glue.conf, never lower
kernel.kptr_restrict=2
# one-way until reboot; linux-hardened has no kexec at all
-kernel.kexec_load_disabled=1
fs.protected_fifos=2
fs.protected_regular=2
fs.protected_symlinks=1
fs.protected_hardlinks=1
dev.tty.ldisc_autoload=0
net.ipv4.conf.all.accept_redirects=0
net.ipv4.conf.default.accept_redirects=0
net.ipv6.conf.all.accept_redirects=0
net.ipv6.conf.default.accept_redirects=0
net.ipv4.conf.all.send_redirects=0
net.ipv4.conf.default.send_redirects=0
"""),
    "ptrace": ("etc/sysctl.d/90-glue-security-ptrace.conf", """\
# glue-security: ptrace
kernel.yama.ptrace_scope=1
"""),
    "userns-restrict": ("etc/sysctl.d/90-glue-security-userns.conf", """\
# glue-security: userns-restrict (breaks Steam runtime, Flatpak, Chromium sandboxes)
# only kernels with the Debian/hardened patch have this knob
-kernel.unprivileged_userns_clone=0
user.max_user_namespaces=0
"""),
}

MAC_RANDOM = "etc/NetworkManager/conf.d/90-glue-mac-random.conf"
MAC_RANDOM_TEXT = """\
# glue-security: mac-random
# stable = one random address per connection, kept across reboots
[device]
wifi.scan-rand-mac-address=yes

[connection]
wifi.cloned-mac-address=stable
"""

AUTOSTART = "etc/xdg/autostart/glue-update-notify.desktop"

RUNIT_AA_LINK = "etc/rc/sysinit/62-apparmor"
RUNIT_AA_CONF = "etc/rc/apparmor.conf"

USBGUARD_CONF = "etc/usbguard/usbguard-daemon.conf"
USBGUARD_RULES = "etc/usbguard/rules.conf"
USBGUARD_KEYS = (
    ("RuleFile", "/etc/usbguard/rules.conf"),
    ("ImplicitPolicyTarget", "block"),
    ("PresentDevicePolicy", "apply-policy"),
    ("InsertedDevicePolicy", "apply-policy"),
    ("IPCAllowedGroups", "wheel"),
)

HARDENED = "linux-hardened"
MIMEAPPS = "etc/xdg/mimeapps.list"
BROWSER_DESKTOP = "librewolf.desktop"
BROWSER_MIMES = ("x-scheme-handler/http", "x-scheme-handler/https",
                 "text/html", "application/xhtml+xml")


# ------------------------------------------------------------- cmdline ----
def cmdline_tokens(t: Target) -> List[str]:
    out: List[str] = []
    for line in (t.read(CMDLINE_DROPIN) or "").splitlines():
        if not line.strip().startswith("#"):
            out += line.split()
    return out


def set_cmdline_token(t: Target, cid: str, on: bool) -> None:
    own = CMDLINE_TOKENS[cid]
    tokens = [x for x in cmdline_tokens(t) if x not in own and x not in LEGACY_TOKENS]
    if on:
        tokens += own
    order = [x for group in CMDLINE_TOKENS.values() for x in group]
    tokens.sort(key=lambda x: order.index(x) if x in order else len(order))
    if tokens:
        t.write(CMDLINE_DROPIN,
                "# glue-security: appended to the kernel command line by glue-boot-update\n"
                + " ".join(tokens) + "\n")
    else:
        t.remove(CMDLINE_DROPIN)
    t.boot_dirty = True


# ----------------------------------------------------------- mimeapps ----
def mime_defaults(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    section = ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            section = s
        elif section == "[Default Applications]" and "=" in s and not s.startswith("#"):
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def set_mime_defaults(text: str, values: Dict[str, Optional[str]]) -> str:
    """Set (or drop, for None) keys of [Default Applications]; rest untouched."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines)
                  if l.strip() == "[Default Applications]"), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[Default Applications]")
        start = len(lines) - 1
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].strip().startswith("[")), len(lines))
    body, done = [], set()
    for line in lines[start + 1:end]:
        key = line.split("=", 1)[0].strip()
        if key in values:
            if key not in done and values[key] is not None:
                body.append(f"{key}={values[key]}")
            done.add(key)
        else:
            body.append(line)
    while body and not body[-1].strip():
        body.pop()
    body += [f"{k}={v}" for k, v in values.items() if v is not None and k not in done]
    tail = lines[end:]
    if tail:
        body.append("")
    return "\n".join(lines[:start + 1] + body + tail) + "\n"


# ---------------------------------------------------------- components ----
def packages(t: Target, cid: str) -> List[str]:
    if cid == "firewall":
        return ["nftables", "glue-security"]
    if cid == "apparmor":
        return ["apparmor", f"apparmor-{t.init()}"]
    if cid == "updates-notify":
        return ["pacman-contrib", "libnotify", "glue-security"]
    if cid == "usbguard":
        return ["usbguard", f"usbguard-{t.init()}"]
    if cid == "kernel-hardened":
        return [HARDENED, f"{HARDENED}-headers"]
    if cid == "librewolf":
        return ["librewolf"]
    return []


def is_on(t: Target, cid: str) -> bool:
    if cid in SYSCTL_FILES:
        return t.exists(SYSCTL_FILES[cid][0])
    if cid == "firewall":
        return t.service_enabled("glue-firewall")
    if cid == "apparmor":
        if not set(CMDLINE_TOKENS[cid]) <= set(cmdline_tokens(t)):
            return False
        if t.init() == "runit":
            return t.exists(RUNIT_AA_LINK)
        return t.service_enabled("apparmor")
    if cid in CMDLINE_TOKENS:
        return set(CMDLINE_TOKENS[cid]) <= set(cmdline_tokens(t))
    if cid == "updates-notify":
        return t.exists(AUTOSTART)
    if cid == "mac-random":
        return t.exists(MAC_RANDOM)
    if cid == "usbguard":
        return t.service_enabled("usbguard")
    if cid == "kernel-hardened":
        if not t.installed(HARDENED):
            return False
        boot = t.read(BOOT_CONF)
        if boot is None:
            return t.state().get("kernel_hardened") == "1"
        return t.parse_kv(boot).get("GLUE_BOOT_KERNEL") == HARDENED
    if cid == "librewolf":
        return t.installed("librewolf") and all(
            mime_defaults(t.read(rel) or "").get(m) == BROWSER_DESKTOP
            for rel in browser_files(t) for m in BROWSER_MIMES)
    raise SecurityError(f"unknown component: {cid}")


def enable(t: Target, cid: str, force: bool = False) -> None:
    if cid == "lockdown" and not force:
        dkms = t.dkms_packages()
        if dkms:
            raise Refused(
                "lockdown would stop these unsigned modules from loading: "
                + ", ".join(dkms) + " (use --force to turn it on anyway)")
    if cid == "kernel-hardened":
        _enable_hardened(t)
        return
    t.install(packages(t, cid))
    if cid in SYSCTL_FILES:
        t.apply_sysctl(*SYSCTL_FILES[cid])
    elif cid == "firewall":
        t.enable_service("glue-firewall")
    elif cid == "apparmor":
        set_cmdline_token(t, cid, True)
        if t.init() == "runit":
            link = t.path(RUNIT_AA_LINK)
            link.parent.mkdir(parents=True, exist_ok=True)
            if not t.exists(RUNIT_AA_LINK):
                link.symlink_to("/usr/lib/rc/sv.d/apparmor")
            t.set_kv(RUNIT_AA_CONF, "APPARMOR", "enforce")
        else:
            t.enable_service("apparmor", boot=True)
    elif cid in CMDLINE_TOKENS:
        set_cmdline_token(t, cid, True)
    elif cid == "updates-notify":
        if not t.exists(AUTOSTART):
            link = t.path(AUTOSTART)
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(f"{SHARE}/glue-update-notify.desktop")
    elif cid == "mac-random":
        t.write(MAC_RANDOM, MAC_RANDOM_TEXT)
        t.live_run(["nmcli", "general", "reload", "conf"])
    elif cid == "usbguard":
        _enable_usbguard(t)
    elif cid == "librewolf":
        _enable_browser(t)
    else:
        raise SecurityError(f"unknown component: {cid}")


def disable(t: Target, cid: str) -> None:
    if cid in SYSCTL_FILES:
        t.remove_sysctl(SYSCTL_FILES[cid][0])
        if cid == "sysctl-base" and t.live:
            t.note("kexec stays disabled until the next restart")
    elif cid == "firewall":
        t.disable_service("glue-firewall")
        t.live_run(["nft", "delete", "table", "inet", FIREWALL_TABLE])
    elif cid == "apparmor":
        set_cmdline_token(t, cid, False)
        if t.init() == "runit":
            t.remove(RUNIT_AA_LINK)
            if t.exists(RUNIT_AA_CONF):
                t.set_kv(RUNIT_AA_CONF, "APPARMOR", "disable")
        else:
            t.disable_service("apparmor")
    elif cid in CMDLINE_TOKENS:
        set_cmdline_token(t, cid, False)
    elif cid == "updates-notify":
        t.remove(AUTOSTART)
    elif cid == "mac-random":
        t.remove(MAC_RANDOM)
        t.live_run(["nmcli", "general", "reload", "conf"])
    elif cid == "usbguard":
        t.disable_service("usbguard")
        if t.live:
            t.note("unplug and replug USB devices that were blocked")
    elif cid == "kernel-hardened":
        _disable_hardened(t)
    elif cid == "librewolf":
        _disable_browser(t)
    else:
        raise SecurityError(f"unknown component: {cid}")


# ---------------------------------------------------------------- usbguard
def _enable_usbguard(t: Target) -> None:
    argv = ["usbguard", "generate-policy"]
    if t.outside:
        # artix-chroot binds the live /sys: the devices plugged in right now
        argv = ["artix-chroot", str(t.root), *argv]
    out = t.run(argv, capture=True).stdout or ""
    rules = [l for l in out.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if not rules:
        raise Refused("usbguard found no USB devices; refusing to turn it on "
                            "(it would block the keyboard)")
    old = [l for l in (t.read(USBGUARD_RULES) or "").splitlines() if l.strip()]
    merged = old + [r for r in rules if r not in old]
    t.write(USBGUARD_RULES, "\n".join(merged) + "\n", mode=0o600)
    for key, value in USBGUARD_KEYS:
        t.set_kv(USBGUARD_CONF, key, value)
    t.path(USBGUARD_CONF).chmod(0o600)
    t.enable_service("usbguard")


# --------------------------------------------------------- kernel-hardened
def _enable_hardened(t: Target) -> None:
    boot = t.read(BOOT_CONF)
    state = t.state()
    prev = None
    if boot is not None:
        prev = t.parse_kv(boot).get("GLUE_BOOT_KERNEL", "")
        if prev != HARDENED:
            # before pacman: its hook regenerates the menu with the new default
            t.set_kv(BOOT_CONF, "GLUE_BOOT_KERNEL", HARDENED)
    try:
        t.install(packages(t, "kernel-hardened"))
    except SecurityError:
        if prev is not None and prev != HARDENED:
            t.set_kv(BOOT_CONF, "GLUE_BOOT_KERNEL", prev)
        raise
    if boot is None:
        # installer: its bootloader step writes boot.conf with this kernel later
        state["kernel_hardened"] = "1"
        if t.live:
            t.note(f"{BOOT_CONF} not found: set GLUE_BOOT_KERNEL={HARDENED} there")
    elif prev and prev != HARDENED:
        state["kernel_prev"] = prev
    t.save_state(state)
    t.boot_dirty = True
    if (t.installed("nvidia-utils")
            and not any(p.startswith("nvidia") for p in t.dkms_packages())):
        t.note(f"NVIDIA: {HARDENED} needs nvidia-open-dkms or nvidia-dkms")


def _disable_hardened(t: Target) -> None:
    state = t.state()
    prev = state.pop("kernel_prev", "")
    state.pop("kernel_hardened", None)
    t.save_state(state)
    boot = t.read(BOOT_CONF)
    if boot is None or t.parse_kv(boot).get("GLUE_BOOT_KERNEL") != HARDENED:
        return
    if not prev or not t.installed(prev):
        prev = "linux-cachyos" if t.installed("linux-cachyos") else ""
    if not prev:
        raise SecurityError(f"no other kernel to boot by default; install one "
                            f"before turning {HARDENED} off")
    t.set_kv(BOOT_CONF, "GLUE_BOOT_KERNEL", prev)
    t.boot_dirty = True


# --------------------------------------------------------------- browser
def browser_files(t: Target) -> List[str]:
    """mimeapps.list plus every <desktop>-mimeapps.list that picks a browser
    (those win over the plain file for their session)."""
    files = [MIMEAPPS]
    for p in sorted(t.path("etc/xdg").glob("*-mimeapps.list")):
        if set(mime_defaults(p.read_text())) & set(BROWSER_MIMES):
            files.append(str(p.relative_to(t.root)))
    return files


def _enable_browser(t: Target) -> None:
    saved = t.var_json("browser-prev.json")
    for rel in browser_files(t):
        text = t.read(rel) or ""
        if rel not in saved:
            cur = mime_defaults(text)
            saved[rel] = {m: cur.get(m) for m in BROWSER_MIMES}
        t.write(rel, set_mime_defaults(text, {m: BROWSER_DESKTOP for m in BROWSER_MIMES}))
    t.save_var_json("browser-prev.json", saved)


def _disable_browser(t: Target) -> None:
    saved = t.var_json("browser-prev.json")
    for rel in browser_files(t):
        prev = saved.get(rel, {})
        values = {m: prev.get(m) for m in BROWSER_MIMES}
        values = {m: (None if v == BROWSER_DESKTOP else v) for m, v in values.items()}
        text = set_mime_defaults(t.read(rel) or "", values)
        if rel == MIMEAPPS and text.strip() == "[Default Applications]":
            t.remove(rel)
        else:
            t.write(rel, text)
    t.save_var_json("browser-prev.json", {})
