"""
Unit tests for wheatley_installer.catalog.

Run with:
  python3 -m unittest discover -s packages/wheatley-installer/tests \
                               -t packages/wheatley-installer -v
"""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

# Locate catalog.json relative to this file's package root
_PKG_ROOT = Path(__file__).parent.parent
_CATALOG_PATH = _PKG_ROOT / "catalog" / "catalog.json"

sys.path.insert(0, str(_PKG_ROOT))

from wheatley_installer.catalog import CatalogError, load_catalog


def _load_raw() -> dict:
    """Return the raw parsed JSON dict (not validated)."""
    return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))


def _write_tmp(data: dict) -> Path:
    """Write data as JSON to a temp file and return its Path."""
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(data, tf)
    tf.flush()
    tf.close()
    return Path(tf.name)


class TestPositive(unittest.TestCase):
    """Positive tests: the shipped catalog.json must load successfully."""

    def setUp(self):
        self.catalog = load_catalog(_CATALOG_PATH)

    def test_loads_successfully(self):
        self.assertIsNotNone(self.catalog)
        self.assertEqual(self.catalog.version, 1)

    def test_exactly_one_primary_kernel_and_it_is_cachyos(self):
        primaries = [k for k in self.catalog.kernels if k.primary]
        self.assertEqual(len(primaries), 1, "Expected exactly one primary kernel")
        self.assertEqual(primaries[0].id, "linux-cachyos")

    def test_linux_zen_is_secondary(self):
        zen = next((k for k in self.catalog.kernels if k.id == "linux-zen"), None)
        self.assertIsNotNone(zen, "linux-zen kernel missing")
        self.assertFalse(zen.primary)

    def test_dinit_is_recommended(self):
        dinit = next((i for i in self.catalog.inits if i.id == "dinit"), None)
        self.assertIsNotNone(dinit, "dinit init entry missing")
        self.assertTrue(dinit.recommended)

    def test_expected_session_ids(self):
        ids = {s.id for s in self.catalog.sessions}
        expected = {"apeturewm", "nvwm", "atomwm", "mangowc", "niri", "sway", "kde-plasma", "xfce"}
        self.assertEqual(ids, expected)

    def test_session_kinds(self):
        wms = {s.id for s in self.catalog.sessions if s.kind == "wm"}
        des = {s.id for s in self.catalog.sessions if s.kind == "de"}
        self.assertEqual(wms, {"apeturewm", "nvwm", "atomwm", "mangowc", "niri", "sway"})
        self.assertEqual(des, {"kde-plasma", "xfce"})

    def test_mangowc_shell_choices(self):
        mangowc = next(s for s in self.catalog.sessions if s.id == "mangowc")
        self.assertEqual(set(mangowc.shell_choices), {"noctalia", "imperative-dots"})

    def test_niri_shell_choices(self):
        niri = next(s for s in self.catalog.sessions if s.id == "niri")
        self.assertEqual(set(niri.shell_choices), {"noctalia", "imperative-dots"})

    def test_expected_shell_ids(self):
        ids = {s.id for s in self.catalog.shells}
        self.assertEqual(ids, {"noctalia", "imperative-dots"})

    def test_all_sessions_ease_lightness_in_range(self):
        for s in self.catalog.sessions:
            with self.subTest(session=s.id):
                self.assertGreaterEqual(s.ease, 1)
                self.assertLessEqual(s.ease, 5)
                self.assertGreaterEqual(s.lightness, 1)
                self.assertLessEqual(s.lightness, 5)

    def test_all_shells_ease_lightness_in_range(self):
        for s in self.catalog.shells:
            with self.subTest(shell=s.id):
                self.assertGreaterEqual(s.ease, 1)
                self.assertLessEqual(s.ease, 5)
                self.assertGreaterEqual(s.lightness, 1)
                self.assertLessEqual(s.lightness, 5)

    def test_all_sessions_have_five_or_more_keybindings(self):
        for s in self.catalog.sessions:
            with self.subTest(session=s.id):
                self.assertGreaterEqual(len(s.keybindings), 5, f"{s.id} has too few keybindings")

    def test_all_shells_have_five_or_more_keybindings(self):
        for s in self.catalog.shells:
            with self.subTest(shell=s.id):
                self.assertGreaterEqual(len(s.keybindings), 5, f"{s.id} has too few keybindings")

    def test_all_sessions_have_screenshot_value(self):
        for s in self.catalog.sessions:
            with self.subTest(session=s.id):
                self.assertIsNotNone(s.screenshot, f"{s.id} missing screenshot")
                self.assertIsInstance(s.screenshot, str)

    def test_all_shells_have_screenshot_value(self):
        for s in self.catalog.shells:
            with self.subTest(shell=s.id):
                self.assertIsNotNone(s.screenshot, f"{s.id} missing screenshot")
                self.assertIsInstance(s.screenshot, str)

    def test_keybinding_strings_nonempty(self):
        for s in self.catalog.sessions:
            for kb in s.keybindings:
                with self.subTest(session=s.id, keys=kb.keys):
                    self.assertTrue(kb.keys.strip())
                    self.assertTrue(kb.action.strip())

    def test_bluetooth_support_entry(self):
        bt = next((s for s in self.catalog.support if s.id == "bluetooth"), None)
        self.assertIsNotNone(bt, "bluetooth support entry missing")
        self.assertIn("bluez", bt.packages)
        self.assertIn("blueman", bt.packages)
        self.assertIn("bluetoothd", bt.services)
        self.assertFalse(bt.default)

    def test_gaming_section(self):
        g = self.catalog.gaming
        self.assertEqual(g.name, "Gaming Mode")
        self.assertTrue(g.gpu_autodetect)
        gaming_pkgs = set(g.packages)
        for pkg in ("steam", "heroic-games-launcher-bin", "proton-ge-custom-bin",
                    "vulkan-icd-loader", "lib32-vulkan-icd-loader",
                    "gamemode", "lib32-gamemode", "mangohud", "lib32-mangohud"):
            self.assertIn(pkg, gaming_pkgs, f"Gaming missing package: {pkg}")

    def test_minimal_section(self):
        m = self.catalog.minimal
        self.assertTrue(m.name)
        self.assertTrue(m.description)

    def test_eight_sessions_two_shells_two_kernels(self):
        self.assertEqual(len(self.catalog.sessions), 8)
        self.assertEqual(len(self.catalog.shells), 2)
        self.assertEqual(len(self.catalog.kernels), 2)


class TestNegative(unittest.TestCase):
    """Negative tests: mutated catalogs must raise CatalogError."""

    def _assert_catalog_error(self, data: dict, fragment: str = ""):
        """Write data to temp file, assert CatalogError is raised."""
        path = _write_tmp(data)
        with self.assertRaises(CatalogError) as ctx:
            load_catalog(path)
        if fragment:
            self.assertIn(fragment, str(ctx.exception),
                          f"Expected '{fragment}' in error: {ctx.exception}")

    def test_missing_required_top_level_key(self):
        data = _load_raw()
        del data["kernels"]
        self._assert_catalog_error(data, "kernels")

    def test_unknown_top_level_key(self):
        data = _load_raw()
        data["unexpected_key"] = "oops"
        self._assert_catalog_error(data, "unexpected_key")

    def test_duplicate_session_id(self):
        data = _load_raw()
        dupe = copy.deepcopy(data["sessions"][0])
        data["sessions"].append(dupe)
        self._assert_catalog_error(data, "duplicate id")

    def test_ease_out_of_range(self):
        data = _load_raw()
        data["sessions"][0]["ease"] = 9
        self._assert_catalog_error(data, "ease")

    def test_lightness_out_of_range_zero(self):
        data = _load_raw()
        data["sessions"][1]["lightness"] = 0
        self._assert_catalog_error(data, "lightness")

    def test_dangling_shell_choices_reference(self):
        data = _load_raw()
        # Add a shell_choice that references a nonexistent shell
        for s in data["sessions"]:
            if s["id"] == "mangowc":
                s["shell_choices"] = ["noctalia", "ghost-shell-does-not-exist"]
                break
        self._assert_catalog_error(data, "ghost-shell-does-not-exist")

    def test_two_primary_kernels(self):
        data = _load_raw()
        for k in data["kernels"]:
            k["primary"] = True
        self._assert_catalog_error(data, "primary")

    def test_zero_primary_kernels(self):
        data = _load_raw()
        for k in data["kernels"]:
            k["primary"] = False
        self._assert_catalog_error(data, "primary")

    def test_no_recommended_init(self):
        data = _load_raw()
        for i in data["inits"]:
            i["recommended"] = False
        self._assert_catalog_error(data, "recommended")

    def test_fewer_than_five_keybindings(self):
        data = _load_raw()
        data["sessions"][0]["keybindings"] = data["sessions"][0]["keybindings"][:3]
        self._assert_catalog_error(data, "keybindings")

    def test_missing_session_required_field(self):
        data = _load_raw()
        del data["sessions"][0]["ease"]
        self._assert_catalog_error(data, "ease")

    def test_invalid_kind(self):
        data = _load_raw()
        data["sessions"][0]["kind"] = "invalid-kind"
        self._assert_catalog_error(data, "kind")

    def test_duplicate_kernel_id(self):
        data = _load_raw()
        dupe = copy.deepcopy(data["kernels"][0])
        data["kernels"].append(dupe)
        # Two primaries AND duplicate id — both violations; check it raises
        self._assert_catalog_error(data)

    def test_ease_wrong_type(self):
        data = _load_raw()
        data["sessions"][0]["ease"] = "high"
        self._assert_catalog_error(data, "ease")

    def test_not_valid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            tf.write("{not valid json")
            path = Path(tf.name)
        with self.assertRaises(CatalogError) as ctx:
            load_catalog(path)
        self.assertIn("JSON", str(ctx.exception))

    def test_missing_shell_required_field(self):
        data = _load_raw()
        del data["shells"][0]["packages"]
        self._assert_catalog_error(data, "packages")

    def test_gaming_missing_gpu_autodetect(self):
        data = _load_raw()
        del data["gaming"]["gpu_autodetect"]
        self._assert_catalog_error(data, "gpu_autodetect")

    def test_duplicate_support_id(self):
        data = _load_raw()
        dupe = copy.deepcopy(data["support"][0])
        data["support"].append(dupe)
        self._assert_catalog_error(data, "duplicate id")


if __name__ == "__main__":
    unittest.main()
