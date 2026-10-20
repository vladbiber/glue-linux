"""Security page: page texts, globalstorage -> selection -> plan, and the
glue-security step, for each level alone with gaming on and off."""

import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.calamares_adapter import AdapterError, compile_adapter, parse_selection
from glue_installer.calamares_config import (
    GS_PREFIX, PAGE_IDS, SECURITY_LEVELS, build_pages, selection_from_globalstorage)
from glue_installer.catalog import load_catalog
from glue_installer.disks import DiskPlan, PartitionSpec
from glue_installer.executor import compile_steps
from glue_installer.plan import PlanError, resolve_plan
from glue_installer.plan_security import LEVELS, security_steps

_CATALOG = load_catalog(_PKG_ROOT / "catalog" / "catalog.json")
_KEY = GS_PREFIX + "gluesecurity"
_GIB = 2 ** 30
_SESSION = {"sessions": ["gluewc"], "shell": "glueqs"}
# a session that ships Firefox itself (catalog), so "super" has something to swap
_FIREFOX_SESSION = {"sessions": ["gluewc-caelestia"]}


def _sel(level, gaming, base=_SESSION):
    d = dict(base, gaming=gaming)
    if level is not None:
        d["security_level"] = level
    return parse_selection(d, _CATALOG)


def _mime(plan):
    return [f.content for f in plan.files if f.path.endswith("-mimeapps.list")]


class PageTest(unittest.TestCase):
    def setUp(self):
        self.page = {p.id: p for p in build_pages(_CATALOG)}["gluesecurity"]

    def test_after_secure_boot_basic_default(self):
        self.assertEqual(PAGE_IDS[-2:], ("gluesecureboot", "gluesecurity"))
        self.assertEqual(self.page.mode, "required")
        self.assertEqual(self.page.item_ids(),
                         ["security-basic", "security-hardened", "security-super"])
        self.assertEqual(self.page.default, "security-basic")
        self.assertEqual([i.badge for i in self.page.items], ["Recommended", "", ""])
        self.assertEqual(sorted(SECURITY_LEVELS.values()), sorted(LEVELS))

    def test_texts(self):
        self.assertIn("Partitions page", self.page.subtitle)
        self.assertIn("changed later in Settings", self.page.subtitle)
        basic, hardened, sup = (" ".join(i.points) for i in self.page.items)
        self.assertIn("Better protected out of the box than Arch, Artix or Linux Mint", basic)
        self.assertIn("Zero impact on games", basic)
        self.assertNotIn("insecure", (basic + self.page.items[0].description).lower())
        self.assertIn("Cheat Engine", hardened)
        self.assertIn("1-2%", hardened)
        for needle in ("USBGuard", "LibreWolf", "lockdown", "NVIDIA", "hibernation", "Steam"):
            self.assertIn(needle, sup)
        self.assertIn("Not for gaming", self.page.items[2].description)
        for item in self.page.items:
            self.assertNotIn("—", item.description + " ".join(item.points))

    def test_globalstorage_to_selection(self):
        for item_id, level in SECURITY_LEVELS.items():
            sel = selection_from_globalstorage({_KEY: item_id}, _CATALOG)
            self.assertEqual(sel, {"security_level": level})
            self.assertEqual(parse_selection(dict(sel, **_SESSION), _CATALOG).security_level,
                             level)
        self.assertEqual(parse_selection(_SESSION, _CATALOG).security_level, "basic")
        self.assertNotIn("security_level", selection_from_globalstorage({}, _CATALOG))
        with self.assertRaises(ValueError):
            selection_from_globalstorage({_KEY: "security-basic,security-super"}, _CATALOG)
        with self.assertRaises(AdapterError):
            parse_selection(dict(_SESSION, security_level="paranoid"), _CATALOG)


class PlanTest(unittest.TestCase):
    def test_every_level_alone_gaming_on_and_off(self):
        for level in LEVELS:
            for gaming in (False, True):
                with self.subTest(level=level, gaming=gaming):
                    plan = resolve_plan(_CATALOG, _sel(level, gaming), gpu_vendors={"amd"})
                    self.assertIn("glue-security", plan.packages)
                    self.assertNotIn("linux-hardened", plan.packages)
                    self.assertEqual(plan.security_level, level)
                    super_warn = [w for w in plan.warnings if w.startswith("Super hardened")]
                    if level == "super":
                        self.assertNotIn("firefox", plan.packages)
                        self.assertEqual(len(super_warn), 1 if gaming else 0)
                        if gaming:
                            self.assertIn("librewolf", plan.packages)
                    else:
                        self.assertEqual(super_warn, [])
                        self.assertNotIn("librewolf", plan.packages)
                        self.assertEqual("firefox" in plan.packages, gaming)

    def test_super_swaps_the_session_browser(self):
        for level in LEVELS:
            plan = resolve_plan(_CATALOG, _sel(level, False, _FIREFOX_SESSION))
            mime = _mime(plan)
            self.assertTrue(mime)
            if level == "super":
                self.assertIn("librewolf", plan.packages)
                self.assertNotIn("firefox", plan.packages)
                self.assertTrue(all("librewolf.desktop" in m and "firefox" not in m for m in mime))
            else:
                self.assertIn("firefox", plan.packages)
                self.assertTrue(all("firefox.desktop" in m for m in mime))

    def test_super_warns_about_nvidia(self):
        for level in LEVELS:
            plan = resolve_plan(_CATALOG, _sel(level, False), gpu_vendors={"nvidia"})
            nv = [w for w in plan.warnings if "skips kernel lockdown" in w]
            self.assertEqual(len(nv), 1 if level == "super" else 0, level)

    def test_minimal_install_gets_the_package(self):
        plan = resolve_plan(_CATALOG, parse_selection({"sessions": []}, _CATALOG))
        self.assertIn("glue-security", plan.packages)
        sel = parse_selection({"sessions": []}, _CATALOG)
        sel.security_level = "bogus"
        with self.assertRaises(PlanError):
            resolve_plan(_CATALOG, sel)


class StepTest(unittest.TestCase):
    def compile(self, level, gaming):
        return compile_adapter(_sel(level, gaming), _CATALOG, root_mount="/tmp/calamares-root",
                               ram_bytes=16 * _GIB, is_laptop=False, firmware="uefi",
                               is_mount=lambda p: True, root_bytes=100 * _GIB)

    def test_level_runs_in_chroot_before_the_boot_menu(self):
        for level in LEVELS:
            for gaming in (False, True):
                with self.subTest(level=level, gaming=gaming):
                    r = self.compile(level, gaming)
                    argvs = [s.argv for s in r.steps if hasattr(s, "argv")]
                    want = ["artix-chroot", "/tmp/calamares-root", "glue-security", "level", level]
                    self.assertEqual(argvs.count(want), 1)
                    i = argvs.index(want)
                    boot = next(j for j, a in enumerate(argvs)
                                if "glue-boot-update --deploy" in a[-1])
                    services = [j for j, a in enumerate(argvs) if "ln" in a]
                    self.assertGreater(i, max(services))
                    self.assertLess(i, boot)
                    self.assertIn("glue-security", r.packages)
                    self.assertNotIn("linux-hardened", r.packages)
                    kernel = "linux-hardened" if level == "super" else "linux-cachyos"
                    self.assertIn(f"GLUE_BOOT_KERNEL={kernel}\n", argvs[boot][-1])

    def test_tui_install_runs_it_too(self):
        plan = resolve_plan(_CATALOG, _sel("hardened", False))
        disk = DiskPlan("/dev/sda", "uefi", (
            PartitionSpec(1, "/dev/sda1", "ef00", "+512M", "vfat", "/boot"),
            PartitionSpec(2, "/dev/sda2", "8300", "0", "ext4", "/")))
        steps = compile_steps(plan, init_id="dinit", disk_plan=disk)
        self.assertEqual(steps[-2], security_steps("hardened", "/mnt")[0])
        self.assertNotIn(security_steps("hardened", "/mnt")[0],
                         compile_steps(plan, init_id="dinit"))
        with self.assertRaises(PlanError):
            security_steps("x", "/mnt")


if __name__ == "__main__":
    unittest.main()
