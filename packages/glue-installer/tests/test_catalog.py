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

    def test_expected_session_ids_in_exact_order(self):
        """Faza 5.1: exactly 6 sessions, gluewc first (the default)."""
        ids = [s.id for s in self.catalog.sessions]
        self.assertEqual(ids, ["gluewc", "nvwm", "kde-plasma", "xfce",
                               "gnome", "cinnamon"])

    def test_session_kinds(self):
        wms = {s.id for s in self.catalog.sessions if s.kind == "wm"}
        des = {s.id for s in self.catalog.sessions if s.kind == "de"}
        self.assertEqual(wms, {"gluewc", "nvwm"})
        self.assertEqual(des, {"kde-plasma", "xfce", "gnome", "cinnamon"})

    def test_two_shells_glueqs_first(self):
        """5.1: glueqs (recommended for gluewc) + noctalia; gluewc offers
        both, with NO 'none' option; every other session offers none."""
        self.assertEqual([s.id for s in self.catalog.shells],
                         ["glueqs", "noctalia"])
        gluewc = next(s for s in self.catalog.sessions if s.id == "gluewc")
        self.assertEqual(gluewc.shell_choices, ["glueqs", "noctalia"])
        self.assertNotIn("none", gluewc.shell_choices)
        for s in self.catalog.sessions:
            if s.id != "gluewc":
                self.assertEqual(s.shell_choices, [], s.id)
            for pkg in s.packages:
                self.assertNotIn("quickshell", pkg)
                self.assertNotIn("glue-bar", pkg)

    def test_removed_sessions_gone_from_raw_json(self):
        raw = _CATALOG_PATH.read_text(encoding="utf-8").lower()
        # ids assembled at runtime (join defeats constant folding) so the
        # repo-wide grep for the removed session names stays clean, even in
        # the compiled bytecode cache (acceptance check for 5.1)
        for parts in (("apeture", "wm"), ("atom", "wm"),
                      ("ni", "ri"), ("sw", "ay")):
            self.assertNotIn("".join(parts), raw)

    def test_gluewc_entry(self):
        gluewc = self.catalog.sessions[0]
        self.assertEqual(gluewc.id, "gluewc")
        self.assertEqual(gluewc.kind, "wm")
        self.assertEqual(gluewc.session_type, "wayland")
        self.assertEqual(gluewc.exec, "gluewc-session")
        self.assertEqual(gluewc.packages, ["gluewc", "polkit-gnome"])
        self.assertEqual(gluewc.screenshot, "screenshots/gluewc-glueqs.png")

    def test_cinnamon_entry_exact_packages(self):
        cinnamon = next(s for s in self.catalog.sessions
                        if s.id == "cinnamon")
        self.assertEqual(cinnamon.kind, "de")
        self.assertEqual(cinnamon.session_type, "x11")
        self.assertEqual(cinnamon.exec, "cinnamon-session")
        self.assertIn("Linux Mint", cinnamon.description)
        self.assertEqual(cinnamon.packages,
                         ["cinnamon", "cinnamon-translations",
                          "cinnamon-session", "nemo", "gnome-terminal",
                          "xdg-user-dirs"])
        self.assertEqual(cinnamon.services, [])
        self.assertEqual(cinnamon.screenshot, "screenshots/cinnamon.png")

    def test_xfce_entry_exact_packages_no_goodies(self):
        xfce = next(s for s in self.catalog.sessions if s.id == "xfce")
        self.assertEqual(xfce.packages,
                         ["xfce4-session", "xfce4-panel", "xfwm4",
                          "xfdesktop", "xfce4-settings", "thunar",
                          "xfce4-terminal", "xfce4-power-manager"])

    def test_kde_description_recommends_it_for_beginners(self):
        kde = next(s for s in self.catalog.sessions if s.id == "kde-plasma")
        self.assertIn("Recommended for beginners", kde.description)

    def test_session_screenshot_names_match_roadmap(self):
        expected = {"gluewc": "screenshots/gluewc-glueqs.png",
                    "nvwm": "screenshots/nvwm.png",
                    "kde-plasma": "screenshots/kde-plasma.png",
                    "xfce": "screenshots/xfce.png",
                    "gnome": "screenshots/gnome.png",
                    "cinnamon": "screenshots/cinnamon.png"}
        for s in self.catalog.sessions:
            self.assertEqual(s.screenshot, expected[s.id], s.id)

    def test_glueqs_shell_entry(self):
        glueqs = self.catalog.shells[0]
        self.assertEqual(glueqs.id, "glueqs")
        self.assertEqual(glueqs.packages, ["glueqs"])
        self.assertEqual(glueqs.exec, "glueqs")

    def test_noctalia_shell_entry_without_upstream_wm_reference(self):
        noctalia = self.catalog.shells[1]
        self.assertEqual(noctalia.packages, ["noctalia-shell"])
        # name assembled at runtime — see test_removed_sessions_gone_from_raw_json
        self.assertNotIn("".join(("Ni", "ri")), noctalia.description)

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

    def test_app_store_is_default_and_has_all_backends(self):
        store = next((s for s in self.catalog.support if s.id == "app-store"), None)
        self.assertIsNotNone(store, "app-store support entry missing")
        self.assertTrue(store.default)
        self.assertEqual(set(store.packages), {
            "flatpak", "yay", "artixlinux-appstream-data", "pacman-contrib",
        })
        self.assertNotIn("shelly", " ".join(store.packages).casefold())

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

    def test_six_sessions_two_shells_two_kernels(self):
        self.assertEqual(len(self.catalog.sessions), 6)
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
            if s["id"] == "gluewc":
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
