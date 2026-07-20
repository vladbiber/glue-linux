"""Unit tests for compile_steps' disk_plan integration (disk prep + grub)."""

import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from wheatley_installer.disks import BlockDevice, plan_disk
from wheatley_installer.executor import (
    RunCommand, WriteTargetFile, compile_steps, execute,
)
from wheatley_installer.plan import InstallPlan, PlannedFile

_GIB = 1024 ** 3


def _plan() -> InstallPlan:
    return InstallPlan(
        packages=["base", "linux-cachyos"],
        services=["greetd"],
        files=[PlannedFile(path="/etc/skel/.bashrc", content="# rc\n", mode=0o644)],
        warnings=[],
    )


def _disk_plan(firmware="uefi", path="/dev/sda"):
    device = BlockDevice(
        name=path.rsplit("/", 1)[-1], path=path, size_bytes=64 * _GIB,
        model="Test Disk", is_removable=False, has_mounted_partitions=False,
    )
    return plan_disk(device, firmware)


def _argvs(steps):
    return [s.argv for s in steps if isinstance(s, RunCommand)]


def _index_of(steps, first_arg):
    """Index of the first RunCommand whose argv starts with first_arg."""
    for i, step in enumerate(steps):
        if isinstance(step, RunCommand) and step.argv[0] == first_arg:
            return i
    raise AssertionError(f"no RunCommand starting with {first_arg!r}")


class TestNoDiskPlanRegression(unittest.TestCase):
    """compile_steps without disk_plan must behave exactly as before."""

    def test_omitted_and_none_are_identical(self):
        self.assertEqual(
            compile_steps(_plan(), init_id="dinit"),
            compile_steps(_plan(), init_id="dinit", disk_plan=None),
        )

    def test_exact_step_list_unchanged(self):
        steps = compile_steps(_plan(), target="/mnt", init_id="dinit")
        expected = [
            RunCommand(
                argv=["basestrap", "/mnt", "base", "linux-cachyos"],
                description="Install 2 packages with basestrap",
            ),
            RunCommand(
                argv=["sh", "-c", "fstabgen -U /mnt >> /mnt/etc/fstab"],
                description="Generate fstab → /mnt/etc/fstab",
            ),
            WriteTargetFile(
                path="/mnt/etc/skel/.bashrc", content="# rc\n", mode=0o644,
                description="Write /etc/skel/.bashrc",
            ),
            RunCommand(
                argv=["artix-chroot", "/mnt", "ln", "-sf",
                      "/etc/dinit.d/greetd", "/etc/dinit.d/boot.d/greetd"],
                description="Enable service greetd (dinit)",
            ),
        ]
        self.assertEqual(steps, expected)

    def test_no_grub_or_sgdisk_without_disk_plan(self):
        argvs = _argvs(compile_steps(_plan(), init_id="runit"))
        flat = [arg for argv in argvs for arg in argv]
        for tool in ("sgdisk", "mkfs.ext4", "mkfs.fat", "grub-install", "mount"):
            self.assertNotIn(tool, flat)


class TestUefiDiskPlan(unittest.TestCase):
    def setUp(self):
        self.steps = compile_steps(
            _plan(), init_id="dinit", disk_plan=_disk_plan("uefi"),
        )

    def test_zap_is_first_step(self):
        self.assertEqual(self.steps[0].argv, ["sgdisk", "--zap-all", "/dev/sda"])

    def test_disk_prep_all_before_basestrap(self):
        basestrap_at = _index_of(self.steps, "basestrap")
        prep_tools = {"sgdisk", "mkfs.fat", "mkfs.ext4", "mount", "mkdir"}
        for i, step in enumerate(self.steps):
            if isinstance(step, RunCommand) and step.argv[0] in prep_tools:
                self.assertLess(i, basestrap_at, step.description)

    def test_partition_creation_commands(self):
        argvs = _argvs(self.steps)
        self.assertIn(
            ["sgdisk", "-n", "1:0:+512M", "-t", "1:ef00", "/dev/sda"], argvs)
        self.assertIn(
            ["sgdisk", "-n", "2:0:0", "-t", "2:8300", "/dev/sda"], argvs)

    def test_mkfs_and_mount_order(self):
        argvs = _argvs(self.steps)
        self.assertIn(["mkfs.fat", "-F32", "/dev/sda1"], argvs)
        self.assertIn(["mkfs.ext4", "-F", "/dev/sda2"], argvs)
        root_mount = argvs.index(["mount", "/dev/sda2", "/mnt"])
        esp_mount = argvs.index(["mount", "/dev/sda1", "/mnt/boot/efi"])
        self.assertLess(root_mount, esp_mount)
        self.assertIn(["mkdir", "-p", "/mnt/boot/efi"], argvs)

    def test_grub_steps_are_last_after_services(self):
        self.assertEqual(self.steps[-2].argv, [
            "artix-chroot", "/mnt", "grub-install", "--target=x86_64-efi",
            "--efi-directory=/boot/efi", "--bootloader-id=Wheatley",
        ])
        self.assertEqual(self.steps[-1].argv, [
            "artix-chroot", "/mnt", "grub-mkconfig", "-o", "/boot/grub/grub.cfg",
        ])
        service_at = _index_of(self.steps, "artix-chroot")
        self.assertIn("ln", self.steps[service_at].argv)
        self.assertLess(service_at, len(self.steps) - 2)

    def test_grub_and_efibootmgr_in_basestrap_sorted(self):
        basestrap = self.steps[_index_of(self.steps, "basestrap")]
        packages = basestrap.argv[2:]
        self.assertIn("grub", packages)
        self.assertIn("efibootmgr", packages)
        self.assertEqual(packages, sorted(packages))

    def test_input_plan_packages_not_mutated(self):
        plan = _plan()
        compile_steps(plan, init_id="dinit", disk_plan=_disk_plan("uefi"))
        self.assertEqual(plan.packages, ["base", "linux-cachyos"])

    def test_deterministic(self):
        again = compile_steps(
            _plan(), init_id="dinit", disk_plan=_disk_plan("uefi"),
        )
        self.assertEqual(self.steps, again)

    def test_custom_target_propagates(self):
        steps = compile_steps(
            _plan(), target="/target", init_id="dinit",
            disk_plan=_disk_plan("uefi"),
        )
        argvs = _argvs(steps)
        self.assertIn(["mount", "/dev/sda2", "/target"], argvs)
        self.assertIn(["mount", "/dev/sda1", "/target/boot/efi"], argvs)
        self.assertEqual(steps[-1].argv[1], "/target")


class TestBiosDiskPlan(unittest.TestCase):
    def setUp(self):
        self.steps = compile_steps(
            _plan(), init_id="runit", disk_plan=_disk_plan("bios", "/dev/nvme0n1"),
        )

    def test_no_esp_mkfs_or_mount(self):
        argvs = _argvs(self.steps)
        flat = [arg for argv in argvs for arg in argv]
        self.assertNotIn("mkfs.fat", flat)
        self.assertNotIn("/mnt/boot/efi", flat)

    def test_bios_boot_partition_type_and_root(self):
        argvs = _argvs(self.steps)
        self.assertIn(
            ["sgdisk", "-n", "1:0:+1M", "-t", "1:ef02", "/dev/nvme0n1"], argvs)
        self.assertIn(["mkfs.ext4", "-F", "/dev/nvme0n1p2"], argvs)
        self.assertIn(["mount", "/dev/nvme0n1p2", "/mnt"], argvs)

    def test_grub_install_targets_i386_pc_with_disk(self):
        self.assertEqual(self.steps[-2].argv, [
            "artix-chroot", "/mnt", "grub-install", "--target=i386-pc",
            "/dev/nvme0n1",
        ])
        self.assertEqual(self.steps[-1].argv[2], "grub-mkconfig")

    def test_grub_but_no_efibootmgr_in_basestrap(self):
        basestrap = self.steps[_index_of(self.steps, "basestrap")]
        packages = basestrap.argv[2:]
        self.assertIn("grub", packages)
        self.assertNotIn("efibootmgr", packages)


class TestDryRunFullList(unittest.TestCase):
    def test_dry_run_logs_every_step_no_side_effects(self):
        steps = compile_steps(
            _plan(), init_id="dinit", disk_plan=_disk_plan("uefi"),
        )
        lines = []
        execute(steps, dry_run=True, log=lines.append)
        self.assertEqual(len(lines), len(steps))
        self.assertTrue(all(line.startswith("DRY-RUN: ") for line in lines))
        self.assertFalse(Path("/mnt/etc/skel/.bashrc").exists())


if __name__ == "__main__":
    unittest.main()
