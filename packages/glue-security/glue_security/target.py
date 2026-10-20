"""The system being changed: the running one, or one mounted at --root.

With --root, or inside a chroot (the installer runs it through artix-chroot),
only files change. Every command goes through run(), which
GLUE_SECURITY_RUN can replace.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional

INITS = ("dinit", "runit", "openrc")
STATE = "etc/glue/security.conf"
CMDLINE_DROPIN = "etc/glue/cmdline.d/50-glue-security.conf"
BOOT_CONF = "etc/glue/boot.conf"
VARDIR = "var/lib/glue-security"

# init -> (service dir, enabled-link dir); same layout as the installer
_LINKS = {
    "dinit": ("etc/dinit.d", "etc/dinit.d/boot.d"),
    "runit": ("etc/runit/sv", "etc/runit/runsvdir/default"),
    "openrc": ("etc/init.d", "etc/runlevels/default"),
}


class SecurityError(Exception):
    pass


class Target:
    def __init__(self, root: Optional[str] = None, env=None):
        # outside: commands run in the target through artix-chroot
        self.outside = root is not None
        self.live = root is None and not in_chroot()
        self.root = Path(root or "/")
        self.env = os.environ if env is None else env
        self.notes: List[str] = []
        self.boot_dirty = False
        self._init: Optional[str] = None

    def path(self, rel: str) -> Path:
        return self.root / rel.lstrip("/")

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    # ------------------------------------------------------------ commands
    def run(self, argv: List[str], capture: bool = False, check: bool = True):
        stub = self.env.get("GLUE_SECURITY_RUN")
        full = (shlex.split(stub) if stub else []) + list(argv)
        try:
            r = subprocess.run(full, text=True,
                               stdout=subprocess.PIPE if capture else None,
                               stderr=subprocess.PIPE if capture else None)
        except FileNotFoundError:
            raise SecurityError(f"command not found: {full[0]}")
        if check and r.returncode != 0:
            err = (r.stderr or "").strip() if capture else ""
            raise SecurityError(f"failed ({r.returncode}): {' '.join(argv)}"
                                + (f": {err}" if err else ""))
        return r

    def live_run(self, argv: List[str]) -> bool:
        """Best-effort action on the running system; skipped with --root."""
        if not self.live:
            return False
        if not self.env.get("GLUE_SECURITY_RUN") and shutil.which(argv[0]) is None:
            return False
        try:
            return self.run(argv, capture=True, check=False).returncode == 0
        except SecurityError:
            return False

    # ------------------------------------------------------------ packages
    def installed(self, name: str) -> bool:
        local = self.path("var/lib/pacman/local")
        if not local.is_dir():
            return False
        for d in local.glob(f"{name}-*"):
            if d.name.rsplit("-", 2)[0] == name and (d / "desc").is_file():
                return True
        return False

    def install(self, names: Iterable[str]) -> None:
        missing = [n for n in dict.fromkeys(names) if not self.installed(n)]
        if not missing:
            return
        cmd = ["pacman", "-S", "--needed", "--noconfirm", *missing]
        if self.outside:
            cmd = ["artix-chroot", str(self.root), *cmd]
        self.run(cmd)
        still = [n for n in missing if not self.installed(n)]
        if still:
            raise SecurityError(f"not installed after pacman: {' '.join(still)}")

    def dkms_packages(self) -> List[str]:
        local = self.path("var/lib/pacman/local")
        if not local.is_dir():
            return []
        return sorted(d.name.rsplit("-", 2)[0] for d in local.iterdir()
                      if d.name.rsplit("-", 2)[0].endswith("-dkms"))

    # ---------------------------------------------------------------- init
    def init(self) -> str:
        if self._init:
            return self._init
        found = None
        # the target's packages first: in a chroot PID 1 is the live ISO's init
        found = next((i for i in INITS if self.installed(i)), None)
        if not found and self.live:
            try:
                comm = Path("/proc/1/comm").read_text().strip()
            except OSError:
                comm = ""
            found = next((i for i in INITS if comm.startswith(i)), None)
        if not found:
            found = next((i for i in INITS if self.path(_LINKS[i][1]).is_dir()), None)
        if not found:
            raise SecurityError(f"cannot tell the init system of {self.root} "
                                "(no dinit, runit or openrc)")
        self._init = found
        return found

    def _service_links(self, svc: str) -> List[Path]:
        init = self.init()
        links = [self.path(_LINKS[init][1]) / svc]
        if init == "openrc":
            links.append(self.path("etc/runlevels/boot") / svc)
        return links

    def service_enabled(self, svc: str) -> bool:
        return any(l.is_symlink() or l.exists() for l in self._service_links(svc))

    def enable_service(self, svc: str, boot: bool = False) -> None:
        init = self.init()
        src_dir, dst_dir = _LINKS[init]
        if init == "openrc" and boot:
            dst_dir = "etc/runlevels/boot"
        link = self.path(dst_dir) / svc
        link.parent.mkdir(parents=True, exist_ok=True)
        if not (link.is_symlink() or link.exists()):
            link.symlink_to(f"../{svc}" if init == "dinit" else f"/{src_dir}/{svc}")
        if init == "dinit":
            self.live_run(["dinitctl", "start", svc])
        elif init == "openrc":
            self.live_run(["rc-service", svc, "start"])

    def disable_service(self, svc: str) -> None:
        init = self.init()
        if init == "dinit":
            self.live_run(["dinitctl", "stop", svc])
        elif init == "runit":
            self.live_run(["sv", "down", svc])
        else:
            self.live_run(["rc-service", svc, "stop"])
        for link in self._service_links(svc):
            if link.is_symlink() or link.exists():
                link.unlink()

    # --------------------------------------------------------------- files
    def write(self, rel: str, text: str, mode: int = 0o644) -> None:
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".glue-tmp")
        tmp.write_text(text)
        tmp.chmod(mode)
        tmp.replace(p)

    def read(self, rel: str) -> Optional[str]:
        try:
            return self.path(rel).read_text()
        except OSError:
            return None

    def remove(self, rel: str) -> None:
        p = self.path(rel)
        if p.is_symlink() or p.exists():
            p.unlink()

    def exists(self, rel: str) -> bool:
        return self.path(rel).is_symlink() or self.path(rel).exists()

    # ------------------------------------------------------- key=value files
    @staticmethod
    def parse_kv(text: Optional[str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for line in (text or "").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            out[k.strip()] = v
        return out

    def set_kv(self, rel: str, key: str, value: str, quote: bool = False) -> None:
        """Replace or append KEY=value, keeping every other line as it is."""
        text = self.read(rel) or ""
        new = f'{key}="{value}"' if quote else f"{key}={value}"
        lines = text.splitlines()
        hit = [i for i, l in enumerate(lines) if l.strip().startswith(key + "=")]
        if hit:
            lines[hit[-1]] = new
            for i in reversed(hit[:-1]):
                del lines[i]
        else:
            lines.append(new)
        self.write(rel, "\n".join(lines) + "\n")

    # --------------------------------------------------------------- state
    def state(self) -> Dict[str, str]:
        return self.parse_kv(self.read(STATE))

    def save_state(self, values: Dict[str, str]) -> None:
        body = "".join(f"{k}={values[k]}\n" for k in sorted(values))
        self.write(STATE, "# glue-security state; change it with glue-security\n" + body)

    def var_json(self, name: str) -> dict:
        try:
            return json.loads(self.read(f"{VARDIR}/{name}") or "{}")
        except ValueError:
            return {}

    def save_var_json(self, name: str, data: dict) -> None:
        if data:
            self.write(f"{VARDIR}/{name}", json.dumps(data, indent=1, sort_keys=True) + "\n")
        else:
            self.remove(f"{VARDIR}/{name}")

    # -------------------------------------------------------------- sysctl
    def apply_sysctl(self, rel: str, text: str) -> None:
        """Write a sysctl.d file; live, remember the old values and load it."""
        if self.live:
            prev = self.var_json("sysctl-prev.json")
            for key, _ in sysctl_pairs(text):
                cur = _proc_sysctl(key)
                if cur is not None:
                    prev.setdefault(key, cur)
            self.save_var_json("sysctl-prev.json", prev)
        self.write(rel, text)
        self.live_run(["sysctl", "-q", "-p", str(self.path(rel))])

    def remove_sysctl(self, rel: str) -> None:
        text = self.read(rel)
        self.remove(rel)
        if not self.live or text is None:
            return
        prev = self.var_json("sysctl-prev.json")
        for key, _ in sysctl_pairs(text):
            if key in prev:
                self.live_run(["sysctl", "-q", "-w", f"{key}={prev.pop(key)}"])
        self.save_var_json("sysctl-prev.json", prev)
        self.live_run(["sysctl", "-q", "--system"])


def in_chroot() -> bool:
    """True when / is not PID 1's root (how systemd-detect-virt --chroot tells)."""
    try:
        a, b = os.stat("/"), os.stat("/proc/1/root")
    except PermissionError:
        return False
    except OSError:
        # no /proc mounted: not a running system
        return True
    return (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino)


def sysctl_pairs(text: str):
    for line in text.splitlines():
        s = line.strip()
        if not s or s[0] in "#;" or "=" not in s:
            continue
        k, v = s.lstrip("-").split("=", 1)
        yield k.strip(), v.strip()


def _proc_sysctl(key: str) -> Optional[str]:
    try:
        return Path("/proc/sys", key.replace(".", "/")).read_text().strip()
    except OSError:
        return None
