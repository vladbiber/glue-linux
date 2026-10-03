"""
Unit tests for glue_installer.catalog.

Run with:
  python3 -m unittest discover -s packages/glue-installer/tests \
                               -t packages/glue-installer -v
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

from glue_installer.catalog import CatalogError, load_catalog


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
        # mangowc was REMOVED (unsupported on the user's GPU, like quickshell)
        ids = {s.id for s in self.catalog.sessions}
        expected = {"apeturewm", "nvwm", "atomwm", "niri", "sway", "kde-plasma", "gnome", "xfce"}
        self.assertEqual(ids, expected)

    def test_session_kinds(self):
        wms = {s.id for s in self.catalog.sessions if s.kind == "wm"}
        des = {s.id for s in self.catalog.sessions if s.kind == "de"}
        self.assertEqual(wms, {"apeturewm", "nvwm", "atomwm", "niri", "sway"})
        self.assertEqual(des, {"kde-plasma", "gnome", "xfce"})

    def test_only_noctalia_shell_remains(self):
        """mangowc + the glue-bar/imperative-qs quickshell bars stay gone;
        noctalia is back as niri's single optional shell (user request)."""
        self.assertEqual([s.id for s in self.catalog.shells], ["noctalia"])
        niri = next(s for s in self.catalog.sessions if s.id == "niri")
        self.assertEqual(niri.shell_choices, ["noctalia"])
        for s in self.catalog.sessions:
            if s.id != "niri":
                self.assertEqual(s.shell_choices, [], s.id)
            for pkg in s.packages:
                self.assertNotIn("quickshell", pkg)
                self.assertNotIn("glue-bar", pkg)
                self.assertNotIn("mangowm", pkg)

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

    def test_shells_may_have_zero_keybindings(self):
        # a bar/shell has no hotkeys of its own (the compositor owns keybinds);
        # the validator only enforces the >=5 minimum for sessions
        for s in self.catalog.shells:
            with self.subTest(shell=s.id):
                self.assertIsInstance(s.keybindings, list)

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
        for pkg in ("steam", "heroic-games-launcher-bin",
                    "vulkan-icd-loader", "lib32-vulkan-icd-loader",
                    "gamemode", "lib32-gamemode", "mangohud"):
            self.assertIn(pkg, gaming_pkgs, f"Gaming missing package: {pkg}")

    def test_minimal_section(self):
        m = self.catalog.minimal
        self.assertTrue(m.name)
        self.assertTrue(m.description)

    def test_eight_sessions_one_shell_two_kernels(self):
        self.assertEqual(len(self.catalog.sessions), 8)
        self.assertEqual(len(self.catalog.shells), 1)
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
            if s["id"] == "niri":
                s["shell_choices"] = ["ghost-shell-does-not-exist"]
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
        # the real catalog ships no shells anymore — inject a broken one
        data["shells"] = [{
            "id": "broken-shell", "name": "Broken", "description": "x",
            "ease": 3, "lightness": 3, "keybindings": [], "screenshot": None,
        }]
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


class TestSchedulers(unittest.TestCase):
    """1.4: gaming.schedulers block."""

    def _assert_catalog_error(self, data, needle):
        path = _write_tmp(data)
        with self.assertRaises(CatalogError) as ctx:
            load_catalog(path)
        self.assertIn(needle, str(ctx.exception))

    def test_real_catalog_lists_lavd_bpfland_none_with_lavd_default(self):
        cat = load_catalog(_CATALOG_PATH)
        ids = [o.id for o in cat.gaming.schedulers]
        self.assertEqual(ids, ["scx_lavd", "scx_bpfland", "none"])
        self.assertEqual([o.id for o in cat.gaming.schedulers if o.default],
                         ["scx_lavd"])

    def test_schedulers_block_is_optional(self):
        data = _load_raw()
        del data["gaming"]["schedulers"]
        cat = load_catalog(_write_tmp(data))
        self.assertEqual(cat.gaming.schedulers, [])

    def test_duplicate_scheduler_id(self):
        data = _load_raw()
        data["gaming"]["schedulers"].append(
            copy.deepcopy(data["gaming"]["schedulers"][0]))
        self._assert_catalog_error(data, "duplicate scheduler ids")

    def test_two_defaults(self):
        data = _load_raw()
        data["gaming"]["schedulers"][1]["default"] = True
        self._assert_catalog_error(data, "exactly one scheduler")

    def test_no_default(self):
        data = _load_raw()
        data["gaming"]["schedulers"][0]["default"] = False
        self._assert_catalog_error(data, "exactly one scheduler")

    def test_scheduler_missing_field(self):
        data = _load_raw()
        del data["gaming"]["schedulers"][0]["description"]
        self._assert_catalog_error(data, "description")


if __name__ == "__main__":
    unittest.main()
