"""Unit tests for limine.py (roadmap 3.1 + 3.2): limine.conf text + steps.

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
    BASE_CMDLINE, BootSpec, _CONFIG_KEYS, bootloader_packages,
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

    def test_amber_palette_and_no_artix(self):
        text = limine_conf(_HIB, _ROOT, _RESUME)
        self.assertNotIn("artix", text.lower())
        self.assertIn("interface_branding_colour: F1B00A", text)
        self.assertIn("backdrop: 100A02", text)
        self.assertIn("term_background: 00100A02", text)
        self.assertIn("term_foreground: F1B00A", text)
        self.assertIn("interface_help_colour: A66900", text)

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
                         ["efibootmgr", "limine"])
        self.assertEqual(bootloader_packages(plan_disk(_device(), "bios")), ["limine"])

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


class TestBootloaderStepsUefi(unittest.TestCase):

    def setUp(self):
        self.steps = bootloader_steps(plan_disk(_device(), "uefi"), _PLAIN)
        self.argvs = [s.argv for s in self.steps]

    def test_both_bootx64_destinations(self):
        self.assertIn(["mkdir", "-p", "/mnt/boot/EFI/BOOT", "/mnt/boot/EFI/limine"],
                      self.argvs)
        self.assertIn(["cp", "/mnt/usr/share/limine/BOOTX64.EFI",
                       "/mnt/boot/EFI/BOOT/BOOTX64.EFI"], self.argvs)
        self.assertIn(["cp", "/mnt/usr/share/limine/BOOTX64.EFI",
                       "/mnt/boot/EFI/limine/BOOTX64.EFI"], self.argvs)

    def test_efibootmgr_live_side_with_esp_number(self):
        self.assertIn(["efibootmgr", "--create", "--disk", "/dev/sda", "--part", "1",
                       "--loader", "\\EFI\\limine\\BOOTX64.EFI",
                       "--label", "Glue Linux", "--unicode"], self.argvs)

    def test_writer_is_last_chrooted_no_resume(self):
        last = self.steps[-1]
        self.assertEqual(last.argv[:4], ["artix-chroot", "/mnt", "sh", "-c"])
        script = last.argv[4]
        self.assertIn("findmnt -no UUID /", script)
        self.assertIn("/boot/limine.conf", script)
        self.assertIn("<<'GLUE_LIMINE_CONF'", script)
        self.assertIn("root=UUID=@ROOT_UUID@", script)
        self.assertIn('sed -i "s|@ROOT_UUID@|$ROOT_UUID|g" /boot/limine.conf', script)
        self.assertNotIn("resume", script)
        self.assertNotIn("mkinitcpio", script)
        self.assertNotIn("blkid", script)

    def test_no_grub_and_all_run_commands(self):
        flat = " ".join(a for argv in self.argvs for a in argv)
        self.assertNotIn("grub", flat)
        self.assertNotIn("/boot/" + "efi", flat)
        self.assertTrue(all(isinstance(s, RunCommand) for s in self.steps))

    def test_custom_target(self):
        argvs = [s.argv for s in bootloader_steps(plan_disk(_device(), "uefi"),
                                                   _PLAIN, target="/target/")]
        self.assertIn(["cp", "/target/usr/share/limine/BOOTX64.EFI",
                       "/target/boot/EFI/limine/BOOTX64.EFI"], argvs)
        self.assertEqual(argvs[-1][1], "/target")

    def test_resume_writer_blkid_hook_and_mkinitcpio(self):
        steps = bootloader_steps(_swap_disk_plan(), _HIB)
        script = steps[-1].argv[4]
        self.assertIn("RESUME_UUID=$(blkid -s UUID -o value /dev/sda2)", script)
        self.assertIn("resume=UUID=@RESUME_UUID@", script)
        self.assertIn('sed -i "s|@RESUME_UUID@|$RESUME_UUID|g" /boot/limine.conf', script)
        self.assertIn("/etc/mkinitcpio.conf", script)
        self.assertIn("filesystems", script)
        self.assertIn("mkinitcpio -P", script)
        self.assertIn("--part", steps[3].argv)
        self.assertEqual(steps[3].argv[steps[3].argv.index("--part") + 1], "1")

    def test_resume_requested_without_swap_partition_is_dropped(self):
        script = bootloader_steps(plan_disk(_device(), "uefi"), _HIB)[-1].argv[4]
        self.assertNotIn("resume", script)
        self.assertNotIn("mkinitcpio", script)

    def test_heredoc_template_survives_shell(self):
        import subprocess
        import tempfile
        script = bootloader_steps(_swap_disk_plan(), _HIB)[-1].argv[4]
        body = script.split("<<'GLUE_LIMINE_CONF'\n")[1].split("\nGLUE_LIMINE_CONF\n")[0]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "limine.conf"
            sh = f"cat > {out} <<'GLUE_LIMINE_CONF'\n{body}\nGLUE_LIMINE_CONF\n"
            subprocess.run(["sh", "-c", sh], check=True)
            self.assertEqual(out.read_text(),
                             limine_conf(_HIB, "@ROOT_UUID@", "@RESUME_UUID@"))

    def test_uefi_plan_without_esp_raises(self):
        broken = dataclasses.replace(plan_disk(_device(), "uefi"),
                                     partitions=plan_disk(_device(), "uefi").partitions[1:])
        with self.assertRaises(DiskError):
            bootloader_steps(broken, _PLAIN)


class TestBootloaderStepsBios(unittest.TestCase):

    def setUp(self):
        self.steps = bootloader_steps(plan_disk(_device("/dev/nvme0n1"), "bios"), _PLAIN)
        self.argvs = [s.argv for s in self.steps]

    def test_bios_install_and_stage_file(self):
        self.assertIn(["cp", "/mnt/usr/share/limine/limine-bios.sys", "/mnt/boot/"],
                      self.argvs)
        self.assertIn(["limine", "bios-install", "/dev/nvme0n1"], self.argvs)
        self.assertLess(self.argvs.index(["cp", "/mnt/usr/share/limine/limine-bios.sys",
                                          "/mnt/boot/"]),
                        self.argvs.index(["limine", "bios-install", "/dev/nvme0n1"]))

    def test_no_efi_artifacts(self):
        flat = " ".join(a for argv in self.argvs for a in argv)
        self.assertNotIn("efibootmgr", flat)
        self.assertNotIn("BOOTX64", flat)
        self.assertNotIn("grub", flat)
        self.assertEqual(self.steps[-1].argv[:3], ["artix-chroot", "/mnt", "sh"])
        self.assertIn("/boot/limine.conf", self.steps[-1].argv[4])


if __name__ == "__main__":
    unittest.main()
