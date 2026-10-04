"""Tests for Glue Linux branding files: os-release, issue, default hostname, fastfetch, PNG images."""

import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
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
    "boot_bg": "#E9E9E7",
    "boot_message": "#2B2F33",
    "boot_text": "#3A3F44",
    "boot_accent": "#B5484D",
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
            "GLUE_BOOT_BG", "GLUE_BOOT_MESSAGE", "GLUE_BOOT_TEXT", "GLUE_BOOT_ACCENT",
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
            "GLUE_BOOT_BG": "boot_bg",
            "GLUE_BOOT_MESSAGE": "boot_message",
            "GLUE_BOOT_TEXT": "boot_text",
            "GLUE_BOOT_ACCENT": "boot_accent",
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


class WallpaperCandidateTest(unittest.TestCase):
    """The packaged wallpaper is desktop-sized and carries auditable CC0 data."""

    def test_wallpaper_is_1920x1080_png(self):
        path = _BRANDING_ROOT / "wallpaper.png"
        data = path.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(data[12:16], b"IHDR")
        self.assertEqual(struct.unpack(">II", data[16:24]), (1920, 1080))

    def test_credits_record_author_source_and_cc0(self):
        credits = (_BRANDING_ROOT / "WALLPAPER-CREDITS.md").read_text()
        self.assertIn("OpenAI image generation", credits)
        self.assertIn("Glue Linux project maintainer", credits)
        self.assertIn("Generated original SHA-256", credits)
        self.assertIn("CC0 1.0", credits)

    def test_pkgbuild_installs_wallpaper_and_credits(self):
        pkgbuild = (_BRANDING_ROOT / "PKGBUILD").read_text()
        self.assertIn("wallpaper.png", pkgbuild)
        self.assertIn("usr/share/backgrounds/glue/wallpaper.png", pkgbuild)
        self.assertIn("WALLPAPER-CREDITS.md", pkgbuild)


_REPO_ROOT = _PKG_ROOT.parent.parent
_THEME_DIR = _REPO_ROOT / "iso-profile/glue/root-overlay/usr/share/grub/themes/artix"
_LIVE_THEME_TXT = _THEME_DIR / "theme.txt"
_KERNELS_CFG = _REPO_ROOT / "iso-profile/glue/live-overlay/usr/share/grub/cfg/kernels.cfg"

_IMG_REF_RE = re.compile(r'"([^"*?]+\.(?:png|jpg|jpeg|bmp|tga))"')
_HEX_IN_THEME_RE = re.compile(r'"(#[0-9A-Fa-f]{6})"')


class TestGrubThemeLive(unittest.TestCase):
    """Live GRUB theme in root-overlay must be Glue-branded and palette-consistent."""

    def setUp(self):
        self.assertTrue(_THEME_DIR.exists(), f"theme dir not found: {_THEME_DIR}")
        self.assertTrue(_LIVE_THEME_TXT.exists(), f"theme.txt not found: {_LIVE_THEME_TXT}")
        self.assertTrue(_KERNELS_CFG.exists(), f"kernels.cfg not found: {_KERNELS_CFG}")
        self._theme_text = _LIVE_THEME_TXT.read_text()
        self._palette = json.loads(_PALETTE_JSON.read_text())
        self._palette_values = {v.upper() for v in self._palette.values()}

    def test_theme_dir_no_artix_in_content(self):
        """No file in the theme dir has 'artix' anywhere in its byte content."""
        for fpath in _THEME_DIR.rglob("*"):
            if not fpath.is_file():
                continue
            raw = fpath.read_bytes()
            try:
                text = raw.decode("utf-8", errors="replace").lower()
            except Exception:
                text = ""
            self.assertNotIn(
                "artix", text,
                f"{fpath.relative_to(_REPO_ROOT)} contains 'artix' in content",
            )

    def test_theme_dir_no_wheatley_in_content(self):
        """No file in the theme dir has 'wheatley' anywhere in its byte content."""
        for fpath in _THEME_DIR.rglob("*"):
            if not fpath.is_file():
                continue
            raw = fpath.read_bytes()
            try:
                text = raw.decode("utf-8", errors="replace").lower()
            except Exception:
                text = ""
            self.assertNotIn(
                "wheatley", text,
                f"{fpath.relative_to(_REPO_ROOT)} contains 'wheatley' in content",
            )

    def test_theme_txt_colors_in_palette(self):
        """Every hex color in theme.txt must be a value present in palette.json."""
        found = _HEX_IN_THEME_RE.findall(self._theme_text)
        self.assertGreater(len(found), 0, "theme.txt has no color values")
        for color in found:
            self.assertIn(
                color.upper(), self._palette_values,
                f"theme.txt color {color} is not in palette.json",
            )

    def test_theme_txt_image_refs_exist(self):
        """Non-glob image files referenced in theme.txt must exist in the theme dir."""
        refs = _IMG_REF_RE.findall(self._theme_text)
        for ref in refs:
            target = _THEME_DIR / ref
            self.assertTrue(
                target.exists(),
                f"theme.txt references '{ref}' but {target} does not exist",
            )

    def test_kernels_cfg_has_glue_linux_live(self):
        """kernels.cfg must contain 'Glue Linux (live)' as a menu entry title."""
        text = _KERNELS_CFG.read_text()
        self.assertIn("Glue Linux (live)", text)


class BrandingImagesTest(unittest.TestCase):
    """Test PNG images in glue-branding: solid backgrounds and pinned content."""

    def _parse_png_chunks(self, data):
        """Parse PNG file into list of (chunk_type, chunk_data) tuples.

        Validates PNG signature and returns chunks without CRC validation.
        """
        if data[:8] != b'\x89PNG\r\n\x1a\n':
            raise ValueError("Invalid PNG signature")
        chunks = []
        pos = 8
        while pos < len(data):
            if pos + 8 > len(data):
                break
            length = struct.unpack('>I', data[pos:pos+4])[0]
            chunk_type = data[pos+4:pos+8]
            chunk_data = data[pos+8:pos+8+length]
            chunks.append((chunk_type, chunk_data))
            pos += 12 + length
        return chunks

    def test_grub_background_is_the_wallpaper(self):
        """The boot menu uses the Stillwater wallpaper (user request 2026-10-04)."""
        self.assertEqual((_BRANDING_ROOT / "grub-background.png").read_bytes(),
                         (_BRANDING_ROOT / "wallpaper.png").read_bytes())

    def test_background_copies_byte_identical(self):
        """Both background.png files must be byte-for-byte identical."""
        primary = _BRANDING_ROOT / "grub-background.png"
        secondary = _REPO_ROOT / "iso-profile/glue/root-overlay/usr/share/grub/themes/artix/background.png"

        self.assertTrue(primary.exists(), f"Primary background not found: {primary}")
        self.assertTrue(secondary.exists(), f"Secondary background not found: {secondary}")

        primary_bytes = primary.read_bytes()
        secondary_bytes = secondary.read_bytes()

        self.assertEqual(primary_bytes, secondary_bytes,
            "background.png files are not byte-identical")

    def test_grub_icon_sha256_pinned(self):
        """grub-icon.png must have expected SHA256 (logo only, no text)."""
        icon_path = _BRANDING_ROOT / "grub-icon.png"
        self.assertTrue(icon_path.exists(), f"grub-icon.png not found")

        actual_sha256 = hashlib.sha256(icon_path.read_bytes()).hexdigest()
        # Verified manually: logo only, no text
        expected_sha256 = "374a32d644a8065f4a7da658456a3b4a3369d6fb6fb2ddbea01801381be2fb2d"

        self.assertEqual(actual_sha256, expected_sha256,
            "grub-icon.png content changed unexpectedly")

    def test_all_png_files_accounted_for(self):
        """Every PNG in glue-branding must be either verified as solid or pinned."""
        png_files = sorted(_BRANDING_ROOT.glob('*.png'))

        pinned_sha256 = {
            "grub-icon.png": "374a32d644a8065f4a7da658456a3b4a3369d6fb6fb2ddbea01801381be2fb2d",
            "wallpaper.png": "29986d12aa6b234ed72eead6d7fcfb1740ac847dc9812e92b446bfd7fe5ca4ca",
            "grub-background.png": "29986d12aa6b234ed72eead6d7fcfb1740ac847dc9812e92b446bfd7fe5ca4ca",
        }

        solid_backgrounds = set()

        for png_file in png_files:
            filename = png_file.name
            is_solid = filename in solid_backgrounds
            is_pinned = filename in pinned_sha256

            if is_solid:
                # no solid backgrounds left
                pass
            elif is_pinned:
                actual_sha256 = hashlib.sha256(png_file.read_bytes()).hexdigest()
                expected = pinned_sha256[filename]
                self.assertEqual(actual_sha256, expected,
                    f"{filename} SHA256 mismatch: {actual_sha256} != {expected}")
            else:
                self.fail(f"PNG file {filename} is not in solid backgrounds or pinned list")


class DefaultWallpaperTest(unittest.TestCase):

    def setUp(self):
        self.script = _BRANDING_ROOT / "glue-wallpaper-init"
        self.pkgbuild = (_BRANDING_ROOT / "PKGBUILD").read_text()

    def test_package_installs_wallpaper_initializer_and_first_login_marker(self):
        self.assertIn('"$pkgdir/usr/bin/glue-wallpaper-init"', self.pkgbuild)
        self.assertIn('"$pkgdir/etc/skel/.local/state/glue/wallpaper-pending"',
                      self.pkgbuild)
        self.assertTrue(self.script.stat().st_mode & 0o111)
        result = subprocess.run(["sh", "-n", str(self.script)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_gnome_applies_default_and_consumes_marker_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "state"
            marker = state / "glue/wallpaper-pending"
            marker.parent.mkdir(parents=True)
            marker.touch()
            wallpaper = root / "wallpaper.png"
            wallpaper.write_bytes(b"test wallpaper")
            bindir = root / "bin"
            bindir.mkdir()
            log = root / "gsettings.log"
            fake = bindir / "gsettings"
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SETTINGS_LOG"\n')
            fake.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "HOME": str(root), "XDG_STATE_HOME": str(state),
                "GLUE_WALLPAPER_PATH": str(wallpaper), "PATH": f"{bindir}:{env['PATH']}",
                "SETTINGS_LOG": str(log),
            })
            result = subprocess.run([str(self.script), "gnome"], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())
            calls = log.read_text().splitlines()
            self.assertEqual(len(calls), 3)
            self.assertIn("picture-uri file://", calls[0])
            self.assertIn("picture-uri-dark file://", calls[1])
            self.assertIn("picture-options zoom", calls[2])


if __name__ == "__main__":
    unittest.main()
