"""Run inside artix-chroot without --root: files only, commands without a prefix."""

import unittest
from unittest import mock

from helpers import TargetCase

from glue_security import target
from glue_security.actions import browser_files
from glue_security.target import Target


class ChrootTest(TargetCase):
    def test_chroot_is_not_live(self):
        with mock.patch.object(target, "in_chroot", return_value=True):
            t = Target(None)
        self.assertFalse(t.live)
        self.assertFalse(t.outside)
        self.assertFalse(t.live_run(["sysctl", "-w", "x=1"]))
        self.assertEqual(self.commands(), [])

    def test_running_system_is_live(self):
        with mock.patch.object(target, "in_chroot", return_value=False):
            self.assertTrue(Target(None).live)
        self.assertFalse(Target(str(self.root)).live)

    def test_install_inside_chroot_has_no_prefix(self):
        with mock.patch.object(target, "in_chroot", return_value=True):
            t = Target(None)
        seen = []
        with mock.patch.object(Target, "installed", lambda self, n: bool(seen)):
            with mock.patch.object(Target, "run", lambda self, argv, **k: seen.append(argv)):
                t.install(["apparmor"])
        self.assertEqual(seen, [["pacman", "-S", "--needed", "--noconfirm", "apparmor"]])

    def test_init_from_target_packages_not_pid1(self):
        # in a chroot /proc/1/comm is the live ISO's init
        for init in ("runit", "openrc"):
            root = self.root.parent / init
            (root / "var/lib/pacman/local" / f"{init}-1-1").mkdir(parents=True)
            (root / "var/lib/pacman/local" / f"{init}-1-1" / "desc").write_text("x")
            with mock.patch.object(target, "in_chroot", return_value=False):
                t = Target(None)
            t.root = root
            self.assertEqual(t.init(), init)

    def test_real_in_chroot_never_crashes(self):
        self.assertIn(target.in_chroot(), (True, False))


class DesktopMimeappsTest(TargetCase):
    def test_desktop_specific_file_follows(self):
        xdg = self.f("etc/xdg")
        xdg.mkdir(parents=True)
        (xdg / "gluewc-mimeapps.list").write_text(
            "[Default Applications]\nx-scheme-handler/https=firefox.desktop\n")
        (xdg / "other-mimeapps.list").write_text(
            "[Default Applications]\nimage/png=loupe.desktop\n")
        self.assertEqual(browser_files(Target(str(self.root))),
                         ["etc/xdg/mimeapps.list", "etc/xdg/gluewc-mimeapps.list"])
        self.ok("enable", "librewolf")
        self.assertIn("x-scheme-handler/https=librewolf.desktop",
                      (xdg / "gluewc-mimeapps.list").read_text())
        self.assertTrue(self.status()["components"]["librewolf"])
        self.ok("disable", "librewolf")
        self.assertEqual((xdg / "gluewc-mimeapps.list").read_text(),
                         "[Default Applications]\nx-scheme-handler/https=firefox.desktop\n")
        self.assertEqual((xdg / "other-mimeapps.list").read_text(),
                         "[Default Applications]\nimage/png=loupe.desktop\n")
        self.assertFalse(self.f("etc/xdg/mimeapps.list").exists())

    def test_installer_already_set_librewolf(self):
        xdg = self.f("etc/xdg")
        xdg.mkdir(parents=True)
        (xdg / "gluewc-mimeapps.list").write_text(
            "[Default Applications]\nx-scheme-handler/http=librewolf.desktop\n"
            "x-scheme-handler/https=librewolf.desktop\n")
        self.ok("level", "super")
        self.assertEqual(self.status()["level"], "super")


if __name__ == "__main__":
    unittest.main()
