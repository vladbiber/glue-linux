"""Secure Boot: the installer page, glue-boot-update's hashed config +
enrolled/signed Limine, and glue-secureboot's checks. limine and sbctl are
fakes on PATH that log their arguments (real ones need the firmware)."""
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from glue_installer.calamares_adapter import parse_selection
from glue_installer.calamares_config import (
    GS_PREFIX, SECUREBOOT_ON, build_pages, selection_from_globalstorage)
from glue_installer.catalog import load_catalog
from glue_installer.disks import DiskPlan, PartitionSpec
from glue_installer.limine import BootSpec, boot_conf, bootloader_packages, bootloader_steps

_PKG = Path(__file__).parent.parent
_BOOT_PKG = _PKG.parent / "glue-boot"
_UPDATE = _BOOT_PKG / "glue-boot-update"
_SECUREBOOT = _BOOT_PKG / "glue-secureboot"
_CATALOG = load_catalog(_PKG / "catalog" / "catalog.json")
_UUID = "3f1c2a9e-7b4d-4c1e-9a6f-0d2e8b5c1a77"
_GLOBAL = "8be4df61-93ca-11d2-aa0d-00e098032b8c"


def _b2(path):
    return hashlib.blake2b(Path(path).read_bytes()).hexdigest()


def _plan(firmware="uefi"):
    parts = [PartitionSpec(2, "/dev/sda2", "8300", "0", "ext4", "/")]
    if firmware == "uefi":
        parts.insert(0, PartitionSpec(1, "/dev/sda1", "ef00", "+1G", "vfat", "/boot"))
    return DiskPlan(device_path="/dev/sda", firmware=firmware, mode="erase",
                    partitions=parts)


class PageTest(unittest.TestCase):
    def test_page_is_off_by_default(self):
        page = {p.id: p for p in build_pages(_CATALOG)}["gluesecureboot"]
        self.assertEqual(page.default, "no-secureboot")
        self.assertEqual([i.id for i in page.items], ["no-secureboot", "secureboot"])

    def test_choice_reaches_the_selection(self):
        sel = selection_from_globalstorage({GS_PREFIX + "gluesecureboot": SECUREBOOT_ON},
                                           _CATALOG)
        self.assertIs(sel["secure_boot"], True)
        self.assertTrue(parse_selection(sel, _CATALOG).secure_boot)
        self.assertFalse(parse_selection({}, _CATALOG).secure_boot)


class InstallStepTest(unittest.TestCase):
    def test_sbctl_only_with_secure_boot_on_uefi(self):
        self.assertNotIn("sbctl", bootloader_packages(_plan()))
        self.assertIn("sbctl", bootloader_packages(_plan(), secure_boot=True))
        self.assertNotIn("sbctl", bootloader_packages(_plan("bios"), secure_boot=True))

    def test_writer_falls_back_to_the_normal_menu(self):
        script = bootloader_steps(_plan(), BootSpec("linux-cachyos", secure_boot=True))[0].argv[-1]
        self.assertIn("glue-secureboot enable --no-update && glue-boot-update --deploy", script)
        self.assertIn("GLUE_BOOT_SECUREBOOT=0", script)
        plain = bootloader_steps(_plan(), BootSpec("linux-cachyos"))[0].argv[-1]
        self.assertNotIn("glue-secureboot", plain)
        self.assertTrue(plain.rstrip().endswith("glue-boot-update --deploy"))


class BootUpdateSecureBootTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        r = Path(self.tmp.name)
        self.root = r / "root"
        boot = self.root / "boot"
        (boot / "EFI" / "limine").mkdir(parents=True)
        (boot / "vmlinuz-linux-cachyos").write_bytes(b"kernel")
        (boot / "initramfs-linux-cachyos.img").write_bytes(b"initramfs")
        (boot / "initramfs-linux-cachyos-fallback.img").write_bytes(b"fallback")
        (boot / "EFI" / "limine" / "BOOTX64.EFI").write_bytes(b"old signed limine")
        share = self.root / "usr/share/limine"
        share.mkdir(parents=True)
        (share / "BOOTX64.EFI").write_bytes(b"fresh limine")
        wall = self.root / "usr/share/backgrounds/glue"
        wall.mkdir(parents=True)
        (wall / "wallpaper.png").write_bytes(b"png")
        (self.root / "etc/glue").mkdir(parents=True)
        (self.root / "etc/glue/boot.conf").write_text(
            boot_conf(BootSpec("linux-cachyos"), "uefi", _UUID) + "GLUE_BOOT_SECUREBOOT=1\n")
        self.bin = r / "bin"
        self.bin.mkdir()
        self.log = r / "calls.log"
        self.fake("limine", 'printf "%s|enrolled\\n" "$4" >> "$3"')
        self.fake("sbctl", 'printf "signed\\n" >> "$2"')

    def fake(self, name, body, code=0):
        # limine enroll-config --quiet FILE HASH / sbctl sign FILE
        (self.bin / name).write_text(
            f'#!/bin/sh\necho "{name} $*" >> "$CALLS"\n{body}\nexit {code}\n')
        (self.bin / name).chmod(0o755)

    def run_update(self, *args):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", CALLS=str(self.log))
        return subprocess.run(["sh", str(_UPDATE), "--root", str(self.root), *args],
                              env=env, capture_output=True, text=True)

    def test_every_loaded_file_is_hashed_and_the_loader_enrolled(self):
        r = self.run_update()
        self.assertEqual(r.returncode, 0, r.stderr)
        boot = self.root / "boot"
        conf = (boot / "limine.conf").read_text()
        self.assertIn(f"kernel_path: boot():/vmlinuz-linux-cachyos#{_b2(boot / 'vmlinuz-linux-cachyos')}", conf)
        self.assertIn(f"module_path: boot():/initramfs-linux-cachyos.img#{_b2(boot / 'initramfs-linux-cachyos.img')}", conf)
        self.assertIn(f"wallpaper: boot():/glue-wallpaper.png#{_b2(boot / 'glue-wallpaper.png')}", conf)
        loader = (boot / "EFI/limine/BOOTX64.EFI").read_text()
        # fresh copy, enrolled with the hash of the config that was written
        self.assertTrue(loader.startswith("fresh limine"))
        self.assertIn(f"{_b2(boot / 'limine.conf')}|enrolled", loader)
        self.assertTrue(loader.endswith("signed\n"))
        self.assertEqual((boot / "EFI/BOOT/BOOTX64.EFI").read_text(), loader)
        self.assertFalse((boot / "EFI/limine/BOOTX64.EFI.glue-new").exists())

    def test_deploy_keeps_the_signed_loader(self):
        r = self.run_update("--deploy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.root / "boot/EFI/limine/BOOTX64.EFI").read_text().endswith("signed\n"))

    def test_failed_signing_leaves_the_old_pair(self):
        self.run_update()
        boot = self.root / "boot"
        old_conf = (boot / "limine.conf").read_text()
        old_loader = (boot / "EFI/limine/BOOTX64.EFI").read_text()
        (boot / "vmlinuz-linux-cachyos").write_bytes(b"new kernel")
        self.fake("sbctl", "", code=1)
        r = self.run_update()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("left untouched", r.stderr)
        self.assertEqual((boot / "limine.conf").read_text(), old_conf)
        self.assertEqual((boot / "EFI/limine/BOOTX64.EFI").read_text(), old_loader)
        self.assertFalse((boot / "limine.conf.new").exists())

    def test_off_means_no_hashes(self):
        conf = self.root / "etc/glue/boot.conf"
        conf.write_text(conf.read_text().replace("SECUREBOOT=1", "SECUREBOOT=0"))
        r = self.run_update("--print")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("#", "".join(l for l in r.stdout.splitlines()[1:]))
        self.assertFalse(self.log.exists())


class GlueSecureBootScriptTest(unittest.TestCase):
    def efivars(self, setup, secure):
        d = Path(self.tmp.name) / "efivars"
        d.mkdir(exist_ok=True)
        (d / f"SetupMode-{_GLOBAL}").write_bytes(bytes([6, 0, 0, 0, setup]))
        (d / f"SecureBoot-{_GLOBAL}").write_bytes(bytes([6, 0, 0, 0, secure]))
        return d

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_sb(self, *args, setup=0, secure=0):
        env = dict(os.environ, GLUE_EFIVARS=str(self.efivars(setup, secure)),
                   GLUE_BOOT_CONF=str(Path(self.tmp.name) / "boot.conf"))
        return subprocess.run(["sh", str(_SECUREBOOT), *args], env=env,
                              capture_output=True, text=True)

    def test_syntax(self):
        self.assertEqual(subprocess.run(["sh", "-n", str(_SECUREBOOT)]).returncode, 0)

    def test_status_reads_the_firmware_variables(self):
        r = self.run_sb("status", setup=1, secure=0)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Secure Boot in firmware: off", r.stdout)
        self.assertIn("Setup Mode (keys can be enrolled): yes", r.stdout)
        r = self.run_sb("status", setup=0, secure=1)
        self.assertIn("Secure Boot in firmware: on", r.stdout)
        self.assertIn("Setup Mode (keys can be enrolled): no", r.stdout)

    def test_enroll_uses_microsoft_keys_and_disable_guards_firmware(self):
        text = _SECUREBOOT.read_text()
        self.assertIn("sbctl enroll-keys --microsoft", text)
        self.assertIn("Secure Boot is still on in the firmware", text)

    def test_unknown_command(self):
        if not os.path.isdir("/sys/firmware/efi"):
            self.skipTest("needs a UEFI-booted test machine")
        r = self.run_sb("frobnicate")
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
