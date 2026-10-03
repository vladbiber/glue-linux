"""Focused tests for the graphical greeter profile."""

import struct
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
_REPO_ROOT = _PKG_ROOT.parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.catalog import Session
from glue_installer.plan_greeter import (
    _GREETER_WRAPPER_CONTENT,
    _REGREET_CONFIG_CONTENT,
    _REGREET_CSS_CONTENT,
    _greeter_files,
)


def _session(session_id: str, session_type: str) -> Session:
    return Session(
        id=session_id, name=session_id, kind="wm", description="test",
        ease=3, lightness=3, keybindings=[], screenshot=None,
        packages=[], services=[], shell_choices=[],
        session_type=session_type, exec=f"start-{session_id}",
    )


class ReGreetConfigTest(unittest.TestCase):

    def test_toml_is_light_has_no_wallpaper_and_uses_glue_name(self):
        config = tomllib.loads(_REGREET_CONFIG_CONTENT)
        self.assertNotIn("background", config)
        self.assertFalse(config["GTK"]["application_prefer_dark_theme"])
        self.assertEqual(config["appearance"]["greeting_msg"], "Glue Linux")
        self.assertFalse(config["skip_selection"])

    def test_css_uses_clean_neutral_palette_without_old_amber_theme(self):
        for color in ("#F4F6F8", "#FFFFFF", "#172033", "#4465E9",
                      "#D9DFE8"):
            self.assertIn(color, _REGREET_CSS_CONTENT)
        for old_color in ("#100A02", "#A66900", "#F1B00A"):
            self.assertNotIn(old_color, _REGREET_CSS_CONTENT)

    def test_live_profile_carries_graphical_greeter_and_fallback(self):
        profile = (_REPO_ROOT / "iso-profile/glue/profile.yaml").read_text()
        for package in ("accountsservice", "cage", "greetd-regreet",
                        "greetd-tuigreet"):
            self.assertIn(f"    - {package}\n", profile)


class GreeterLauncherTest(unittest.TestCase):

    def test_wrapper_is_valid_posix_shell(self):
        result = subprocess.run(
            ["sh", "-n"], input=_GREETER_WRAPPER_CONTENT,
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrapper_uses_kms_guard_cage_and_tuigreet_fallback(self):
        self.assertIn("/dev/dri/card*", _GREETER_WRAPPER_CONTENT)
        self.assertIn("cage -s -mlast -d -- regreet", _GREETER_WRAPPER_CONTENT)
        self.assertIn("/usr/bin/tuigreet", _GREETER_WRAPPER_CONTENT)
        self.assertIn("--sessions /usr/share/glue/sessions",
                      _GREETER_WRAPPER_CONTENT)

    def test_wrapper_builds_regreet_view_from_glue_sessions(self):
        self.assertIn("/usr/share/glue/sessions/*.desktop",
                      _GREETER_WRAPPER_CONTENT)
        self.assertIn("X-Glue-SessionType=x11", _GREETER_WRAPPER_CONTENT)
        self.assertIn('export XDG_DATA_DIRS="$session_data"',
                      _GREETER_WRAPPER_CONTENT)
        self.assertIn("xsessions|wayland-sessions", _GREETER_WRAPPER_CONTENT)
        self.assertIn('ln -s "$data"', _GREETER_WRAPPER_CONTENT)


class GeneratedSessionEntryTest(unittest.TestCase):

    def test_entries_record_x11_and_wayland_for_regreet(self):
        files = _greeter_files(
            [_session("x-test", "x11"), _session("wl-test", "wayland")],
            {}, {},
        )
        by_path = {file.path: file for file in files}
        self.assertIn(
            "X-Glue-SessionType=x11",
            by_path["/usr/share/glue/sessions/x-test.desktop"].content,
        )
        self.assertIn(
            "X-Glue-SessionType=wayland",
            by_path["/usr/share/glue/sessions/wl-test.desktop"].content,
        )

    def test_profile_installs_config_css_and_executable_launcher(self):
        files = _greeter_files([_session("wl-test", "wayland")], {}, {})
        by_path = {file.path: file for file in files}
        self.assertEqual(by_path["/etc/greetd/regreet.toml"].mode, 0o644)
        self.assertEqual(by_path["/etc/greetd/regreet.css"].mode, 0o644)
        self.assertEqual(by_path["/usr/local/bin/glue-greeter"].mode, 0o755)


class GreeterScreenshotTest(unittest.TestCase):

    def test_real_capture_is_committed_at_1920x1080(self):
        path = _REPO_ROOT / "screenshots/greeter.png"
        self.assertTrue(path.is_file(), "run scripts/greeter-headless.sh")
        data = path.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(data[12:16], b"IHDR")
        self.assertEqual(struct.unpack(">II", data[16:24]), (1920, 1080))
        self.assertGreater(len(data), 10_000, "capture is unexpectedly small")


if __name__ == "__main__":
    unittest.main()
