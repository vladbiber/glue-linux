"""
Tests for the generated session wrapper (polkit agent, welcome, shell autostart).

Run with:
  cd packages/glue-installer
  python -m unittest discover -s tests -v
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.catalog import load_catalog
from glue_installer.plan import Selection, resolve_plan
from glue_installer.plan_greeter import (
    _SHELL_AUTOSTART_TEMPLATE, _session_wrapper_content,
)

_POLKIT = "/usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1"


class TestSessionWrapperPolkit(unittest.TestCase):

    def setUp(self):
        self.catalog = load_catalog(_PKG_ROOT / "catalog" / "catalog.json")
        self.sessions = {s.id: s for s in self.catalog.sessions}

    def _wrapper(self, session_id, shell_cmd=None):
        return _session_wrapper_content(self.sessions[session_id], shell_cmd)

    def _plan(self, session_ids, shell_choice):
        return resolve_plan(self.catalog, Selection(
            kernel_id="linux-cachyos", init_id="dinit",
            session_ids=session_ids, shell_choice=shell_choice,
            support_ids=[], gaming=False, minimal=False,
        ))

    def test_gluewc_polkit_before_welcome_and_shell(self):
        content = self._wrapper("gluewc", "glueqs")
        polkit = content.index(_POLKIT)
        welcome = content.index("glue-welcome --autostart")
        shell = content.index("exec glueqs")
        self.assertLess(polkit, welcome)
        self.assertLess(welcome, shell)
        self.assertLess(polkit, content.index("exec gluewc-session"))
        # Inside the --inner section, not the outer bootstrap.
        self.assertLess(content.index('"--inner" ]'), polkit)
        self.assertLess(polkit, content.index("\nfi\n"))
        self.assertIn('exec dbus-run-session -- sh -l "$0" --inner', content)

    def test_gluewc_wrapper_is_valid_sh(self):
        content = self._wrapper("gluewc", "glueqs")
        result = subprocess.run(
            ["sh", "-n"], input=content, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_nvwm_x11_wrapper_has_polkit(self):
        content = self._wrapper("nvwm")
        self.assertEqual(self.sessions["nvwm"].session_type, "x11")
        self.assertIn(f"[ -x {_POLKIT} ] && {_POLKIT} &", content)

    def test_desktop_environments_have_no_polkit_gnome(self):
        for session in self.catalog.sessions:
            if session.kind != "de":
                continue
            with self.subTest(session=session.id):
                self.assertNotIn("polkit-gnome", self._wrapper(session.id))
                self.assertNotIn("polkit-gnome", session.packages)
        self.assertNotIn("polkit-gnome", self._wrapper("kde-plasma"))

    def test_shell_autostart_still_in_wrapper(self):
        content = self._wrapper("gluewc", "glueqs")
        self.assertIn(_SHELL_AUTOSTART_TEMPLATE.format(shell_cmd="glueqs"), content)
        self.assertIn("wayland-*", content)
        self.assertNotIn("exec glueqs", self._wrapper("gluewc", None))

    def test_wayland_clients_wait_for_compositor(self):
        content = self._wrapper("gluewc", "glueqs")
        self.assertIn("wait_wayland() {", content)
        self.assertIn("(wait_wayland && command -v glue-welcome", content)
        self.assertIn("(wait_wayland && [ -x /usr/bin/glue-wallpaper-init ]", content)
        self.assertNotIn("wait_wayland", self._wrapper("nvwm", None))

    def test_plan_includes_polkit_gnome_only_for_wm_sessions(self):
        self.assertIn(
            "polkit-gnome", self._plan(["gluewc"], {"gluewc": "glueqs"}).packages)
        self.assertIn("polkit-gnome", self._plan(["nvwm"], {}).packages)
        self.assertNotIn("polkit-gnome", self._plan(["kde-plasma"], {}).packages)


class TestPortalConfig(unittest.TestCase):
    """xdg-desktop-portal backend file for Wayland WM sessions (roadmap 5.9)."""

    _DIR = "/usr/share/xdg-desktop-portal"

    def setUp(self):
        self.catalog = load_catalog(_PKG_ROOT / "catalog" / "catalog.json")
        self.sessions = {s.id: s for s in self.catalog.sessions}

    def _plan(self, session_ids, shell_choice):
        return resolve_plan(self.catalog, Selection(
            kernel_id="linux-cachyos", init_id="dinit",
            session_ids=session_ids, shell_choice=shell_choice,
            support_ids=[], gaming=False, minimal=False,
        ))

    def _portal_files(self, plan):
        return [f for f in plan.files if f.path.startswith(self._DIR + "/")]

    def test_gluewc_gets_portals_conf_named_after_its_desktop(self):
        plan = self._plan(["gluewc"], {"gluewc": "glueqs"})
        files = self._portal_files(plan)
        self.assertEqual(len(files), 1)
        conf = files[0]
        wrapper = _session_wrapper_content(self.sessions["gluewc"], "glueqs")
        desktop = re.search(r"^export XDG_CURRENT_DESKTOP=(\S+)$", wrapper, re.M).group(1)
        self.assertEqual(desktop, desktop.lower())
        self.assertEqual(conf.path, f"{self._DIR}/{desktop}-portals.conf")
        self.assertEqual(conf.mode, 0o644)
        self.assertIn("[preferred]\ndefault=gtk\n", conf.content)
        self.assertIn("org.freedesktop.impl.portal.ScreenCast=wlr\n", conf.content)
        self.assertIn("org.freedesktop.impl.portal.Screenshot=wlr\n", conf.content)
        self.assertIn("xdg-desktop-portal-wlr", plan.packages)
        self.assertIn("xdg-desktop-portal-gtk", plan.packages)

    def test_no_portals_conf_for_desktop_environments_or_x11(self):
        for sid in ("kde-plasma", "xfce", "gnome", "cinnamon", "nvwm"):
            with self.subTest(session=sid):
                self.assertEqual(self._portal_files(self._plan([sid], {})), [])

    def test_uppercase_desktop_rejected(self):
        from dataclasses import replace
        from glue_installer.plan_greeter import _portal_file
        wm = replace(self.sessions["gluewc"], desktop="GlueWC")
        with self.assertRaises(ValueError):
            _portal_file(wm)
        self.assertIsNone(_portal_file(self.sessions["kde-plasma"]))
