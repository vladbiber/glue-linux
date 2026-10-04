"""Parity tests for packages/glue-boot/glue-boot-update (roadmap 3.3 + 3.5).

The POSIX script is run with `sh ... --root TMP --print` on a synthetic
root and its output is compared byte for byte with limine_conf().
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.limine import BootSpec, boot_conf, limine_conf

_SCRIPT = _PKG_ROOT.parent / "glue-boot" / "glue-boot-update"
_ROOT_UUID = "3f1c2a9e-7b4d-4c1e-9a6f-0d2e8b5c1a77"
_RESUME_UUID = "b7e0d2c4-5a6f-4e8b-8c1d-2f3a4b5c6d7e"
_PLAIN = BootSpec("linux-cachyos")
_HIB = BootSpec("linux-cachyos", ("amd_pstate=active",), True)


def _run(root, *args):
    return subprocess.run(["sh", str(_SCRIPT), "--root", str(root), *args],
                          capture_output=True, text=True)


class BootUpdateCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "boot").mkdir()
        (self.root / "etc" / "glue").mkdir(parents=True)
        self.kernel("linux-cachyos")

    def tearDown(self):
        self._tmp.cleanup()

    def kernel(self, name, initramfs=True, fallback=True):
        boot = self.root / "boot"
        (boot / f"vmlinuz-{name}").write_bytes(b"kernel")
        if initramfs:
            (boot / f"initramfs-{name}.img").write_bytes(b"initramfs")
        if fallback:
            (boot / f"initramfs-{name}-fallback.img").write_bytes(b"fallback")

    def conf(self, spec, firmware="uefi", resume=None):
        (self.root / "etc" / "glue" / "boot.conf").write_text(
            boot_conf(spec, firmware, _ROOT_UUID, resume))


class TestParity(BootUpdateCase):

    def test_script_exists_posix_shebang_and_syntax(self):
        self.assertTrue(_SCRIPT.is_file(), _SCRIPT)
        self.assertTrue(_SCRIPT.read_text().startswith("#!/bin/sh\n"))
        self.assertEqual(subprocess.run(["sh", "-n", str(_SCRIPT)]).returncode, 0)
        self.assertNotIn("artix", _SCRIPT.read_text().lower())

    def test_uefi_no_resume_matches_limine_conf(self):
        self.conf(_PLAIN)
        r = _run(self.root, "--print")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, limine_conf(_PLAIN, _ROOT_UUID))
        self.assertEqual(r.stderr, "")

    def test_bios_with_resume_matches_limine_conf(self):
        self.conf(_HIB, "bios", _RESUME_UUID)
        r = _run(self.root, "--print")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, limine_conf(_HIB, _ROOT_UUID, _RESUME_UUID, "bios"))
        self.assertIn(f"resume=UUID={_RESUME_UUID}", r.stdout)

    def test_second_kernel_matches_extra_kernels(self):
        self.kernel("linux-cachyos-bore")
        self.conf(_HIB, "uefi", _RESUME_UUID)
        r = _run(self.root, "--print")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, limine_conf(_HIB, _ROOT_UUID, _RESUME_UUID, "uefi",
                                               extra_kernels=("linux-cachyos-bore",)))

    def test_primary_stays_first_even_if_not_alphabetically_first(self):
        self.kernel("linux")  # sorts before linux-cachyos
        self.conf(_PLAIN)
        r = _run(self.root, "--print")
        self.assertEqual(r.stdout, limine_conf(_PLAIN, _ROOT_UUID, extra_kernels=("linux",)))

    def test_kernel_without_initramfs_is_skipped(self):
        self.kernel("linux-zen", initramfs=False, fallback=False)
        self.conf(_PLAIN)
        r = _run(self.root, "--print")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, limine_conf(_PLAIN, _ROOT_UUID))
        self.assertNotIn("linux-zen", r.stdout)
        self.assertIn("skipping linux-zen", r.stderr)

    def test_secondary_without_fallback_has_no_fallback_entry(self):
        self.kernel("linux-lts", fallback=False)
        self.conf(_PLAIN)
        r = _run(self.root, "--print")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("/Glue Linux (linux-lts)\n", r.stdout)
        self.assertNotIn("/Glue Linux (linux-lts, fallback initramfs)", r.stdout)
        self.assertNotIn("initramfs-linux-lts-fallback.img", r.stdout)
        self.assertEqual(r.stdout.count("/Glue Linux"), 3)

    def test_missing_primary_falls_back_to_first_with_warning(self):
        self.conf(BootSpec("linux-gone"))
        r = _run(self.root, "--print")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, limine_conf(_PLAIN, _ROOT_UUID))
        self.assertIn("linux-gone", r.stderr)


_WIN_EFI = ("# Windows Boot Manager on /dev/nvme0n1p1 (os-prober)\n/Windows Boot Manager\n"
            "protocol: efi\nimage_path: guid(deadbeef-1111-2222-3333-444444444444):"
            "/EFI/Microsoft/Boot/bootmgfw.efi\n")
_WIN_BIOS = ("# Windows 10 on /dev/sda1 (os-prober)\n/Windows 10\nprotocol: bios\n"
             "mbr_id: a1b2c3d4\npartition: 1\n")
_FEDORA = ("# Fedora on /dev/sdc1 (os-prober)\n/Fedora\nprotocol: efi\n"
           "image_path: guid(cafebabe-1111-2222-3333-444444444444):/EFI/fedora/shimx64.efi\n")


class TestForeignEntries(BootUpdateCase):
    """boot.d fragments (roadmap 3.4): shell and limine_conf() byte-identical."""

    def bootd(self, name, text):
        d = self.root / "etc" / "glue" / "boot.d"
        d.mkdir(exist_ok=True)
        (d / name).write_text(text)

    def test_uefi_windows(self):
        self.bootd("10-windows-boot-manager-deadbeef.conf", _WIN_EFI)
        self.conf(_PLAIN)
        r = _run(self.root, "--print")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, limine_conf(_PLAIN, _ROOT_UUID, foreign_entries=(_WIN_EFI,)))
        self.assertEqual(r.stderr, "")
        self.assertTrue(r.stdout.endswith("\n\n" + _WIN_EFI))

    def test_bios_windows_after_all_kernel_entries(self):
        self.kernel("linux-lts")
        self.bootd("30-windows-10-a1b2c3d4.conf", _WIN_BIOS)
        self.conf(_PLAIN, "bios")
        r = _run(self.root, "--print")
        self.assertEqual(r.stdout, limine_conf(_PLAIN, _ROOT_UUID, firmware="bios",
                                               extra_kernels=("linux-lts",),
                                               foreign_entries=(_WIN_BIOS,)))
        self.assertLess(r.stdout.index("/Glue Linux (linux-lts, fallback initramfs)"),
                        r.stdout.index("/Windows 10"))

    def test_empty_file_ignored(self):
        self.bootd("10-empty-00000000.conf", "")
        self.conf(_PLAIN)
        r = _run(self.root, "--print")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, limine_conf(_PLAIN, _ROOT_UUID))
        self.assertIn("skipping empty", r.stderr)

    def test_invalid_file_ignored_with_warning(self):
        self.bootd("20-bad-00000000.conf", "protocol: efi\nimage_path: boot():/x.efi\n")
        self.bootd("10-windows-boot-manager-deadbeef.conf", _WIN_EFI)
        self.conf(_PLAIN)
        r = _run(self.root, "--print")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, limine_conf(_PLAIN, _ROOT_UUID, foreign_entries=(_WIN_EFI,)))
        self.assertIn("20-bad-00000000.conf: first line", r.stderr)
        with self.assertRaises(ValueError):
            limine_conf(_PLAIN, _ROOT_UUID, foreign_entries=("protocol: efi\n",))

    def test_order_10_before_20_and_trailing_newlines_normalised(self):
        self.bootd("20-fedora-cafebabe.conf", _FEDORA + "\n\n")
        self.bootd("10-windows-boot-manager-deadbeef.conf", _WIN_EFI)
        self.conf(_HIB, "uefi", _RESUME_UUID)
        r = _run(self.root, "--print")
        self.assertEqual(r.stdout, limine_conf(_HIB, _ROOT_UUID, _RESUME_UUID,
                                               foreign_entries=(_WIN_EFI, _FEDORA)))
        self.assertLess(r.stdout.index("/Windows Boot Manager"), r.stdout.index("/Fedora"))
        self.assertNotIn("\n\n\n", r.stdout)

    def test_written_file_counts_foreign_entries(self):
        self.bootd("10-windows-boot-manager-deadbeef.conf", _WIN_EFI)
        self.conf(_PLAIN)
        r = _run(self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 other OS entries", r.stderr)
        self.assertEqual((self.root / "boot" / "limine.conf").read_text(),
                         limine_conf(_PLAIN, _ROOT_UUID, foreign_entries=(_WIN_EFI,)))


class TestFilesAndErrors(BootUpdateCase):

    def test_no_boot_conf_is_a_noop(self):
        r = _run(self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("nothing to do", r.stderr)
        self.assertFalse((self.root / "boot" / "limine.conf").exists())

    def test_writes_limine_conf_idempotently_without_leftover(self):
        self.conf(_HIB, "uefi", _RESUME_UUID)
        out = self.root / "boot" / "limine.conf"
        r1 = _run(self.root)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        first = out.read_bytes()
        self.assertEqual(first.decode(), limine_conf(_HIB, _ROOT_UUID, _RESUME_UUID))
        r2 = _run(self.root)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(out.read_bytes(), first)
        self.assertEqual(sorted(os.listdir(self.root / "boot")), [
            "initramfs-linux-cachyos-fallback.img", "initramfs-linux-cachyos.img",
            "limine.conf", "vmlinuz-linux-cachyos"])

    def test_print_does_not_touch_disk(self):
        self.conf(_PLAIN)
        _run(self.root, "--print")
        self.assertFalse((self.root / "boot" / "limine.conf").exists())

    def test_invalid_firmware_fails(self):
        conf = self.root / "etc" / "glue" / "boot.conf"
        conf.write_text(boot_conf(_PLAIN, "uefi", _ROOT_UUID).replace(
            "GLUE_BOOT_FIRMWARE=uefi", "GLUE_BOOT_FIRMWARE=efi"))
        r = _run(self.root, "--print")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("GLUE_BOOT_FIRMWARE", r.stderr)
        self.assertFalse((self.root / "boot" / "limine.conf").exists())

    def test_empty_root_uuid_and_no_kernels_fail(self):
        conf = self.root / "etc" / "glue" / "boot.conf"
        conf.write_text(boot_conf(_PLAIN, "uefi", _ROOT_UUID).replace(
            f"GLUE_BOOT_ROOT_UUID={_ROOT_UUID}", "GLUE_BOOT_ROOT_UUID="))
        self.assertNotEqual(_run(self.root, "--print").returncode, 0)
        self.conf(_PLAIN)
        for f in (self.root / "boot").iterdir():
            f.unlink()
        r = _run(self.root)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no kernel", r.stderr)

    def test_unknown_option_fails(self):
        self.conf(_PLAIN)
        self.assertNotEqual(_run(self.root, "--bogus").returncode, 0)


if __name__ == "__main__":
    unittest.main()
