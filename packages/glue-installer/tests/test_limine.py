"""Unit tests for limine.py: limine.conf, boot.conf, steps.

Option names are checked against the set taken from
https://raw.githubusercontent.com/limine-bootloader/limine/v12.9.0/CONFIG.md
"""

import dataclasses
import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.disk_swap import plan_disk_with_swap
from glue_installer.disks import BlockDevice, DiskError, plan_disk
from glue_installer.executor import RunCommand
from glue_installer.limine import (
    BASE_CMDLINE, BootSpec, _CONFIG_KEYS, boot_conf, bootloader_packages,
    bootloader_steps, emitted_keys, kernel_cmdline, kernel_name, limine_conf,
    resume_wanted,
)
from glue_installer.swap import swap_plan

_GIB = 1024 ** 3
_ROOT = "aaaa-1111"
_RESUME = "bbbb-2222"
_HIB = BootSpec("linux-cachyos", ("amd_pstate=active",), True)
_PLAIN = BootSpec("linux-cachyos")
_CMDLINE = ("root=UUID=aaaa-1111 rw quiet loglevel=3 rd.udev.log_level=3 "
            "nowatchdog zswap.enabled=0 amd_pstate=active")


def _device(path="/dev/sda", size=64 * _GIB):
    return BlockDevice(name=path.rsplit("/", 1)[-1], path=path, size_bytes=size,
                       model="Test", is_removable=False, has_mounted_partitions=False)


def _swap_disk_plan():
    return plan_disk_with_swap(_device(size=500 * _GIB), "uefi",
                               swap_plan(16 * _GIB, 500 * _GIB, hibernate=True),
                               swap_uuid="u-1")


_FRAG = "# Windows Boot Manager on /dev/sda1 (os-prober)\n/Windows Boot Manager\nprotocol: efi\n" \
        "image_path: guid(deadbeef-1111-2222-3333-444444444444):/EFI/Microsoft/Boot/bootmgfw.efi\n"


class TestForeignEntries(unittest.TestCase):

    def test_fragments_after_kernels_blank_line_between(self):
        base = limine_conf(_HIB, _ROOT, _RESUME, extra_kernels=("linux",))
        text = limine_conf(_HIB, _ROOT, _RESUME, extra_kernels=("linux",),
                           foreign_entries=(_FRAG, _FRAG.replace("Windows", "W2")))
        self.assertTrue(text.startswith(base))
        self.assertEqual(text, base + "\n" + _FRAG + "\n" + _FRAG.replace("Windows", "W2"))
        self.assertTrue(set(emitted_keys(text)) <= _CONFIG_KEYS)

    def test_chainload_keys_are_in_config_md_set(self):
        self.assertTrue({"image_path", "partition", "mbr_id", "gpt_uuid", "comment"} <= _CONFIG_KEYS)

    def test_bad_fragments_raise(self):
        for bad in ("", "\n", "protocol: efi\n", "/X\nprotocol: efi\nwallpaper_x: a\n", "/X\ndrive: 1\n"):
            with self.assertRaises(ValueError, msg=bad):
                limine_conf(_PLAIN, _ROOT, foreign_entries=(bad,))


class TestLimineConf(unittest.TestCase):

    def test_uefi_hibernate_full_text(self):
        text = limine_conf(_HIB, _ROOT, _RESUME)
        lines = text.splitlines()
        for line in ("timeout: 5", "interface_branding: Glue Linux", "/Glue Linux",
                     "/Glue Linux (fallback initramfs)", "protocol: linux",
                     "kernel_path: boot():/vmlinuz-linux-cachyos",
                     "module_path: boot():/initramfs-linux-cachyos.img",
                     "module_path: boot():/initramfs-linux-cachyos-fallback.img",
                     f"cmdline: {_CMDLINE} resume=UUID={_RESUME}"):
            self.assertIn(line, lines)
        self.assertEqual(lines.count("protocol: linux"), 2)
        self.assertEqual(lines.count(f"cmdline: {_CMDLINE} resume=UUID={_RESUME}"), 2)
        self.assertTrue(text.endswith("\n"))

    def test_entry_order_glue_then_fallback(self):
        text = limine_conf(_HIB, _ROOT, _RESUME)
        self.assertLess(text.index("/Glue Linux\n"),
                        text.index("/Glue Linux (fallback initramfs)\n"))
        glue, fallback = text.split("/Glue Linux (fallback initramfs)")
        self.assertIn("initramfs-linux-cachyos.img", glue)
        self.assertNotIn("fallback", glue.split("/Glue Linux")[1])
        self.assertIn("initramfs-linux-cachyos-fallback.img", fallback)

    def test_no_resume_without_flag_or_uuid(self):
        self.assertNotIn("resume=", limine_conf(_PLAIN, _ROOT, _RESUME))
        self.assertNotIn("resume=", limine_conf(_HIB, _ROOT, None))
        self.assertNotIn("resume=", limine_conf(_HIB, _ROOT, ""))
        self.assertIn(f"cmdline: {_CMDLINE}\n", limine_conf(_HIB, _ROOT, None))

    def test_cmdline_order_root_base_extras_resume(self):
        spec = BootSpec("linux", ("zzz=1", "aaa=2", "aaa=2"), True)
        self.assertEqual(
            kernel_cmdline(spec, _ROOT, _RESUME),
            "root=UUID=aaaa-1111 " + " ".join(BASE_CMDLINE)
            + " aaa=2 zzz=1 resume=UUID=bbbb-2222")
        self.assertEqual(BASE_CMDLINE[:3], ("rw", "quiet", "loglevel=3"))
        self.assertIn("nowatchdog", BASE_CMDLINE)
        self.assertIn("zswap.enabled=0", BASE_CMDLINE)

    def test_kernel_linux_paths(self):
        text = limine_conf(BootSpec("linux"), _ROOT)
        self.assertIn("kernel_path: boot():/vmlinuz-linux\n", text)
        self.assertIn("module_path: boot():/initramfs-linux.img\n", text)
        self.assertIn("module_path: boot():/initramfs-linux-fallback.img\n", text)
        self.assertNotIn("cachyos", text)

    def test_bios_same_entries_as_uefi(self):
        self.assertEqual(limine_conf(_HIB, _ROOT, _RESUME, firmware="bios"),
                         limine_conf(_HIB, _ROOT, _RESUME, firmware="uefi"))
        with self.assertRaises(ValueError):
            limine_conf(_HIB, _ROOT, _RESUME, firmware="efi")

    def test_every_key_is_in_config_md_set(self):
        for text in (limine_conf(_HIB, _ROOT, _RESUME), limine_conf(_PLAIN, _ROOT),
                     limine_conf(BootSpec("linux"), _ROOT, firmware="bios")):
            keys = emitted_keys(text)
            self.assertTrue(keys)
            self.assertTrue(set(keys) <= _CONFIG_KEYS, set(keys) - _CONFIG_KEYS)

    def test_neutral_palette_wallpaper_and_no_artix(self):
        text = limine_conf(_HIB, _ROOT, _RESUME)
        self.assertNotIn("artix", text.lower())
        self.assertIn("interface_branding_colour: 2B2F33", text)
        self.assertIn("backdrop: E9E9E7", text)
        self.assertIn("wallpaper: boot():/glue-wallpaper.png", text)
        self.assertIn("wallpaper_style: stretched", text)
        self.assertIn("term_background: 30E9E9E7", text)
        self.assertIn("term_foreground: 2B2F33", text)
        self.assertIn("interface_help_colour: 3A3F44", text)
        for amber in ("F1B00A", "A66900", "100A02"):
            self.assertNotIn(amber, text)

    def test_comments_only_on_own_lines(self):
        for line in limine_conf(_HIB, _ROOT, _RESUME).splitlines():
            if "#" in line:
                self.assertTrue(line.startswith("#"), line)

    def test_invalid_inputs_raise(self):
        with self.assertRaises(ValueError):
            limine_conf(BootSpec(""), _ROOT)
        with self.assertRaises(ValueError):
            limine_conf(BootSpec("linux cachyos"), _ROOT)
        with self.assertRaises(ValueError):
            limine_conf(BootSpec("/boot/vmlinuz"), _ROOT)
        with self.assertRaises(ValueError):
            limine_conf(BootSpec("linux", ("a b",)), _ROOT)
        with self.assertRaises(ValueError):
            limine_conf(_PLAIN, "")
        with self.assertRaises(ValueError):
            limine_conf(_HIB, _ROOT, "x y")


class TestPackagesAndHelpers(unittest.TestCase):

    def test_packages_per_firmware_no_grub(self):
        self.assertEqual(bootloader_packages(plan_disk(_device(), "uefi")),
                         ["efibootmgr", "glue-boot", "limine"])
        self.assertEqual(bootloader_packages(plan_disk(_device(), "bios")),
                         ["glue-boot", "limine"])

    def test_kernel_name_skips_headers(self):
        self.assertEqual(kernel_name(["linux-cachyos", "linux-cachyos-headers"]),
                         "linux-cachyos")
        self.assertEqual(kernel_name(["linux-zen-headers", "linux-zen"]), "linux-zen")
        with self.assertRaises(ValueError):
            kernel_name(["base", "linux-firmware-headers"])

    def test_resume_wanted_only_for_new_swap_partition(self):
        self.assertTrue(resume_wanted(_swap_disk_plan(), True))
        self.assertFalse(resume_wanted(_swap_disk_plan(), False))
        self.assertFalse(resume_wanted(plan_disk(_device(), "uefi"), True))
        self.assertFalse(resume_wanted(None, True))
        existing = dataclasses.replace(_swap_disk_plan(), mode="existing")
        self.assertFalse(resume_wanted(existing, True))
        swapfile = dataclasses.replace(plan_disk(_device(), "uefi"), swapfile_mib=4096)
        self.assertFalse(resume_wanted(swapfile, True))


class TestExtraKernelsAndBootConf(unittest.TestCase):

    def test_no_extra_kernels_is_byte_identical(self):
        self.assertEqual(limine_conf(_HIB, _ROOT, _RESUME, "uefi", ()),
                         limine_conf(_HIB, _ROOT, _RESUME))

    def test_extra_kernel_adds_two_entries_after_primary_pair(self):
        text = limine_conf(_PLAIN, _ROOT, extra_kernels=("linux-cachyos-bore",))
        titles = [l for l in text.splitlines() if l.startswith("/")]
        self.assertEqual(titles, [
            "/Glue Linux", "/Glue Linux (fallback initramfs)",
            "/Glue Linux (linux-cachyos-bore)",
            "/Glue Linux (linux-cachyos-bore, fallback initramfs)"])
        self.assertIn("kernel_path: boot():/vmlinuz-linux-cachyos-bore\n", text)
        self.assertIn("module_path: boot():/initramfs-linux-cachyos-bore-fallback.img\n", text)
        self.assertEqual(text.count("cmdline: root=UUID=aaaa-1111 "), 4)
        self.assertTrue(text.startswith(limine_conf(_PLAIN, _ROOT).rstrip("\n") + "\n\n/"))
        self.assertTrue(set(emitted_keys(text)) <= _CONFIG_KEYS)
        with self.assertRaises(ValueError):
            limine_conf(_PLAIN, _ROOT, extra_kernels=("linux-cachyos",))
        with self.assertRaises(ValueError):
            limine_conf(_PLAIN, _ROOT, extra_kernels=("a b",))

    def test_boot_conf_uefi_resume_exact(self):
        self.assertEqual(boot_conf(_HIB, "uefi", _ROOT, _RESUME), (
            "# Glue Linux boot settings; glue-boot-update turns these into /boot/limine.conf\n"
            "GLUE_BOOT_FIRMWARE=uefi\n"
            "GLUE_BOOT_KERNEL=linux-cachyos\n"
            "GLUE_BOOT_ROOT_UUID=aaaa-1111\n"
            "GLUE_BOOT_RESUME_UUID=bbbb-2222\n"
            'GLUE_BOOT_CMDLINE="rw quiet loglevel=3 rd.udev.log_level=3 nowatchdog '
            'zswap.enabled=0 amd_pstate=active"\n'
            "GLUE_BOOT_TIMEOUT=5\n"))

    def test_boot_conf_bios_no_resume_exact(self):
        text = boot_conf(_PLAIN, "bios", _ROOT, _RESUME)  # resume flag off: dropped
        self.assertEqual(text, (
            "# Glue Linux boot settings; glue-boot-update turns these into /boot/limine.conf\n"
            "GLUE_BOOT_FIRMWARE=bios\n"
            "GLUE_BOOT_KERNEL=linux-cachyos\n"
            "GLUE_BOOT_ROOT_UUID=aaaa-1111\n"
            "GLUE_BOOT_RESUME_UUID=\n"
            'GLUE_BOOT_CMDLINE="rw quiet loglevel=3 rd.udev.log_level=3 nowatchdog '
            'zswap.enabled=0"\n'
            "GLUE_BOOT_TIMEOUT=5\n"))
        self.assertEqual(boot_conf(_HIB, "bios", _ROOT, None).count("RESUME_UUID=\n"), 1)
        self.assertNotIn("root=", text)
        with self.assertRaises(ValueError):
            boot_conf(_PLAIN, "efi", _ROOT)
        with self.assertRaises(ValueError):
            boot_conf(BootSpec("linux", ('a="b"',)), "uefi", _ROOT)


class TestBootloaderStepsUefi(unittest.TestCase):

    def setUp(self):
        self.steps = bootloader_steps(plan_disk(_device(), "uefi"), _PLAIN)
        self.argvs = [s.argv for s in self.steps]

    def test_single_chrooted_step_no_live_side_tools(self):
        self.assertEqual(len(self.steps), 1)
        self.assertTrue(all(isinstance(s, RunCommand) for s in self.steps))
        for argv in self.argvs:
            self.assertNotIn(argv[0], ("cp", "efibootmgr", "limine", "mkdir"))
        flat = " ".join(a for argv in self.argvs for a in argv)
        self.assertNotIn("grub", flat)
        self.assertNotIn("/boot/" + "efi", flat)
        self.assertNotIn("BOOTX64", flat)
        self.assertIn("limine.conf", self.steps[-1].description)

    def test_writer_script_no_resume(self):
        last = self.steps[-1]
        self.assertEqual(last.argv[:4], ["artix-chroot", "/mnt", "sh", "-c"])
        script = last.argv[4]
        self.assertTrue(script.startswith("set -e\n"))
        self.assertIn("findmnt -no UUID /", script)
        self.assertIn("mkdir -p /etc/glue", script)
        self.assertIn("cat > /etc/glue/boot.conf <<'GLUE_BOOT_CONF'", script)
        self.assertIn("GLUE_BOOT_FIRMWARE=uefi\n", script)
        self.assertIn("GLUE_BOOT_KERNEL=linux-cachyos\n", script)
        self.assertIn("GLUE_BOOT_ROOT_UUID=@ROOT_UUID@\n", script)
        self.assertIn('sed -i "s|@ROOT_UUID@|$ROOT_UUID|g" /etc/glue/boot.conf', script)
        self.assertTrue(script.endswith("\nglue-boot-update --deploy\n"))
        self.assertNotIn("resume", script)
        self.assertNotIn("mkinitcpio", script)
        self.assertNotIn("blkid", script)

    def test_custom_target(self):
        steps = bootloader_steps(plan_disk(_device(), "uefi"), _PLAIN, target="/target/")
        self.assertEqual(steps[-1].argv[:2], ["artix-chroot", "/target"])

    def test_resume_writer_blkid_hook_and_mkinitcpio(self):
        steps = bootloader_steps(_swap_disk_plan(), _HIB)
        self.assertEqual(len(steps), 1)
        script = steps[-1].argv[4]
        self.assertIn("RESUME_UUID=$(blkid -s UUID -o value /dev/sda2)", script)
        self.assertIn("GLUE_BOOT_RESUME_UUID=@RESUME_UUID@\n", script)
        self.assertIn('sed -i "s|@RESUME_UUID@|$RESUME_UUID|g" /etc/glue/boot.conf', script)
        self.assertIn("/etc/mkinitcpio.conf", script)
        self.assertIn("filesystems)([ )])/\\1 resume\\2/", script)
        self.assertIn("mkinitcpio -P", script)
        self.assertLess(script.index("mkinitcpio -P"), script.index("glue-boot-update --deploy"))
        self.assertIn("hibernation resume", steps[-1].description)

    def test_resume_requested_without_swap_partition_is_dropped(self):
        script = bootloader_steps(plan_disk(_device(), "uefi"), _HIB)[-1].argv[4]
        self.assertNotIn("resume", script)
        self.assertNotIn("mkinitcpio", script)

    def test_heredoc_template_survives_shell(self):
        import subprocess
        import tempfile
        script = bootloader_steps(_swap_disk_plan(), _HIB)[-1].argv[4]
        body = script.split("<<'GLUE_BOOT_CONF'\n")[1].split("\nGLUE_BOOT_CONF\n")[0]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "boot.conf"
            sh = f"cat > {out} <<'GLUE_BOOT_CONF'\n{body}\nGLUE_BOOT_CONF\n"
            subprocess.run(["sh", "-c", sh], check=True)
            self.assertEqual(out.read_text(),
                             boot_conf(_HIB, "uefi", "@ROOT_UUID@", "@RESUME_UUID@"))

    def test_uefi_plan_without_esp_raises(self):
        broken = dataclasses.replace(plan_disk(_device(), "uefi"),
                                     partitions=plan_disk(_device(), "uefi").partitions[1:])
        with self.assertRaises(DiskError):
            bootloader_steps(broken, _PLAIN)


class TestBootloaderStepsBios(unittest.TestCase):

    def setUp(self):
        self.steps = bootloader_steps(plan_disk(_device("/dev/nvme0n1"), "bios"), _PLAIN)
        self.argvs = [s.argv for s in self.steps]

    def test_single_chrooted_step_with_bios_firmware(self):
        self.assertEqual(len(self.steps), 1)
        for argv in self.argvs:
            self.assertNotIn(argv[0], ("cp", "efibootmgr", "limine", "mkdir"))
        self.assertEqual(self.steps[-1].argv[:4], ["artix-chroot", "/mnt", "sh", "-c"])
        script = self.steps[-1].argv[4]
        self.assertIn("GLUE_BOOT_FIRMWARE=bios\n", script)
        self.assertIn("<<'GLUE_BOOT_CONF'", script)
        self.assertTrue(script.rstrip("\n").endswith("glue-boot-update --deploy"))

    def test_no_efi_artifacts(self):
        flat = " ".join(a for argv in self.argvs for a in argv)
        self.assertNotIn("efibootmgr", flat)
        self.assertNotIn("BOOTX64", flat)
        self.assertNotIn("grub", flat)
        self.assertIn("limine.conf", self.steps[-1].description)


if __name__ == "__main__":
    unittest.main()
