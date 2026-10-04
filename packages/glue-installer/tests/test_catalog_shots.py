"""`screenshot` (legacy) vs `screenshots` (list) in catalog.json."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.catalog import CatalogError, load_catalog
from glue_installer.ui_view import session_item, shell_item

_CATALOG_PATH = _PKG_ROOT / "catalog" / "catalog.json"


def _raw():
    return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))


def _load(data):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "catalog.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return load_catalog(path)


def _with_session(**fields):
    data = _raw()
    sess = data["sessions"][0]
    sess.pop("screenshots", None)
    sess.update(fields)
    return data


class RealCatalogTest(unittest.TestCase):
    def test_json_uses_list_only_and_python_keeps_first(self):
        raw = _raw()
        for entry in raw["sessions"] + raw["shells"]:
            self.assertNotIn("screenshot", entry, entry["id"])
            self.assertTrue(entry["screenshots"], entry["id"])
        cat = load_catalog(_CATALOG_PATH)
        for entry in cat.sessions + cat.shells:
            self.assertEqual(entry.screenshot, entry.screenshots[0], entry.id)

    def test_every_listed_file_exists(self):
        cat = load_catalog(_CATALOG_PATH)
        for entry in cat.sessions + cat.shells:
            for rel in entry.screenshots:
                self.assertTrue((_PKG_ROOT / "catalog" / rel).is_file(), rel)

    def test_item_carries_the_whole_list(self):
        cat = load_catalog(_CATALOG_PATH)
        item = session_item(cat.sessions[0], False)
        self.assertEqual(item.screenshots, tuple(cat.sessions[0].screenshots))
        self.assertEqual(item.screenshot, cat.sessions[0].screenshot)
        self.assertEqual(shell_item(cat.shells[0], False).screenshots,
                         tuple(cat.shells[0].screenshots))


class ParseShotsTest(unittest.TestCase):
    def test_legacy_singular_still_loads(self):
        sess = _load(_with_session(screenshot="screenshots/xfce.png")).sessions[0]
        self.assertEqual(sess.screenshot, "screenshots/xfce.png")
        self.assertEqual(sess.screenshots, ["screenshots/xfce.png"])

    def test_legacy_null_means_no_screenshot(self):
        sess = _load(_with_session(screenshot=None)).sessions[0]
        self.assertIsNone(sess.screenshot)
        self.assertEqual(sess.screenshots, [])

    def test_three_images(self):
        shots = ["screenshots/a.png", "screenshots/b.png", "screenshots/c.png"]
        sess = _load(_with_session(screenshots=shots)).sessions[0]
        self.assertEqual(sess.screenshots, shots)
        self.assertEqual(sess.screenshot, "screenshots/a.png")

    def test_both_fields_agreeing(self):
        sess = _load(_with_session(screenshot="a.png",
                                   screenshots=["a.png", "b.png"])).sessions[0]
        self.assertEqual((sess.screenshot, sess.screenshots), ("a.png", ["a.png", "b.png"]))

    def test_shell_accepts_list(self):
        data = _raw()
        data["shells"][0].pop("screenshots", None)
        data["shells"][0]["screenshots"] = ["screenshots/a.png", "screenshots/b.png"]
        self.assertEqual(_load(data).shells[0].screenshot, "screenshots/a.png")

    def _bad(self, needle, **fields):
        with self.assertRaises(CatalogError) as ctx:
            _load(_with_session(**fields))
        self.assertIn(needle, str(ctx.exception))

    def test_conflict(self):
        self._bad("must equal", screenshot="b.png", screenshots=["a.png", "b.png"])

    def test_conflict_with_null_legacy(self):
        self._bad("must equal", screenshot=None, screenshots=["a.png"])

    def test_parent_dir(self):
        self._bad("'..'", screenshots=["screenshots/../../etc/passwd"])
        self._bad("'..'", screenshot="../x.png")

    def test_absolute(self):
        self._bad("relative", screenshots=["/usr/share/x.png"])

    def test_duplicate(self):
        self._bad("duplicate", screenshots=["a.png", "b.png", "a.png"])

    def test_empty_list(self):
        self._bad("non-empty list", screenshots=[])

    def test_non_string_and_empty_entries(self):
        self._bad("non-empty strings", screenshots=["a.png", 3])
        self._bad("non-empty strings", screenshots=[""])
        self._bad("non-empty list", screenshots="a.png")

    def test_neither_field(self):
        self._bad("screenshots")


if __name__ == "__main__":
    unittest.main()
