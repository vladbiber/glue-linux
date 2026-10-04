"""calamares_config (10.3 lot 2): sequence, generated pages, gluefstab job."""

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.calamares_adapter import parse_selection
from glue_installer.calamares_config import (
    EXEC, FORBIDDEN_MODULES, GS_PREFIX, PAGE_IDS, ConfigError, append_fstab_line,
    build_pages, build_settings, has_page_values, render_all, render_page_conf,
    render_settings_conf, selection_from_globalstorage, sequence_modules)
from glue_installer.catalog import load_catalog

_CATALOG = load_catalog(_PKG_ROOT / "catalog" / "catalog.json")
_CFG = _PKG_ROOT.parent / "glue-calamares-config"
_SETTINGS = build_settings(_CATALOG)
# `ls /usr/lib/calamares/modules` of Artix calamares 3.4.2 (glue-cala-img, 2026-10-04);
# anything else in the sequence must be a Glue module shipped in the package.
_STANDARD_MODULES = frozenset("""
bootloader contextualprocess displaymanager dracut dracutlukscfg finished finishedq
fsresizer fstab grubcfg hostinfo hwclock initcpio initcpiocfg keyboard keyboardq license
locale localecfg localeq luksbootkeyfile luksopenswaphookcfg machineid mkinitfs mount
netinstall networkcfg notesqml openrcdmcryptcfg packagechooser packagechooserq packages
partition plasmalnf plymouthcfg preservefiles rawfs removeuser services-openrc shellprocess
summary summaryq umount unpackfs unpackfsc users usersq welcome welcomeq zfs zfshostid
""".split())


def _phase(settings, name, index):
    phase = settings["sequence"][index]
    return phase[name]


class TestSequence(unittest.TestCase):
    def test_exec_order_exact(self):
        self.assertEqual(_phase(_SETTINGS, "exec", 1), [
            "partition", "mount", "glueinstall", "fstab", "gluefstab", "locale",
            "keyboard", "localecfg", "users", "umount"])
        self.assertEqual(tuple(_phase(_SETTINGS, "exec", 1)), EXEC)
        self.assertEqual(_phase(_SETTINGS, "show", 2), ["finished"])

    def test_show_order_pages_between_users_and_summary(self):
        show = _phase(_SETTINGS, "show", 0)
        pages = [f"packagechooser@{p}" for p in PAGE_IDS]
        self.assertEqual(show, ["welcome", "locale", "keyboard", "partition", "users",
                                "notesqml@gluegallery"] + pages + ["summary"])
        self.assertEqual([i["id"] for i in _SETTINGS["instances"]],
                         ["gluegallery"] + list(PAGE_IDS))
        for inst in _SETTINGS["instances"]:
            expected = "notesqml" if inst["id"] == "gluegallery" else "packagechooser"
            self.assertEqual(inst["module"], expected)
            self.assertTrue((_CFG / "modules" / inst["config"]).is_file())

    def test_forbidden_modules_absent(self):
        mods = set(sequence_modules(_SETTINGS))
        for bad in ("bootloader", "services-systemd", "pacstrap", "chwd", "shellprocess",
                    "initcpio", "initcpiocfg", "unpackfs", "packages", "displaymanager"):
            self.assertIn(bad, FORBIDDEN_MODULES)
        self.assertFalse(mods & FORBIDDEN_MODULES, mods & FORBIDDEN_MODULES)
        text = render_settings_conf(_SETTINGS)
        self.assertNotIn("systemctl", text)
        self.assertNotIn("shellprocess", text)
        self.assertEqual(text.count("  - glueinstall\n"), 1)

    def test_every_module_exists(self):
        for name in sequence_modules(_SETTINGS):
            if name in _STANDARD_MODULES:
                continue
            desc = _CFG / "modules" / name / "module.desc"
            self.assertTrue(desc.is_file(), f"{name}: neither standard nor shipped")
            self.assertIn(f'name: "{name}"', desc.read_text())
            entry = f"{name}.qml" if name == "gluegallery" else "main.py"
            self.assertTrue((_CFG / "modules" / name / entry).is_file())

    def test_committed_files_equal_generator_output(self):
        files = render_all(_CATALOG)
        self.assertEqual(set(files), {
            "settings.conf", "gluegallery.json", "modules/gluegallery.conf",
            "modules/gluegallery/GalleryData.js"}
                         | {f"modules/{p}.conf" for p in PAGE_IDS})
        for rel, text in files.items():
            self.assertEqual((_CFG / rel).read_bytes(), text.encode("utf-8"), rel)

    def test_rendered_yaml_parses(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        for rel, text in render_all(_CATALOG).items():
            if not rel.endswith(".conf"):
                continue
            data = yaml.safe_load(text)
            self.assertIsInstance(data, dict, rel)
        settings = yaml.safe_load(render_settings_conf(_SETTINGS))
        self.assertEqual(settings["sequence"][1]["exec"], list(EXEC))
        self.assertEqual(settings["branding"], "glue")
        self.assertEqual(settings["modules-search"], ["local", "/etc/calamares/modules"])
        self.assertIs(settings["prompt-install"], True)


class TestPages(unittest.TestCase):
    def setUp(self):
        self.pages = {p.id: p for p in build_pages(_CATALOG)}

    def test_sessions_page_covers_catalog_with_gluewc_default(self):
        page = self.pages["gluesessions"]
        self.assertEqual(page.mode, "optionalmultiple")
        self.assertEqual(page.item_ids(), [s.id for s in _CATALOG.sessions])
        self.assertEqual(len(page.item_ids()), 6)
        self.assertEqual(page.default, "gluewc")
        self.assertEqual(page.items[0].id, "")
        self.assertIn("tick several", page.items[0].description)  # 5.1b notice
        kde = next(i for i in page.items if i.id == "kde-plasma")
        self.assertIn("Recommended for beginners", kde.description)
        for item in page.items[1:]:
            self.assertTrue(item.screenshot.startswith("/usr/share/glue-installer/catalog/"))
            shot = _PKG_ROOT / "catalog" / item.screenshot.split("/catalog/", 1)[1]
            self.assertTrue(shot.is_file(), shot)

    def test_shell_kernel_init_pages(self):
        shell = self.pages["glueshell"]
        self.assertEqual((shell.mode, shell.default), ("required", "glueqs"))
        self.assertEqual(shell.item_ids(), [s.id for s in _CATALOG.shells])
        self.assertEqual(shell.item_ids(), ["glueqs", "noctalia"])
        kernel = self.pages["gluekernel"]
        self.assertEqual((kernel.mode, kernel.default), ("required", "linux-cachyos"))
        self.assertEqual(kernel.item_ids(), [k.id for k in _CATALOG.kernels])
        init = self.pages["glueinit"]
        self.assertEqual((init.mode, init.default), ("required", "dinit"))
        self.assertEqual(init.item_ids(), ["dinit", "runit", "openrc"])

    def test_gaming_and_extras_pages(self):
        gaming = self.pages["gluegaming"]
        self.assertEqual((gaming.mode, gaming.default), ("optional", None))
        self.assertEqual(gaming.item_ids(), ["gaming-scx_lavd", "gaming-scx_bpfland",
                                             "gaming-none"])
        extras = self.pages["glueextras"]
        self.assertEqual((extras.mode, extras.default), ("optionalmultiple", "app-store"))
        self.assertEqual(extras.item_ids(), ["app-store", "bluetooth"])
        text = render_page_conf(extras)
        self.assertIn("method: legacy", text)
        self.assertIn('step: "Extras"', text)
        self.assertIn('default: "app-store"', text)

    def test_item_texts_come_from_catalog(self):
        for page in self.pages.values():
            for item in page.items:
                self.assertTrue(item.name and item.description, (page.id, item.id))
        names = {s.id: s.name for s in _CATALOG.sessions}
        for item in self.pages["gluesessions"].items[1:]:
            self.assertEqual(item.name, names[item.id])


class TestSelectionFromGlobalstorage(unittest.TestCase):
    def test_full_round_trip(self):
        gs = {GS_PREFIX + "gluesessions": ",gluewc,kde-plasma",
              GS_PREFIX + "glueshell": "noctalia", GS_PREFIX + "gluekernel": "linux-zen",
              GS_PREFIX + "glueinit": "runit", GS_PREFIX + "gluegaming": "gaming-scx_bpfland",
              GS_PREFIX + "glueextras": "bluetooth", "rootMountPoint": "/tmp/x"}
        sel = selection_from_globalstorage(gs, _CATALOG)
        self.assertEqual(sel, {"sessions": ["gluewc", "kde-plasma"], "shell": "noctalia",
                               "kernel": "linux-zen", "init": "runit", "gaming": True,
                               "scheduler": "scx_bpfland", "app_store": False,
                               "bluetooth": True})
        parsed = parse_selection(sel, _CATALOG)
        self.assertEqual(parsed.shell_choice, {"gluewc": "noctalia"})
        self.assertEqual(parsed.support_ids, ["bluetooth"])
        self.assertTrue(has_page_values(gs))

    def test_defaults_and_empty_pages(self):
        gs = {GS_PREFIX + "gluesessions": "gluewc", GS_PREFIX + "glueshell": "glueqs",
              GS_PREFIX + "gluekernel": "linux-cachyos", GS_PREFIX + "glueinit": "dinit",
              GS_PREFIX + "gluegaming": "", GS_PREFIX + "glueextras": "app-store"}
        sel = selection_from_globalstorage(gs, _CATALOG)
        self.assertEqual(sel["gaming"], False)
        self.assertNotIn("scheduler", sel)
        self.assertEqual((sel["app_store"], sel["bluetooth"]), (True, False))
        parsed = parse_selection(sel, _CATALOG)
        self.assertEqual((parsed.kernel_id, parsed.init_id, parsed.scheduler),
                         ("linux-cachyos", "dinit", "scx_lavd"))
        # minimal: no session, nothing else written
        self.assertEqual(selection_from_globalstorage({GS_PREFIX + "gluesessions": ""}, _CATALOG),
                         {"sessions": []})
        self.assertTrue(parse_selection({"sessions": []}, _CATALOG).minimal)
        self.assertFalse(has_page_values({"rootMountPoint": "/x"}))
        self.assertFalse(has_page_values({GS_PREFIX + "gluekernel": None}))

    def test_invalid_values_raise_config_error(self):
        bad = [{GS_PREFIX + "gluesessions": "sway"}, {GS_PREFIX + "gluekernel": ""},
               {GS_PREFIX + "gluekernel": "linux-cachyos,linux-zen"},
               {GS_PREFIX + "gluegaming": "gaming-scx_lavd,gaming-none"},
               {GS_PREFIX + "glueshell": ["glueqs"]}, {GS_PREFIX + "glueextras": "wifi"}]
        for gs in bad:
            with self.assertRaises(ConfigError, msg=gs):
                selection_from_globalstorage(gs, _CATALOG)
        self.assertTrue(issubclass(ConfigError, ValueError))


class _FakeGS:
    def __init__(self, data):
        self.data = dict(data)

    def value(self, key):
        return self.data.get(key)

    def insert(self, key, value):
        self.data[key] = value


def _fake_calamares(gs_data, config=None):
    cala = types.ModuleType("libcalamares")
    cala.globalstorage = _FakeGS(gs_data)
    cala.job = types.SimpleNamespace(configuration=config or {}, progress=[],
                                     setprogress=lambda v: cala.job.progress.append(v))
    cala.log = []
    cala.utils = types.SimpleNamespace(debug=cala.log.append, warning=cala.log.append)
    return cala


def _load_gluefstab():
    path = _CFG / "modules" / "gluefstab" / "main.py"
    spec = importlib.util.spec_from_file_location("gluefstab_main", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestGluefstab(unittest.TestCase):
    LINE = "/swapfile none swap defaults 0 0\n"

    def setUp(self):
        self.mod = _load_gluefstab()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        (Path(self.root) / "etc").mkdir()
        self.fstab = Path(self.root) / "etc" / "fstab"

    def tearDown(self):
        self.tmp.cleanup()
        sys.modules.pop("libcalamares", None)

    def _run(self, gs, config=None):
        cala = _fake_calamares(gs, config)
        sys.modules["libcalamares"] = cala
        return self.mod.run(is_mount=lambda p: p == self.root), cala

    def test_appends_once_over_two_runs(self):
        self.fstab.write_text("# Static information about the filesystems.\n"
                              "UUID=abc / ext4 defaults 0 1")  # no trailing newline
        gs = {"rootMountPoint": self.root, "glue_swapfile_fstab_line": self.LINE}
        for _ in range(2):
            result, cala = self._run(gs)
            self.assertIsNone(result)
            self.assertEqual(cala.job.progress, [1.0])
        text = self.fstab.read_text()
        self.assertEqual(text.count("/swapfile"), 1)
        self.assertTrue(text.endswith("UUID=abc / ext4 defaults 0 1\n"
                                      "/swapfile none swap defaults 0 0\n"))
        self.assertFalse(append_fstab_line(str(self.fstab), "/swapfile\tnone swap sw 0 0"))

    def test_noop_without_line_and_creates_missing_fstab(self):
        result, cala = self._run({"rootMountPoint": self.root})
        self.assertIsNone(result)
        self.assertFalse(self.fstab.exists())
        self.assertTrue(any("nothing to do" in line for line in cala.log))
        result, _ = self._run({"rootMountPoint": self.root + "/",
                               "glue_swapfile_fstab_line": self.LINE})
        self.assertIsNone(result)
        self.assertEqual(self.fstab.read_text(), self.LINE)

    def test_clear_errors(self):
        for gs in ({"glue_swapfile_fstab_line": self.LINE},
                   {"rootMountPoint": None, "glue_swapfile_fstab_line": self.LINE}):
            result, _ = self._run(gs)
            self.assertEqual(result[0], "Glue Linux swapfile fstab step rejected")
            self.assertIn("rootMountPoint is missing", result[1])
        result, _ = self._run({"rootMountPoint": "/", "glue_swapfile_fstab_line": self.LINE})
        self.assertIn("refusing to install onto /", result[1])
        result, _ = self._run({"rootMountPoint": 42, "glue_swapfile_fstab_line": self.LINE})
        self.assertIn("absolute path", result[1])
        result, _ = self._run({"rootMountPoint": "/tmp/not-a-mount",
                               "glue_swapfile_fstab_line": self.LINE})
        self.assertIn("not a mountpoint", result[1])
        result, _ = self._run({"rootMountPoint": self.root, "glue_swapfile_fstab_line": 7})
        self.assertIn("must be a string", result[1])
        result, _ = self._run({"rootMountPoint": self.root, "glue_swapfile_fstab_line": "a\nb"})
        self.assertEqual(result[0], "Glue Linux swapfile fstab step failed")
        result, _ = self._run({"rootMountPoint": self.root, "glue_swapfile_fstab_line": self.LINE},
                              {"fstab": "../etc/fstab"})
        self.assertIn("absolute path inside the target", result[1])
        self.assertFalse(self.fstab.exists())

    def test_module_files(self):
        d = _CFG / "modules" / "gluefstab"
        desc = (d / "module.desc").read_text()
        for line in ('type: "job"', 'interface: "python"', 'name: "gluefstab"',
                     'script: "main.py"'):
            self.assertIn(line, desc)
        self.assertIn("fstab: /etc/fstab", (d.parent / "gluefstab.conf").read_text())
        self.assertNotIn("import libcalamares",
                         (_PKG_ROOT / "glue_installer" / "calamares_config.py").read_text())


class TestPackaging(unittest.TestCase):
    def test_pkgbuild_installs_modules_and_pages(self):
        text = (_CFG / "PKGBUILD").read_text()
        for needle in ("settings.conf", "modules/*.conf", "glueinstall gluefstab",
                       "module.desc", "main.py", "branding.desc", "'glue-installer'"):
            self.assertIn(needle, text)
        self.assertNotIn("Co-Authored", text)

    def test_no_file_swap_choice_in_partition_conf(self):
        text = (_CFG / "modules" / "partition.conf").read_text()
        self.assertIn("userSwapChoices: [ none, small ]", text)
        self.assertNotIn("file ]", text)

    def test_files_under_500_lines(self):
        for path in [_PKG_ROOT / "glue_installer" / "calamares_config.py",
                     _CFG / "modules" / "gluefstab" / "main.py",
                     _CFG / "modules" / "glueinstall" / "main.py", Path(__file__)]:
            self.assertLess(len(path.read_text().splitlines()), 500, path)


class TestGallery(unittest.TestCase):
    def _catalog(self, shots):
        cat = load_catalog(_PKG_ROOT / "catalog" / "catalog.json")
        cat.sessions[0].screenshots = shots
        cat.sessions[0].screenshot = shots[0]
        return cat

    def test_card_uses_first_image_and_gallery_has_all(self):
        shots = ["screenshots/a.png", "screenshots/b.png"]
        cat = self._catalog(shots)
        files = render_all(cat)
        root = "/usr/share/glue-installer/catalog/"
        page = files["modules/gluesessions.conf"]
        self.assertIn(f'screenshot: "{root}screenshots/a.png"', page)
        self.assertNotIn("screenshots/b.png", page)
        self.assertNotIn("gallery", page)
        gallery = json.loads(files["gluegallery.json"])
        self.assertEqual(gallery[cat.sessions[0].id], [root + s for s in shots])

    def test_committed_gallery_lists_every_catalog_image(self):
        gallery = json.loads((_CFG / "gluegallery.json").read_text())
        for entry in _CATALOG.sessions + _CATALOG.shells:
            self.assertEqual(
                gallery[entry.id],
                ["/usr/share/glue-installer/catalog/" + s for s in entry.screenshots])
        self.assertNotIn("", gallery)


if __name__ == "__main__":
    unittest.main()
