"""Tests for glue_installer.swap (roadmap 4.1)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from glue_installer import plan as plan_mod
from glue_installer.swap import (
    GIB, MIB, SwapPlan, mkinitcpio_hooks_with_resume, parse_meminfo,
    resume_cmdline, swap_plan, zramen_conf,
)

# (ram GiB, disk GiB, hibernate) -> (zram GiB, disk GiB, effective hibernate)
_TABLE = {
    (1, 10, False): (1, 0, False),   (1, 10, True): (1, 0, False),
    (1, 30, False): (1, 2, False),   (1, 30, True): (1, 2, True),
    (1, 500, False): (1, 2, False),  (1, 500, True): (1, 2, True),
    (2, 10, False): (2, 0, False),   (2, 10, True): (2, 0, False),
    (2, 30, False): (2, 2, False),   (2, 30, True): (2, 3, True),
    (2, 500, False): (2, 2, False),  (2, 500, True): (2, 3, True),
    (4, 10, False): (4, 0, False),   (4, 10, True): (4, 0, False),
    (4, 30, False): (4, 4, False),   (4, 30, True): (4, 5, True),
    (4, 500, False): (4, 4, False),  (4, 500, True): (4, 5, True),
    (8, 10, False): (8, 0, False),   (8, 10, True): (8, 0, False),
    (8, 30, False): (8, 8, False),   (8, 30, True): (8, 9, True),
    (8, 500, False): (8, 8, False),  (8, 500, True): (8, 9, True),
    (16, 10, False): (8, 0, False),  (16, 10, True): (8, 0, False),
    (16, 30, False): (8, 4, False),  (16, 30, True): (8, 17, True),
    (16, 500, False): (8, 4, False), (16, 500, True): (8, 17, True),
    (32, 10, False): (8, 0, False),  (32, 10, True): (8, 0, False),
    (32, 30, False): (8, 4, False),  (32, 30, True): (8, 4, False),
    (32, 500, False): (8, 4, False), (32, 500, True): (8, 33, True),
    (64, 10, False): (8, 0, False),  (64, 10, True): (8, 0, False),
    (64, 30, False): (8, 2, False),  (64, 30, True): (8, 2, False),
    (64, 500, False): (8, 2, False), (64, 500, True): (8, 65, True),
}

_HOOKS = ["base", "udev", "autodetect", "modconf", "block", "filesystems",
          "keyboard", "fsck"]


class TestSwapTable(unittest.TestCase):
    def test_table_is_complete(self):
        self.assertEqual(len(_TABLE), 42)

    def test_full_table(self):
        for (ram, disk, hib), (zram, dsk, eff) in _TABLE.items():
            with self.subTest(ram=ram, disk=disk, hibernate=hib):
                p = swap_plan(ram * GIB, disk * GIB, hib)
                self.assertEqual(p.zram_bytes, zram * GIB)
                self.assertEqual(p.disk_bytes, dsk * GIB)
                self.assertEqual(p.hibernate, eff)
                self.assertEqual((p.zram_priority, p.disk_priority), (100, 10))
                self.assertEqual(bool(p.warnings), hib and not eff)

    def test_disk_between_12_and_24_caps_to_1gib(self):
        p = swap_plan(4 * GIB, 20 * GIB, True)
        self.assertEqual(p.disk_bytes, GIB)
        self.assertFalse(p.hibernate)
        self.assertTrue(p.warnings)

    def test_warning_text(self):
        p = swap_plan(16 * GIB, 10 * GIB, True)
        self.assertIn("17 GiB", p.warnings[0])
        self.assertTrue(p.warnings[0].startswith("Hibernation disabled"))

    def test_fractional_ram_rounds_to_mib(self):
        ram = 16306512 * 1024
        p = swap_plan(ram, 500 * GIB, True)
        self.assertEqual(p.disk_bytes % MIB, 0)
        self.assertGreaterEqual(p.disk_bytes, ram + GIB)
        self.assertTrue(p.hibernate)

    def test_hibernate_swap_never_exceeds_disk_minus_reserve(self):
        for ram in (1, 2, 4, 8, 16, 32, 64, 128):
            for disk in (12, 24, 30, 48, 64, 100, 500):
                with self.subTest(ram=ram, disk=disk):
                    p = swap_plan(ram * GIB, disk * GIB, True)
                    if p.hibernate:
                        self.assertLessEqual(p.disk_bytes, (disk - 12) * GIB)
                    self.assertLessEqual(p.disk_bytes, disk * GIB)

    def test_properties(self):
        p = swap_plan(16 * GIB, 500 * GIB, True)
        self.assertEqual((p.zram_mib, p.disk_mib), (8192, 17408))
        self.assertEqual(p.disk_gib_human, "17 GiB")
        self.assertEqual(SwapPlan(0, disk_bytes=GIB * 3 // 2).disk_gib_human, "1.5 GiB")


class TestSwapModes(unittest.TestCase):
    def test_zram_mode(self):
        p = swap_plan(16 * GIB, 500 * GIB, False, "zram")
        self.assertEqual((p.zram_bytes, p.disk_bytes), (8 * GIB, 0))
        self.assertFalse(p.warnings)

    def test_none_mode(self):
        p = swap_plan(16 * GIB, 500 * GIB, False, "none")
        self.assertEqual((p.zram_bytes, p.disk_bytes), (0, 0))

    def test_hibernate_with_non_auto_mode_warns(self):
        for mode in ("zram", "none"):
            with self.subTest(mode=mode):
                p = swap_plan(16 * GIB, 500 * GIB, True, mode)
                self.assertFalse(p.hibernate)
                self.assertEqual(p.disk_bytes, 0)
                self.assertTrue(p.warnings)

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            swap_plan(GIB, GIB, mode="bogus")
        with self.assertRaises(ValueError):
            swap_plan(0, GIB)
        with self.assertRaises(ValueError):
            swap_plan(GIB, -1)


class TestMeminfo(unittest.TestCase):
    def test_parse(self):
        text = ("MemTotal:       16306512 kB\nMemFree:         1234 kB\n"
                "MemAvailable:    999 kB\n")
        self.assertEqual(parse_meminfo(text), 16306512 * 1024)

    def test_missing(self):
        with self.assertRaises(ValueError):
            parse_meminfo("MemFree: 1 kB\nSwapTotal: 0 kB\n")


class TestZramenConf(unittest.TestCase):
    def test_paths_match_plan_and_prefix(self):
        p = swap_plan(16 * GIB, 500 * GIB)
        expect = {"dinit": "/etc/dinit.d/config/zramen.conf",
                  "runit": "/etc/runit/sv/zramen/conf",
                  "openrc": "/etc/conf.d/zramen"}
        for init, path in expect.items():
            with self.subTest(init=init):
                got_path, content = zramen_conf(p, 16 * GIB, init)
                self.assertEqual(got_path, path)
                self.assertEqual(plan_mod._ZRAMEN_CONF[init][0], path)
                self.assertIn("ZRAM_COMP_ALGORITHM=zstd", content)
                self.assertIn("ZRAM_SIZE=50\n".replace("\n", ""), content)
                self.assertIn("ZRAM_MAX_SIZE=8192", content)
                self.assertIn("ZRAM_PRIORITY=100", content)
                self.assertEqual(content.startswith("export "), init == "runit")
                if init == "runit":
                    self.assertTrue(all(l.startswith("export ")
                                        for l in content.splitlines()))

    def test_small_ram_is_100_percent(self):
        _, content = zramen_conf(swap_plan(GIB, 500 * GIB), GIB, "dinit")
        self.assertIn("ZRAM_SIZE=100", content)
        self.assertIn("ZRAM_MAX_SIZE=1024", content)

    def test_unknown_init_and_no_zram(self):
        p = swap_plan(8 * GIB, 100 * GIB)
        self.assertIsNone(zramen_conf(p, 8 * GIB, "s6"))
        self.assertIsNone(zramen_conf(swap_plan(8 * GIB, 100 * GIB, mode="none"),
                                      8 * GIB, "dinit"))


class TestResume(unittest.TestCase):
    def test_cmdline(self):
        u = "0a1b2c3d-1234-5678-9abc-def012345678"
        self.assertEqual(resume_cmdline(u), f"resume=UUID={u}")

    def test_cmdline_invalid(self):
        for bad in ("", "nope", "0a1b2c3d-1234-5678-9abc-def01234567",
                    "0a1b2c3d-1234-5678-9abc-def012345678 quiet", "/dev/sda2"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                resume_cmdline(bad)

    def test_hooks_insert_after_filesystems(self):
        out = mkinitcpio_hooks_with_resume(_HOOKS)
        self.assertEqual(out, ["base", "udev", "autodetect", "modconf", "block",
                               "filesystems", "resume", "keyboard", "fsck"])
        self.assertNotIn("resume", _HOOKS)

    def test_hooks_idempotent(self):
        once = mkinitcpio_hooks_with_resume(_HOOKS)
        self.assertEqual(mkinitcpio_hooks_with_resume(once), once)

    def test_hooks_without_filesystems(self):
        with self.assertRaises(ValueError):
            mkinitcpio_hooks_with_resume(["base", "udev"])


if __name__ == "__main__":
    unittest.main()
