"""Tests for Glue Linux branding files: os-release, issue, default hostname."""

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


if __name__ == "__main__":
    unittest.main()
