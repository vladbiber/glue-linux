"""Tests for the Glue alacritty config and the termfont script."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
_BRANDING = _PKG_ROOT.parent / "glue-branding"
_TOML = _BRANDING / "alacritty.toml"
_TERMFONT = _BRANDING / "termfont"
sys.path.insert(0, str(_PKG_ROOT))


def _code_lines(path):
    return [l for l in path.read_text().splitlines() if not l.startswith("#")]


class TestAlacrittyToml(unittest.TestCase):
    def setUp(self):
        with open(_TOML, "rb") as f:
            self.d = tomllib.load(f)

    def test_values(self):
        self.assertEqual(self.d["terminal"]["shell"], {"program": "/bin/zsh", "args": ["-l"]})
        self.assertEqual(self.d["window"]["opacity"], 0.5)
        self.assertEqual(self.d["font"]["size"], 11.0)
        self.assertEqual(self.d["font"]["normal"]["family"], "Liberation Mono")

    def test_bindings(self):
        b = self.d["keyboard"]["bindings"]
        self.assertEqual(len(b), 3)
        for x in b:
            self.assertEqual(x["mods"], "Control|Shift")
            self.assertEqual(x["command"]["program"], "termfont")
        self.assertEqual([x["command"]["args"] for x in b],
                         [["alacritty", "+1"], ["alacritty", "-1"], ["alacritty", "reset"]])

    def test_comments_are_english(self):
        self.assertIsNone(re.search(r"[ăâîșț]", _TOML.read_text()))


class TestBrandingPkgbuildAlacritty(unittest.TestCase):
    def setUp(self):
        self.text = (_BRANDING / "PKGBUILD").read_text()

    def test_install_paths(self):
        for line in (
            'install -Dm644 alacritty.toml "$pkgdir/etc/skel/.config/alacritty/alacritty.toml"',
            'install -Dm644 alacritty.toml "$pkgdir/etc/xdg/alacritty/alacritty.toml"',
            'install -Dm755 termfont "$pkgdir/usr/bin/termfont"',
        ):
            self.assertIn(line, self.text)

    def test_sources_and_backup(self):
        self.assertIn("'alacritty.toml' 'termfont'", self.text)
        self.assertIn("'etc/xdg/alacritty/alacritty.toml'", self.text)
        self.assertIn("'ttf-liberation: terminal font (Liberation Mono)'", self.text)

    def test_sums_match_sources(self):
        src = re.search(r"^source=\((.*?)\)", self.text, re.S | re.M).group(1)
        sums = re.search(r"^sha256sums=\((.*?)\)", self.text, re.S | re.M).group(1)
        self.assertEqual(len(src.split()), len(sums.split()))


@unittest.skipUnless(shutil.which("bash"), "bash missing")
class TestTermfont(unittest.TestCase):
    def test_script_basics(self):
        text = _TERMFONT.read_text()
        self.assertTrue(text.startswith("#!/usr/bin/env bash"))
        self.assertTrue(os.stat(_TERMFONT).st_mode & 0o111)
        self.assertEqual(len([l for l in text.splitlines() if "kitty" in l.lower()]), 1)
        self.assertIsNone(re.search(r"[ăâîșț]", text))
        self.assertLess(len(text.splitlines()), 100)
        subprocess.run(["bash", "-n", str(_TERMFONT)], check=True)

    def _run(self, home, *args):
        env = dict(os.environ, HOME=home)
        return subprocess.run(["bash", str(_TERMFONT), *args], env=env,
                              capture_output=True, text=True)

    def _font_size(self, home):
        with open(Path(home) / ".config/alacritty/alacritty.toml", "rb") as f:
            return tomllib.load(f)["font"]["size"]

    def test_grow_reset_show(self):
        with tempfile.TemporaryDirectory() as home:
            conf = Path(home) / ".config/alacritty"
            conf.mkdir(parents=True)
            shutil.copy(_TOML, conf / "alacritty.toml")
            self.assertEqual(self._run(home, "alacritty", "+1").returncode, 0)
            self.assertEqual(self._font_size(home), 12.0)
            self.assertEqual(self._run(home, "-2").returncode, 0)
            self.assertEqual(self._font_size(home), 10.0)
            self.assertEqual(self._run(home, "alacritty", "reset").returncode, 0)
            self.assertEqual(self._font_size(home), 11.0)
            r = self._run(home)
            self.assertIn("alacritty", r.stdout)
            self.assertIn("11.0", r.stdout)
            # only the [font] size changed
            self.assertEqual(_code_lines(conf / "alacritty.toml"), _code_lines(_TOML))

    def test_clamps_and_rejects(self):
        with tempfile.TemporaryDirectory() as home:
            shutil.copy(_TOML, Path(home) / "x.toml")
            self._run(home, "alacritty", "999")
            self.assertEqual(self._font_size(home), 72.0)
            r = self._run(home, "abc")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("invalid size", r.stderr)

    def test_kitty_is_an_error(self):
        with tempfile.TemporaryDirectory() as home:
            r = self._run(home, "kitty", "+1")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("only alacritty is supported", r.stderr)

    def test_missing_config_is_created(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(self._run(home, "+1").returncode, 0)
            # size depends on whether the host has /etc/xdg/alacritty
            self.assertGreater(self._font_size(home), 11.0)


class TestFastfetchInBranding(unittest.TestCase):
    def test_xdg_copy_installed_and_backed_up(self):
        text = (_BRANDING / "PKGBUILD").read_text()
        self.assertIn('install -Dm644 "$srcdir/fastfetch.jsonc" "$pkgdir/etc/xdg/fastfetch/config.jsonc"', text)
        self.assertIn("'etc/xdg/fastfetch/config.jsonc'", text)


class TestTerminalCheckScript(unittest.TestCase):
    """scripts/terminal-check.sh exists and is well-formed."""
    _SCRIPT = _PKG_ROOT.parent.parent / "scripts" / "terminal-check.sh"

    def test_exists_executable_small(self):
        self.assertTrue(self._SCRIPT.is_file())
        self.assertTrue(os.stat(self._SCRIPT).st_mode & 0o111)
        self.assertLess(len(self._SCRIPT.read_text().splitlines()), 500)

    @unittest.skipUnless(shutil.which("sh"), "sh missing")
    def test_sh_n_clean(self):
        r = subprocess.run(["sh", "-n", str(self._SCRIPT)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_has_all_probes(self):
        text = self._SCRIPT.read_text()
        for name in ("fastfetch-config-path", "fastfetch-logo", "fastfetch-os",
                     "fastfetch-latency", "alacritty-xdg-config",
                     "alacritty-fastfetch-shot", "termfont"):
            self.assertIn(name, text)
        self.assertIn("GLUE_CHECK_IMAGE", text)


class TestFontInPlan(unittest.TestCase):
    def test_liberation_on_every_graphical_install(self):
        from glue_installer import plan
        self.assertIn("alacritty", plan._DESKTOP_PACKAGES)
        self.assertIn("glue-zsh", plan._DESKTOP_PACKAGES)
        self.assertEqual(plan._ZSHRC_CONTENT, (_BRANDING.parent / "glue-zsh/skel.zshrc").read_text())
        self.assertIn("ttf-liberation", plan._DESKTOP_PACKAGES)


if __name__ == "__main__":
    unittest.main()
