"""Temp target root + fake command runner shared by the tests."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG))

from glue_security import cli  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fake_run.py"
USB = ('allow id 1d6b:0002 serial "0000:00:14.0" name "xHCI Host Controller" '
       'hash "abc" parent-hash "def" with-interface 09:00:00\n'
       'allow id 046d:c52b serial "" name "USB Receiver" hash "ghi" '
       'parent-hash "abc" with-interface { 03:01:01 03:01:02 }\n')
BOOT_CONF = """\
# Glue Linux boot settings; glue-boot-update turns these into /boot/limine.conf
GLUE_BOOT_FIRMWARE=uefi
GLUE_BOOT_KERNEL=linux-cachyos
GLUE_BOOT_ROOT_UUID=aaaa-1111
GLUE_BOOT_RESUME_UUID=
GLUE_BOOT_CMDLINE="rw quiet loglevel=3 rd.udev.log_level=3 nowatchdog zswap.enabled=0"
GLUE_BOOT_TIMEOUT=5
"""

# verified in the Artix system/world/galaxy dbs (2026-10-20); glue-security is Glue's own
REPO_PACKAGES = {
    "nftables", "apparmor", "apparmor-dinit", "apparmor-runit", "apparmor-openrc",
    "usbguard", "usbguard-dinit", "usbguard-runit", "usbguard-openrc",
    "linux-hardened", "linux-hardened-headers", "librewolf",
    "pacman-contrib", "libnotify", "glue-security",
}


class TargetCase(unittest.TestCase):
    init = "dinit"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "mnt"
        self.log = Path(self.tmp.name) / "run.log"
        self.log.touch()
        self.add_pkg(self.init)
        self.add_pkg("linux-cachyos")
        self.add_pkg("networkmanager")
        (self.root / "etc/glue").mkdir(parents=True)
        (self.root / "etc/glue/boot.conf").write_text(BOOT_CONF)
        env = {"GLUE_SECURITY_RUN": f"{sys.executable} {FAKE}",
               "FAKE_LOG": str(self.log), "FAKE_USB": USB}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def add_pkg(self, name):
        d = self.root / "var/lib/pacman/local" / f"{name}-1.0-1"
        d.mkdir(parents=True, exist_ok=True)
        (d / "desc").write_text(f"%NAME%\n{name}\n")

    def cli(self, *args, root=True):
        argv = list(args) + (["--root", str(self.root)] if root else [])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(argv)
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def ok(self, *args):
        code, out, err = self.cli(*args)
        self.assertEqual(code, 0, f"{args}: {out}{err}")
        return out

    def status(self):
        return json.loads(self.ok("status", "--json"))

    def commands(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()]

    def f(self, rel):
        return self.root / rel

    def text(self, rel):
        return self.f(rel).read_text()

    def state(self):
        out = {}
        for line in self.text("etc/glue/security.conf").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                out[k] = v
        return out
