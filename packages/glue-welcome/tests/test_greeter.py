import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from glue_welcome import greeter

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\0" * 32
WEBP = b"RIFF\0\0\0\0WEBPVP8 " + b"\0" * 32

CONFIG = """\
# /etc/greetd/regreet.toml
skip_selection = false

[background]
path = "/usr/share/backgrounds/glue/wallpaper.png"
fit = "Contain"

[GTK]
theme_name = "Adwaita"
"""


class TestImages(unittest.TestCase):
    def test_magic_bytes(self):
        self.assertEqual(greeter.image_type(PNG), "png")
        self.assertEqual(greeter.image_type(JPG), "jpg")
        self.assertEqual(greeter.image_type(WEBP), "webp")
        self.assertIsNone(greeter.image_type(b"root:x:0:0:"))
        self.assertIsNone(greeter.image_type(b"<svg xmlns="))

    def test_size_limit(self):
        with self.assertRaises(ValueError):
            greeter.read_image(io.BytesIO(PNG + b"\0" * 64), limit=64)
        data, kind = greeter.read_image(io.BytesIO(PNG), limit=64)
        self.assertEqual((data, kind), (PNG, "png"))

    def test_rejects_non_image(self):
        with self.assertRaises(ValueError):
            greeter.read_image(io.BytesIO(b"#!/bin/sh\necho hi\n"))


class TestToml(unittest.TestCase):
    def test_replaces_path_and_fit_only(self):
        out = greeter.set_background(CONFIG, "/var/lib/glue/greeter-background.jpg")
        self.assertIn('path = "/var/lib/glue/greeter-background.jpg"', out)
        self.assertIn('fit = "Cover"', out)
        self.assertNotIn("Contain", out)
        self.assertEqual(out.count("\n"), CONFIG.count("\n"))
        self.assertIn('[GTK]\ntheme_name = "Adwaita"', out)
        self.assertTrue(out.startswith("# /etc/greetd/regreet.toml\nskip_selection"))

    def test_adds_missing_keys_inside_section(self):
        out = greeter.set_background('[background]\n\n[GTK]\nx = 1\n', "/a.png")
        section = out.split("[GTK]")[0]
        self.assertIn('path = "/a.png"', section)
        self.assertIn('fit = "Cover"', section)

    def test_adds_section_when_absent(self):
        out = greeter.set_background('[GTK]\nx = 1\n\n', "/a.png")
        self.assertTrue(out.endswith('[background]\npath = "/a.png"\nfit = "Cover"\n'))
        self.assertIn("[GTK]\nx = 1\n", out)

    def test_path_with_quote_is_escaped_and_read_back(self):
        out = greeter.set_background(CONFIG, '/tmp/a"b.png')
        self.assertIn(r'path = "/tmp/a\"b.png"', out)
        self.assertEqual(greeter.current_background(out), '/tmp/a"b.png')

    def test_current_background(self):
        self.assertEqual(greeter.current_background(CONFIG), greeter.DEFAULT)
        self.assertEqual(greeter.current_background("[GTK]\npath = \"/x\"\n"),
                         greeter.DEFAULT)

    def test_installer_config_round_trip(self):
        sys.path.insert(0, str(ROOT.parent / "glue-installer"))
        from glue_installer.plan_greeter import _REGREET_CONFIG_CONTENT
        out = greeter.set_background(_REGREET_CONFIG_CONTENT, "/var/lib/glue/g.png")
        self.assertEqual(greeter.current_background(out), "/var/lib/glue/g.png")
        cleared = greeter.set_background(out, greeter.DEFAULT)
        self.assertEqual(cleared.strip(), _REGREET_CONFIG_CONTENT.strip())


class TestApply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.config = base / "regreet.toml"
        self.config.write_text(CONFIG)
        self.state = base / "state"

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_then_switch_type_then_clear(self):
        target = greeter.apply_set(io.BytesIO(PNG), self.config, self.state)
        self.assertEqual(target.read_bytes(), PNG)
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        self.assertEqual(greeter.current_background(self.config.read_text()), str(target))
        jpg = greeter.apply_set(io.BytesIO(JPG), self.config, self.state)
        self.assertFalse(target.exists())
        self.assertEqual(greeter.current_background(self.config.read_text()), str(jpg))
        greeter.apply_clear(self.config, self.state)
        self.assertFalse(jpg.exists())
        self.assertEqual(greeter.current_background(self.config.read_text()), greeter.DEFAULT)
        self.assertIn('[GTK]\ntheme_name = "Adwaita"', self.config.read_text())

    def test_bad_image_changes_nothing(self):
        with self.assertRaises(ValueError):
            greeter.apply_set(io.BytesIO(b"not an image"), self.config, self.state)
        self.assertEqual(self.config.read_text(), CONFIG)

    def test_does_not_follow_symlinked_target(self):
        self.state.mkdir()
        victim = Path(self.tmp.name) / "victim"
        victim.write_text("keep")
        os.symlink(victim, self.state / "greeter-background.png")
        greeter.apply_set(io.BytesIO(PNG), self.config, self.state)
        self.assertEqual(victim.read_text(), "keep")
        self.assertFalse((self.state / "greeter-background.png").is_symlink())

    def test_availability(self):
        self.assertIsNotNone(greeter.availability(dri=str(Path(self.tmp.name) / "none*")))


class TestPackagingHelper(unittest.TestCase):
    def test_helper_and_policy_installed(self):
        pkgbuild = (ROOT / "PKGBUILD").read_text()
        self.assertIn("/usr/lib/glue/greeter-background", pkgbuild)
        self.assertIn("polkit-1/actions/org.glue.greeter-background.policy", pkgbuild)
        policy = (ROOT / "data/polkit/org.glue.greeter-background.policy").read_text()
        self.assertIn(">/usr/lib/glue/greeter-background<", policy)
        self.assertTrue(os.access(ROOT / "data/helpers/greeter-background", os.X_OK))


if __name__ == "__main__":
    unittest.main()
