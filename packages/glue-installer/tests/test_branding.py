"""Tests for Glue Linux branding files: os-release, issue, default hostname, fastfetch."""

import json
import re
import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
_BRANDING_ROOT = _PKG_ROOT.parent / "glue-branding"
sys.path.insert(0, str(_PKG_ROOT))

_OS_RELEASE = _BRANDING_ROOT / "os-release"
_ISSUE = _BRANDING_ROOT / "issue"


def _parse_os_release(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        result[key.strip()] = val.strip().strip('"')
    return result


class TestOsRelease(unittest.TestCase):

    def setUp(self):
        self.assertTrue(_OS_RELEASE.exists(), f"os-release not found at {_OS_RELEASE}")
        self.fields = _parse_os_release(_OS_RELEASE.read_text())

    def test_name(self):
        self.assertEqual(self.fields.get("NAME"), "Glue Linux")

    def test_pretty_name(self):
        self.assertEqual(self.fields.get("PRETTY_NAME"), "Glue Linux")

    def test_id(self):
        self.assertEqual(self.fields.get("ID"), "glue")

    def test_id_like(self):
        self.assertEqual(self.fields.get("ID_LIKE"), "arch")

    def test_logo(self):
        self.assertEqual(self.fields.get("LOGO"), "glue")

    def test_ansi_color(self):
        self.assertEqual(self.fields.get("ANSI_COLOR"), "0;33")

    def test_no_wheatley_or_artix(self):
        text = _OS_RELEASE.read_text().lower()
        self.assertNotIn("wheatley", text)
        self.assertNotIn("artix", text)


class TestIssue(unittest.TestCase):

    def setUp(self):
        self.assertTrue(_ISSUE.exists(), f"issue not found at {_ISSUE}")
        self.text = _ISSUE.read_text()

    def test_contains_glue_linux(self):
        self.assertIn("Glue Linux", self.text)

    def test_has_multiline_banner(self):
        banner_lines = [ln for ln in self.text.splitlines() if len(ln.strip()) > 4]
        self.assertGreater(len(banner_lines), 2, "issue banner must have >2 non-trivial lines")

    def test_no_wheatley(self):
        self.assertNotIn("wheatley", self.text.lower())

    def test_no_artix(self):
        self.assertNotIn("artix", self.text.lower())


class TestHostnameDefault(unittest.TestCase):

    def test_hostname_default_is_glue(self):
        from glue_installer.ui_forms import _IDENTITY_DEFAULTS
        self.assertEqual(_IDENTITY_DEFAULTS["hostname"], "glue")


class TestLogoTxt(unittest.TestCase):
    """logo.txt must contain $1..$4 colour placeholders and no $$ escapes."""

    def setUp(self):
        self._logo = _BRANDING_ROOT / "logo.txt"
        self.assertTrue(self._logo.exists(), f"logo.txt not found at {self._logo}")
        self._text = self._logo.read_text()

    def test_has_placeholder_1(self):
        self.assertIn("$1", self._text)

    def test_has_placeholder_2(self):
        self.assertIn("$2", self._text)

    def test_has_placeholder_3(self):
        self.assertIn("$3", self._text)

    def test_has_placeholder_4(self):
        self.assertIn("$4", self._text)

    def test_no_escaped_dollar(self):
        self.assertNotIn("$$", self._text)


class TestFastfetchJsonc(unittest.TestCase):
    """fastfetch.jsonc must point to logo.txt with type:file and 4-step amber colour map."""

    def setUp(self):
        path = _BRANDING_ROOT / "fastfetch.jsonc"
        self.assertTrue(path.exists(), f"fastfetch.jsonc not found at {path}")
        raw = path.read_text()
        # strip only line-starting // comments (preserves :// in URLs)
        stripped = re.sub(r'(?m)^\s*//.*\n?', '', raw)
        self._cfg = json.loads(stripped)

    def test_logo_source(self):
        self.assertEqual(self._cfg["logo"]["source"], "/usr/share/glue/logo.txt")

    def test_logo_type(self):
        self.assertEqual(self._cfg["logo"]["type"], "file")

    def test_logo_color_keys(self):
        color = self._cfg["logo"]["color"]
        for key in ("1", "2", "3", "4"):
            self.assertIn(key, color, f"logo.color missing key '{key}'")


class TestPkgbuildClean(unittest.TestCase):
    """PKGBUILD must not reference logo-fastfetch or neofetch."""

    def setUp(self):
        path = _BRANDING_ROOT / "PKGBUILD"
        self.assertTrue(path.exists(), f"PKGBUILD not found at {path}")
        self._text = path.read_text()

    def test_no_logo_fastfetch(self):
        self.assertNotIn("logo-fastfetch", self._text)

    def test_no_neofetch(self):
        self.assertNotIn("neofetch", self._text)


if __name__ == "__main__":
    unittest.main()
