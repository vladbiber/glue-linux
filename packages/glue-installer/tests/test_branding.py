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


_PALETTE_JSON = _BRANDING_ROOT / "palette.json"
_PALETTE_SH   = _BRANDING_ROOT / "palette.sh"

_EXPECTED_PALETTE = {
    "bg":     "#100A02",
    "lines":  "#A66900",
    "text":   "#F1B00A",
    "amber1": "#F1B00A",
    "amber2": "#A66900",
    "amber3": "#FFD75F",
    "amber4": "#7A4E00",
}

_HEX_RE = re.compile(r'^#[0-9A-F]{6}$')


class TestPaletteJson(unittest.TestCase):
    """palette.json must contain exactly the 7 canonical amber values."""

    def setUp(self):
        self.assertTrue(_PALETTE_JSON.exists(), f"palette.json not found at {_PALETTE_JSON}")
        self._data = json.loads(_PALETTE_JSON.read_text())

    def test_exact_keys(self):
        self.assertEqual(set(self._data.keys()), set(_EXPECTED_PALETTE.keys()))

    def test_values(self):
        for key, expected in _EXPECTED_PALETTE.items():
            self.assertEqual(self._data[key], expected, f"palette.json key '{key}' mismatch")

    def test_hex_format(self):
        for key, val in self._data.items():
            self.assertRegex(val.upper(), _HEX_RE, f"palette.json key '{key}' not #RRGGBB uppercase")


class TestPaletteSh(unittest.TestCase):
    """palette.sh must define all 7 GLUE_* vars with same values as palette.json."""

    _VAR_RE = re.compile(r'^(GLUE_\w+)=["\']?(#[0-9A-Fa-f]{6})["\']?\s*$')

    def setUp(self):
        self.assertTrue(_PALETTE_SH.exists(), f"palette.sh not found at {_PALETTE_SH}")
        self._vars: dict[str, str] = {}
        for line in _PALETTE_SH.read_text().splitlines():
            m = self._VAR_RE.match(line.strip())
            if m:
                self._vars[m.group(1)] = m.group(2).upper()

    def test_posix_syntax(self):
        import subprocess
        result = subprocess.run(["sh", "-n", str(_PALETTE_SH)])
        self.assertEqual(result.returncode, 0, "palette.sh fails sh -n syntax check")

    def test_all_vars_present(self):
        expected_vars = {
            "GLUE_BG", "GLUE_LINES", "GLUE_TEXT",
            "GLUE_AMBER1", "GLUE_AMBER2", "GLUE_AMBER3", "GLUE_AMBER4",
        }
        self.assertEqual(set(self._vars.keys()), expected_vars)

    def test_values_match_json(self):
        palette = json.loads(_PALETTE_JSON.read_text())
        mapping = {
            "GLUE_BG":     "bg",
            "GLUE_LINES":  "lines",
            "GLUE_TEXT":   "text",
            "GLUE_AMBER1": "amber1",
            "GLUE_AMBER2": "amber2",
            "GLUE_AMBER3": "amber3",
            "GLUE_AMBER4": "amber4",
        }
        for var, key in mapping.items():
            self.assertEqual(
                self._vars.get(var), palette[key].upper(),
                f"palette.sh {var} != palette.json {key}",
            )


class TestPaletteConsistencyFastfetch(unittest.TestCase):
    """fastfetch.jsonc color 1..4 must equal amber1..amber4 from palette.json."""

    def setUp(self):
        self.assertTrue(_PALETTE_JSON.exists(), f"palette.json not found at {_PALETTE_JSON}")
        path = _BRANDING_ROOT / "fastfetch.jsonc"
        self.assertTrue(path.exists(), f"fastfetch.jsonc not found at {path}")
        raw = path.read_text()
        stripped = re.sub(r'(?m)^\s*//.*\n?', '', raw)
        self._cfg = json.loads(stripped)
        self._palette = json.loads(_PALETTE_JSON.read_text())

    def test_color1_eq_amber1(self):
        self.assertEqual(
            self._cfg["logo"]["color"]["1"].upper(),
            self._palette["amber1"].upper(),
        )

    def test_color2_eq_amber2(self):
        self.assertEqual(
            self._cfg["logo"]["color"]["2"].upper(),
            self._palette["amber2"].upper(),
        )

    def test_color3_eq_amber3(self):
        self.assertEqual(
            self._cfg["logo"]["color"]["3"].upper(),
            self._palette["amber3"].upper(),
        )

    def test_color4_eq_amber4(self):
        self.assertEqual(
            self._cfg["logo"]["color"]["4"].upper(),
            self._palette["amber4"].upper(),
        )


class TestPaletteConsistencyGrub(unittest.TestCase):
    """grub-theme.txt colors must match bg/lines/text from palette.json."""

    _PROP_RE = re.compile(r'^\s*([\w-]+)\s*[=:]\s*"?(#[0-9A-Fa-f]{6})"?')

    def setUp(self):
        self.assertTrue(_PALETTE_JSON.exists(), f"palette.json not found at {_PALETTE_JSON}")
        path = _BRANDING_ROOT / "grub-theme.txt"
        self.assertTrue(path.exists(), f"grub-theme.txt not found at {path}")
        self._props: dict[str, str] = {}
        for line in path.read_text().splitlines():
            m = self._PROP_RE.match(line)
            if m:
                self._props[m.group(1)] = m.group(2).upper()
        self._palette = json.loads(_PALETTE_JSON.read_text())

    def test_message_color_eq_text(self):
        self.assertEqual(self._props.get("message-color"), self._palette["text"].upper())

    def test_selected_item_color_eq_text(self):
        self.assertEqual(self._props.get("selected_item_color"), self._palette["text"].upper())

    def test_message_bg_color_eq_bg(self):
        self.assertEqual(self._props.get("message-bg-color"), self._palette["bg"].upper())

    def test_desktop_color_eq_bg(self):
        self.assertEqual(self._props.get("desktop-color"), self._palette["bg"].upper())

    def test_item_color_eq_lines(self):
        self.assertEqual(self._props.get("item_color"), self._palette["lines"].upper())


class TestPkgbuildIncludesPalette(unittest.TestCase):
    """PKGBUILD must list palette.sh and palette.json in source=()."""

    def setUp(self):
        path = _BRANDING_ROOT / "PKGBUILD"
        self.assertTrue(path.exists(), f"PKGBUILD not found at {path}")
        self._text = path.read_text()

    def test_palette_sh_in_source(self):
        self.assertIn("palette.sh", self._text)

    def test_palette_json_in_source(self):
        self.assertIn("palette.json", self._text)


if __name__ == "__main__":
    unittest.main()
