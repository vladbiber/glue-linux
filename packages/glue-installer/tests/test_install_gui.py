"""glue-install-gui launcher (roadmap 10.1/10.2): exit codes, command, live-only."""

import itertools
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.catalog import load_catalog
from glue_installer.clone import LIVE_ONLY_FILES, LIVE_ONLY_PACKAGES
from glue_installer.plan import resolve_plan
from glue_installer.plan_types import Selection

_REPO_ROOT = _PKG_ROOT.parent.parent
_CFG = _REPO_ROOT / "packages" / "glue-calamares-config"
_SCRIPT = _CFG / "glue-install-gui"
_PROFILE = _REPO_ROOT / "iso-profile" / "glue" / "profile.yaml"
_CATALOG = _PKG_ROOT / "catalog" / "catalog.json"
_LIVE_ONLY_GUI = ("calamares", "kpmcore", "ckbcomp", "glue-calamares-config")


class _Launcher(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.marker = self.tmp / "bootmnt"
        self.marker.mkdir()
        (self.tmp / "card0").touch()
        self.runtime = self.tmp / "run"
        self.runtime.mkdir()

    def run_gui(self, **overrides):
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GLUE_LIVE_MARKER": str(self.marker),
            "GLUE_KMS_GLOB": str(self.tmp / "card*"),
            "GLUE_INSTALL_DRYRUN": "1",
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_RUNTIME_DIR": str(self.runtime),
        }
        env.update(overrides)
        env = {k: v for k, v in env.items() if v is not None}
        return subprocess.run(["sh", str(_SCRIPT)], env=env, capture_output=True, text=True)


class TestLauncher(_Launcher):
    def test_syntax(self):
        r = subprocess.run(["sh", "-n", str(_SCRIPT)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_not_live_exit_2(self):
        r = self.run_gui(GLUE_LIVE_MARKER=str(self.tmp / "missing"))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")

    def test_no_kms_exit_3_points_to_tui(self):
        r = self.run_gui(GLUE_KMS_GLOB=str(self.tmp / "nodri*"))
        self.assertEqual(r.returncode, 3)
        self.assertIn("glue-install", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_no_display_exit_4(self):
        r = self.run_gui(WAYLAND_DISPLAY=None, DISPLAY=None)
        self.assertEqual(r.returncode, 4)

    def test_wayland_without_runtime_dir_exit_4(self):
        r = self.run_gui(XDG_RUNTIME_DIR=None)
        self.assertEqual(r.returncode, 4)

    def test_live_check_precedes_kms_check(self):
        r = self.run_gui(GLUE_LIVE_MARKER=str(self.tmp / "missing"),
                         GLUE_KMS_GLOB=str(self.tmp / "nodri*"))
        self.assertEqual(r.returncode, 2)

    def test_dry_run_wayland_command(self):
        r = self.run_gui()
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.strip()
        self.assertEqual(len(out.splitlines()), 1)
        self.assertTrue(out.startswith("pkexec env "))
        self.assertIn("WAYLAND_DISPLAY=wayland-0", out)
        self.assertIn(f"XDG_RUNTIME_DIR={self.runtime}", out)
        self.assertIn("QT_QPA_PLATFORM=wayland", out)
        self.assertIn("calamares -c /etc/calamares", out)
        for bad in ("xhost", "sudo", " -E", "password"):
            self.assertNotIn(bad, out)

    def test_dry_run_x11_command(self):
        r = self.run_gui(WAYLAND_DISPLAY=None, DISPLAY=":0", XAUTHORITY="/tmp/xa")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DISPLAY=:0", r.stdout)
        self.assertIn("XAUTHORITY=/tmp/xa", r.stdout)
        self.assertIn("QT_QPA_PLATFORM=xcb", r.stdout)
        self.assertNotIn("WAYLAND_DISPLAY", r.stdout)

    def test_config_dir_override(self):
        r = self.run_gui(GLUE_CALAMARES_CONFIG="/opt/cfg")
        self.assertIn("calamares -c /opt/cfg", r.stdout)

    def test_missing_config_exit_5_when_not_dry_run(self):
        r = self.run_gui(GLUE_INSTALL_DRYRUN=None,
                         GLUE_CALAMARES_CONFIG=str(self.tmp / "nocfg"))
        self.assertEqual(r.returncode, 5)

    def test_no_artix_in_messages(self):
        results = [
            self.run_gui(GLUE_LIVE_MARKER=str(self.tmp / "missing")),
            self.run_gui(GLUE_KMS_GLOB=str(self.tmp / "nodri*")),
            self.run_gui(WAYLAND_DISPLAY=None, DISPLAY=None),
            self.run_gui(),
            self.run_gui(GLUE_INSTALL_DRYRUN=None,
                         GLUE_CALAMARES_CONFIG=str(self.tmp / "nocfg")),
        ]
        for r in results:
            self.assertNotIn("artix", (r.stdout + r.stderr).lower())


class TestPackaging(unittest.TestCase):
    def test_desktop_entry(self):
        text = (_CFG / "glue-install-gui.desktop").read_text()
        self.assertIn("Name=Install Glue Linux", text)
        self.assertIn("Exec=glue-install-gui", text)
        self.assertIn("Categories=System;", text)
        self.assertNotIn("artix", text.lower())

    def test_pkgbuild_installs_launcher_not_autostart(self):
        text = (_CFG / "PKGBUILD").read_text()
        self.assertIn("-Dm755", text)
        self.assertIn("/usr/bin/glue-install-gui", text)
        self.assertIn("/usr/share/applications/glue-install-gui.desktop", text)
        self.assertNotIn("autostart", text)

    def test_script_is_posix_sh(self):
        text = _SCRIPT.read_text()
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertNotIn("[[", text)
        self.assertNotIn("xhost", text)
        self.assertNotIn("sudo", text)

    def test_packages_in_profile_and_live_only(self):
        profile = _PROFILE.read_text()
        for pkg in _LIVE_ONLY_GUI + ("os-prober", "polkit"):
            self.assertIn(f"    - {pkg}\n", profile)
        for pkg in _LIVE_ONLY_GUI:
            self.assertIn(pkg, LIVE_ONLY_PACKAGES)
        self.assertIn("/usr/share/applications/glue-install-gui.desktop", LIVE_ONLY_FILES)

    def test_make_iso_builds_config_package(self):
        text = (_REPO_ROOT / "scripts" / "make-iso.sh").read_text()
        self.assertIn("glue-installer glue-calamares-config; do", text)

    def test_never_in_a_resolved_plan(self):
        cat = load_catalog(_CATALOG)
        sessions = {s.id: s for s in cat.sessions}
        session_sets = [[]] + [[s.id] for s in cat.sessions] + [[s.id for s in cat.sessions]]
        checked = 0
        for kernel, init, sids, gaming in itertools.product(
                cat.kernels, cat.inits, session_sets, (False, True)):
            choice = {}
            for sid in sids:
                if sessions[sid].shell_choices:
                    choice[sid] = sessions[sid].shell_choices[0]
            sel = Selection(kernel.id, init.id, list(sids), choice, [], gaming,
                            minimal=not sids)
            if not sids and gaming:
                continue
            plan = resolve_plan(cat, sel)
            checked += 1
            for pkg in _LIVE_ONLY_GUI:
                self.assertNotIn(pkg, plan.packages)
        self.assertGreater(checked, 20)
        # every shell alternative too
        for s in cat.sessions:
            for shell in s.shell_choices:
                sel = Selection(cat.kernels[0].id, cat.inits[0].id, [s.id], {s.id: shell},
                                [], False, False)
                plan = resolve_plan(cat, sel)
                for pkg in _LIVE_ONLY_GUI:
                    self.assertNotIn(pkg, plan.packages)


if __name__ == "__main__":
    unittest.main()
