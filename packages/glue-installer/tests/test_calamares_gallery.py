"""calamares_gallery (5.6b lot 3): generated data for the QML gallery page."""

import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.calamares_config import (
    SCREENSHOT_ROOT, Item, Page, build_pages, build_settings, render_all)
from glue_installer.calamares_gallery import (
    build_gallery_entries, parse_gallery_data_js, render_gallery_data_js,
    render_gallery_qml_conf)
from glue_installer.catalog import load_catalog

_CATALOG = load_catalog(_PKG_ROOT / "catalog" / "catalog.json")
_CFG = _PKG_ROOT.parent / "glue-calamares-config"
_ENTRIES = build_gallery_entries(build_pages(_CATALOG))
_BY_ID = {e["id"]: e for e in _ENTRIES}
_CATALOG_DIR = _PKG_ROOT / "catalog"


class TestEntries(unittest.TestCase):
    def test_order_sessions_then_shells(self):
        self.assertEqual([e["id"] for e in _ENTRIES],
                         ["gluewc", "nvwm", "kde-plasma", "xfce", "gnome", "cinnamon",
                          "glueqs", "noctalia"])
        self.assertEqual([e["kind"] for e in _ENTRIES], ["session"] * 6 + ["shell"] * 2)
        self.assertTrue(all(e["name"] for e in _ENTRIES))

    def test_shells_have_two_distinct_images_that_exist(self):
        for sid in ("glueqs", "noctalia"):
            imgs = _BY_ID[sid]["images"]
            self.assertEqual(len(imgs), 2)
            self.assertEqual(len(set(imgs)), 2)
            self.assertIn("bar", imgs[0])
            self.assertIn("overview", imgs[1])
        for e in _ENTRIES:
            for url in e["images"]:
                prefix = "file://" + SCREENSHOT_ROOT + "/"
                self.assertTrue(url.startswith(prefix), url)
                self.assertTrue((_CATALOG_DIR / url[len(prefix):]).is_file(), url)

    def test_item_without_images_still_emitted(self):
        pages = [Page("gluesessions", "S", "optionalmultiple",
                      [Item("", "notice", "x"), Item("bare", "Bare", "d")]),
                 Page("glueshell", "H", "required", [Item("sh", "Sh", "d", gallery=[])])]
        entries = build_gallery_entries(pages)
        self.assertEqual(entries, [
            {"id": "bare", "name": "Bare", "kind": "session", "images": []},
            {"id": "sh", "name": "Sh", "kind": "shell", "images": []}])

    def test_missing_page_rejected(self):
        with self.assertRaises(ValueError):
            build_gallery_entries([])


class TestRender(unittest.TestCase):
    def test_data_js_round_trips(self):
        text = render_gallery_data_js(_ENTRIES)
        self.assertTrue(text.startswith("// Generated"))
        self.assertIn("do not edit", text.splitlines()[0])
        self.assertIn(".pragma library", text)
        self.assertEqual(parse_gallery_data_js(text), _ENTRIES)
        self.assertEqual(len(parse_gallery_data_js(text)), 8)

    def test_non_ascii_kept(self):
        text = render_gallery_data_js([{"id": "a", "name": "Ünï", "kind": "session",
                                        "images": []}])
        self.assertIn("Ünï", text)

    def test_qml_conf(self):
        self.assertIn('step: "Previews"', render_gallery_qml_conf())

    def test_no_artix_word(self):
        for text in (render_gallery_data_js(_ENTRIES), render_gallery_qml_conf()):
            self.assertNotIn("artix", text.lower())


class TestIntegration(unittest.TestCase):
    def test_gallery_right_before_sessions_chooser(self):
        show = build_settings(_CATALOG)["sequence"][0]["show"]
        self.assertEqual(show[show.index("packagechooser@gluesessions") - 1],
                         "gluegallery")
        self.assertEqual(show.count("gluegallery"), 1)
        settings_conf = (_CFG / "settings.conf").read_text().splitlines()
        i = settings_conf.index("  - packagechooser@gluesessions")
        self.assertEqual(settings_conf[i - 1], "  - gluegallery")

    def test_checked_in_equals_generator(self):
        files = render_all(_CATALOG)
        for rel in ("settings.conf", "modules/gluegallery.conf",
                    "modules/gluegallery/GalleryData.js"):
            self.assertEqual((_CFG / rel).read_bytes(), files[rel].encode("utf-8"), rel)

    def test_static_files_and_pkgbuild(self):
        d = _CFG / "modules" / "gluegallery"
        desc = (d / "module.desc").read_text()
        for needle in ('type: "view"', 'interface: "qtquick"', 'name: "gluegallery"',
                       'qmlPath: "gluegallery.qml"'):
            self.assertIn(needle, desc)
        qml = (d / "gluegallery.qml").read_text()
        for needle in ("No preview available", "Previous", "Next", "Nav.label("):
            self.assertIn(needle, qml)
        pkgbuild = (_CFG / "PKGBUILD").read_text()
        for name in ("module.desc", "gluegallery.qml", "GalleryNav.js", "GalleryData.js"):
            self.assertIn(name, pkgbuild)
        self.assertTrue((_CFG / "modules" / "gluegallery.conf").is_file())


if __name__ == "__main__":
    unittest.main()
