"""Unit tests for glue_installer.clone (offline install = rsync clone, 3.6)."""

import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.clone import (
    CLONE_INIT, CLONE_KERNEL, LIVE_ONLY_FILES, LIVE_ONLY_PACKAGES, LIVE_USER,
    MKINITCPIO_HOOKS, RSYNC_EXCLUDES, CloneError, clone_steps,
)
from glue_installer.disks import BlockDevice, plan_disk
from glue_installer.disk_swap import plan_disk_with_swap
from glue_installer.executor import RunCommand, WriteTargetFile
from glue_installer.limine import BootSpec
from glue_installer.plan import InstallPlan, PlannedFile
from glue_installer.swap import SwapPlan

_DEV = BlockDevice(name="sda", path="/dev/sda", size_bytes=64 * 1024 ** 3,
                   model="t", is_removable=False, has_mounted_partitions=False)
_UEFI = plan_disk(_DEV, "uefi")
_BIOS = plan_disk(_DEV, "bios")
_BOOT = BootSpec(kernel="linux", cmdline_extra=("amd_pstate=active",))


def _plan():
    return InstallPlan(
        packages=["linux-cachyos", "steam"], services=["NetworkManager", "greetd"],
        files=[PlannedFile("/etc/glue/a.conf", "a\n", 0o644),
               PlannedFile("/etc/zramen.conf", "z\n", 0o644)],
        warnings=[],
    )


def _argv_text(step):
    return " ".join(getattr(step, "argv", []))


def _find(steps, needle):
    return next(i for i, s in enumerate(steps) if needle in _argv_text(s))


class TestOrder(unittest.TestCase):
    def _check_order(self, disk_plan):
        steps = clone_steps(_plan(), disk_plan, boot=_BOOT)
        mount_at = max(i for i, s in enumerate(steps)
                       if isinstance(s, RunCommand) and s.argv[0] == "mount")
        rsync_at = _find(steps, "rsync -aAXH")
        fstab_at = _find(steps, "fstabgen -U /mnt")
        clean_at = _find(steps, "userdel -r glue")
        file_at = next(i for i, s in enumerate(steps) if isinstance(s, WriteTargetFile))
        svc_at = _find(steps, "ln -sf /etc/runit/sv/greetd")
        kernel_at = _find(steps, "mkinitcpio -P")
        self.assertLess(mount_at, rsync_at)
        self.assertLess(rsync_at, fstab_at)
        self.assertLess(fstab_at, clean_at)
        self.assertLess(clean_at, file_at)
        self.assertLess(file_at, svc_at)
        self.assertLess(svc_at, kernel_at)
        self.assertEqual(kernel_at, len(steps) - 2)
        last = steps[-1]
        self.assertEqual(last.argv[:4], ["artix-chroot", "/mnt", "sh", "-c"])
        self.assertIn("glue-boot-update --deploy", last.argv[4])
        self.assertIn("GLUE_BOOT_KERNEL=linux", last.argv[4])
        return steps

    def test_uefi_order(self):
        steps = self._check_order(_UEFI)
        self.assertIn("GLUE_BOOT_FIRMWARE=uefi", steps[-1].argv[4])
        self.assertTrue(any("mkfs.fat" in _argv_text(s) for s in steps))

    def test_bios_order(self):
        steps = self._check_order(_BIOS)
        self.assertIn("GLUE_BOOT_FIRMWARE=bios", steps[-1].argv[4])
        self.assertFalse(any("mkfs.fat" in _argv_text(s) for s in steps))

    def test_swap_steps_between_mount_and_rsync(self):
        swap = SwapPlan(zram_bytes=2 ** 30, disk_bytes=4 * 2 ** 30, hibernate=False,
                        warnings=[])
        dp = plan_disk_with_swap(_DEV, "uefi", swap)
        steps = clone_steps(_plan(), dp, boot=_BOOT)
        self.assertLess(_find(steps, "mount /dev/sda3 /mnt"), _find(steps, "swapon"))
        self.assertLess(_find(steps, "swapon"), _find(steps, "rsync -aAXH"))

    def test_deterministic(self):
        self.assertEqual(clone_steps(_plan(), _UEFI, boot=_BOOT),
                         clone_steps(_plan(), _UEFI, boot=_BOOT))

    def test_custom_target(self):
        steps = clone_steps(_plan(), _UEFI, boot=_BOOT, target="/target/")
        rsync = steps[_find(steps, "rsync -aAXH")]
        self.assertEqual(rsync.argv[-2:], ["/", "/target/"])
        self.assertIn("fstabgen -U /target >> /target/etc/fstab",
                      " ".join(_argv_text(s) for s in steps))


class TestRsync(unittest.TestCase):
    def setUp(self):
        steps = clone_steps(_plan(), _UEFI, boot=_BOOT)
        self.rsync = steps[_find(steps, "rsync -aAXH")]

    def test_flags(self):
        self.assertEqual(self.rsync.argv[:4],
                         ["rsync", "-aAXH", "--numeric-ids", "--info=progress2"])

    def test_all_excludes(self):
        excludes = [a[len("--exclude="):] for a in self.rsync.argv
                    if a.startswith("--exclude=")]
        self.assertEqual(excludes, list(RSYNC_EXCLUDES))
        for must in ("/dev/*", "/proc/*", "/sys/*", "/run/*", "/mnt/*", "/boot/*",
                     "/etc/fstab", "/etc/machine-id", "/home/glue", "/swapfile",
                     "/var/cache/pacman/pkg/*", "/run/artix"):
            self.assertIn(must, excludes)

    def test_source_and_destination(self):
        self.assertEqual(self.rsync.argv[-2:], ["/", "/mnt/"])


class TestCleanupStep(unittest.TestCase):
    def setUp(self):
        steps = clone_steps(_plan(), _UEFI, boot=_BOOT)
        self.step = steps[_find(steps, "userdel -r glue")]
        self.script = self.step.argv[4]

    def test_is_chrooted_sh(self):
        self.assertEqual(self.step.argv[:4], ["artix-chroot", "/mnt", "sh", "-c"])
        self.assertTrue(self.script.startswith("set -e\n"))

    def test_userdel_guarded_by_id(self):
        self.assertIn(f"if id {LIVE_USER} >/dev/null 2>&1; then userdel -r {LIVE_USER}",
                      self.script)
        self.assertEqual(LIVE_USER, "glue")

    def test_live_only_files_removed(self):
        for f in LIVE_ONLY_FILES:
            self.assertIn(f, self.script)
        self.assertIn("/etc/profile.d/glue-live.sh", LIVE_ONLY_FILES)
        self.assertIn("/etc/sudoers.d/10-glue-live", LIVE_ONLY_FILES)

    def test_autologin_removed(self):
        self.assertIn("sed -i 's/ --autologin root//' /etc/runit/sv/agetty-tty1/conf",
                      self.script)

    def test_packages_removed_only_if_present(self):
        self.assertIn("pacman -Rns --noconfirm", self.script)
        self.assertIn('pacman -Qq "$p"', self.script)
        for p in ("glue-installer", "rsync", "os-prober", "grub", "artix-live-runit",
                  "artix-grub-live", "calamares", "kpmcore", "ckbcomp"):
            self.assertIn(p, LIVE_ONLY_PACKAGES)
            self.assertIn(p, self.script)

    def test_machine_id_and_boot_d(self):
        self.assertIn("rm -f /etc/machine-id && dbus-uuidgen --ensure=/etc/machine-id",
                      self.script)
        self.assertIn("mkdir -p /etc/glue/boot.d", self.script)


class TestKernelStep(unittest.TestCase):
    def setUp(self):
        steps = clone_steps(_plan(), _UEFI, boot=_BOOT)
        self.script = steps[_find(steps, "mkinitcpio -P")].argv[4]

    def test_vmlinuz_from_modules_dir(self):
        self.assertIn("for d in /usr/lib/modules/*", self.script)
        self.assertIn('"$d/pkgbase"', self.script)
        self.assertIn('install -Dm644 "$d/vmlinuz" "/boot/vmlinuz-$(cat "$d/pkgbase")"',
                      self.script)

    def test_hooks_rewritten_whole_line_then_mkinitcpio(self):
        self.assertEqual(MKINITCPIO_HOOKS, "HOOKS=(base udev autodetect modconf kms "
                         "keyboard keymap consolefont block filesystems fsck)")
        self.assertIn(f"sed -i 's|^HOOKS=.*|{MKINITCPIO_HOOKS}|' /etc/mkinitcpio.conf",
                      self.script)
        self.assertLess(self.script.index("HOOKS="), self.script.index("mkinitcpio -P"))

    def test_kernel_step_before_bootloader_resume_hook(self):
        steps = clone_steps(_plan(), _UEFI, boot=_BOOT)
        self.assertLess(_find(steps, "mkinitcpio -P"), _find(steps, "glue-boot-update"))


class TestConfigReuse(unittest.TestCase):
    def test_services_guarded(self):
        steps = clone_steps(_plan(), _UEFI, boot=_BOOT)
        enables = [s for s in steps if "ln -sf /etc/runit/sv/" in _argv_text(s)]
        self.assertEqual(len(enables), 2)
        for s in enables:
            self.assertEqual(s.argv[:4], ["artix-chroot", "/mnt", "sh", "-c"])
            self.assertTrue(s.argv[4].startswith("[ -e /etc/runit/sv/"), s.argv[4])
            self.assertTrue(s.argv[4].endswith("|| true"))
        self.assertEqual(CLONE_INIT, "runit")

    def test_files_written_under_target(self):
        steps = clone_steps(_plan(), _UEFI, boot=_BOOT)
        paths = [s.path for s in steps if isinstance(s, WriteTargetFile)]
        self.assertEqual(paths, ["/mnt/etc/glue/a.conf", "/mnt/etc/zramen.conf"])


class TestForbidden(unittest.TestCase):
    def test_no_network_or_package_install_tokens(self):
        for dp in (_UEFI, _BIOS):
            for s in clone_steps(_plan(), dp, boot=_BOOT):
                for arg in getattr(s, "argv", []):
                    for tok in ("basestrap", "grub", "curl", "wget"):
                        self.assertFalse(arg.startswith(tok), arg)
                    self.assertNotIn("pacman -S", arg)

    def test_plan_packages_never_installed(self):
        text = " ".join(_argv_text(s) for s in clone_steps(_plan(), _UEFI, boot=_BOOT))
        self.assertNotIn("steam", text)


class TestErrors(unittest.TestCase):
    def test_wrong_kernel(self):
        with self.assertRaises(CloneError):
            clone_steps(_plan(), _UEFI, boot=BootSpec(kernel="linux-cachyos"))
        self.assertEqual(CLONE_KERNEL, "linux")

    def test_root_target(self):
        for bad in ("/", ""):
            with self.assertRaises(CloneError):
                clone_steps(_plan(), _UEFI, boot=_BOOT, target=bad)

    def test_clone_error_is_value_error(self):
        self.assertTrue(issubclass(CloneError, ValueError))


class TestPurity(unittest.TestCase):
    def test_module_has_no_subprocess_or_os(self):
        src = (_PKG_ROOT / "glue_installer" / "clone.py").read_text()
        self.assertNotIn("import subprocess", src)
        self.assertNotIn("import os", src)
        self.assertLess(src.count("\n"), 500)


if __name__ == "__main__":
    unittest.main()
