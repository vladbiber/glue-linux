"""Unit tests for compile_steps' disk_plan integration (disk prep + Limine)."""

import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.disks import BlockDevice, plan_disk
from glue_installer.executor import (
    RunCommand, WriteTargetFile, compile_steps, execute,
)
from glue_installer.plan import InstallPlan, PlannedFile

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
            RunCommand(
                argv=[
                    "artix-chroot", "/mnt", "sh", "-c",
                    "f=/usr/share/dbus-1/system-services/"
                    "org.freedesktop.ModemManager1.service; "
                    '[ -e "$f" ] && mv -f "$f" "$f.glue-disabled"; true',
                ],
                description="Disable ModemManager D-Bus activation (console spam)",
            ),
        ]
        self.assertEqual(steps, expected)

    def test_no_grub_or_sgdisk_without_disk_plan(self):
        argvs = _argvs(compile_steps(_plan(), init_id="runit"))
        flat = [arg for argv in argvs for arg in argv]
        for tool in ("sgdisk", "mkfs.ext4", "mkfs.fat", "limine", "efibootmgr", "mount"):
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
            if isinstance(step, RunCommand) and step.argv[0] in prep_tools \
                    and "EFI" not in step.argv[-1]:  # Limine dirs come after
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
        esp_mount = argvs.index(["mount", "/dev/sda1", "/mnt/boot"])
        self.assertLess(root_mount, esp_mount)
        self.assertIn(["mkdir", "-p", "/mnt/boot"], argvs)

    def test_limine_steps_are_last_after_services(self):
        argvs = _argvs(self.steps)
        tail = argvs[-5:]
        self.assertEqual(tail[0], ["mkdir", "-p", "/mnt/boot/EFI/BOOT", "/mnt/boot/EFI/limine"])
        self.assertEqual(tail[1], ["cp", "/mnt/usr/share/limine/BOOTX64.EFI",
                                   "/mnt/boot/EFI/BOOT/BOOTX64.EFI"])
        self.assertEqual(tail[2], ["cp", "/mnt/usr/share/limine/BOOTX64.EFI",
                                   "/mnt/boot/EFI/limine/BOOTX64.EFI"])
        # live side (not chrooted): firmware boot entry
        self.assertEqual(tail[3], [
            "efibootmgr", "--create", "--disk", "/dev/sda", "--part", "1",
            "--loader", "\\EFI\\limine\\BOOTX64.EFI",
            "--label", "Glue Linux", "--unicode",
        ])
        # LAST step: chrooted writer of /boot/limine.conf with the real UUIDs
        self.assertEqual(self.steps[-1].argv[:4], ["artix-chroot", "/mnt", "sh", "-c"])
        self.assertIn("findmnt -no UUID /", self.steps[-1].argv[4])
        self.assertIn("/boot/limine.conf", self.steps[-1].argv[4])
        self.assertIn("vmlinuz-linux-cachyos", self.steps[-1].argv[4])
        service_at = _index_of(self.steps, "artix-chroot")
        self.assertIn("ln", self.steps[service_at].argv)
        self.assertLess(service_at, len(self.steps) - 5)

    def test_no_grub_anywhere(self):
        for argv in _argvs(self.steps):
            for token in argv:
                self.assertFalse(token.startswith("grub"), argv)
                self.assertNotIn("grub-mkconfig", token)

    def test_boot_spec_changes_kernel_and_cmdline(self):
        from glue_installer.limine import BootSpec
        steps = compile_steps(
            _plan(), init_id="dinit", disk_plan=_disk_plan("uefi"),
            boot=BootSpec("linux", ("amd_pstate=active",)),
        )
        script = steps[-1].argv[4]
        self.assertIn("vmlinuz-linux\n", script)
        self.assertIn("amd_pstate=active", script)
        default = compile_steps(_plan(), init_id="dinit", disk_plan=_disk_plan("uefi"))
        self.assertEqual(default, compile_steps(
            _plan(), init_id="dinit", disk_plan=_disk_plan("uefi"),
            boot=BootSpec("linux-cachyos")))

    def test_limine_and_efibootmgr_in_basestrap_sorted(self):
        basestrap = self.steps[_index_of(self.steps, "basestrap")]
        packages = basestrap.argv[2:]
        self.assertIn("limine", packages)
        self.assertIn("efibootmgr", packages)
        self.assertNotIn("grub", packages)
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
        self.assertIn(["mount", "/dev/sda1", "/target/boot"], argvs)
        self.assertIn(["cp", "/target/usr/share/limine/BOOTX64.EFI",
                       "/target/boot/EFI/limine/BOOTX64.EFI"], argvs)
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
        self.assertNotIn("/mnt/boot", flat)

    def test_bios_boot_partition_type_and_root(self):
        argvs = _argvs(self.steps)
        self.assertIn(
            ["sgdisk", "-n", "1:0:+1M", "-t", "1:ef02", "/dev/nvme0n1"], argvs)
        self.assertIn(["mkfs.ext4", "-F", "/dev/nvme0n1p2"], argvs)
        self.assertIn(["mount", "/dev/nvme0n1p2", "/mnt"], argvs)

    def test_limine_bios_install_with_disk(self):
        argvs = _argvs(self.steps)
        self.assertIn(["limine", "bios-install", "/dev/nvme0n1"], argvs)
        self.assertIn(["cp", "/mnt/usr/share/limine/limine-bios.sys", "/mnt/boot/"], argvs)
        self.assertEqual(self.steps[-2].argv, ["limine", "bios-install", "/dev/nvme0n1"])
        self.assertEqual(self.steps[-1].argv[:4], ["artix-chroot", "/mnt", "sh", "-c"])
        self.assertIn("/boot/limine.conf", self.steps[-1].argv[4])
        flat = [a for argv in argvs for a in argv]
        self.assertNotIn("efibootmgr", flat)
        self.assertFalse(any(a.startswith("grub") for a in flat))

    def test_limine_but_no_efibootmgr_in_basestrap(self):
        basestrap = self.steps[_index_of(self.steps, "basestrap")]
        packages = basestrap.argv[2:]
        self.assertIn("limine", packages)
        self.assertNotIn("grub", packages)
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


class TestSwapSteps(unittest.TestCase):
    def _plan_with_swap(self, **kw):
        import dataclasses
        from glue_installer.disk_swap import plan_disk_with_swap
        from glue_installer.swap import swap_plan
        device = BlockDevice(
            name="sda", path="/dev/sda", size_bytes=500 * _GIB,
            model="Test Disk", is_removable=False, has_mounted_partitions=False,
        )
        dp = plan_disk_with_swap(device, "uefi", swap_plan(16 * _GIB, 500 * _GIB))
        return dataclasses.replace(dp, **kw)

    def _idx(self, steps, argv):
        return _argvs(steps).index(argv)

    def test_swap_after_root_mount_before_basestrap_and_fstab(self):
        steps = compile_steps(_plan(), init_id="dinit",
                              disk_plan=self._plan_with_swap())
        mount = self._idx(steps, ["mount", "/dev/sda3", "/mnt"])
        mkswap = self._idx(steps, ["mkswap", "/dev/sda2"])
        swapon = self._idx(steps, ["swapon", "/dev/sda2"])
        basestrap = _index_of(steps, "basestrap")
        fstab = next(i for i, s in enumerate(steps)
                     if "fstabgen" in " ".join(getattr(s, "argv", [])))
        self.assertLess(mount, mkswap)
        self.assertLess(mkswap, swapon)
        self.assertLess(swapon, basestrap)
        self.assertLess(basestrap, fstab)

    def test_no_mkfs_on_swap_partition(self):
        steps = compile_steps(_plan(), init_id="dinit",
                              disk_plan=self._plan_with_swap())
        for argv in _argvs(steps):
            if argv[0].startswith("mkfs"):
                self.assertNotEqual(argv[-1], "/dev/sda2")

    def test_hibernate_boot_spec_adds_resume_to_writer(self):
        from glue_installer.limine import BootSpec
        steps = compile_steps(_plan(), init_id="dinit",
                              disk_plan=self._plan_with_swap(swap_uuid="abc"),
                              boot=BootSpec("linux-cachyos", (), True))
        script = steps[-1].argv[4]
        self.assertIn("blkid -s UUID -o value /dev/sda2", script)
        self.assertIn("resume=UUID=@RESUME_UUID@", script)
        self.assertIn("mkinitcpio -P", script)

    def test_swap_uuid_passed_to_mkswap(self):
        steps = compile_steps(_plan(), init_id="dinit",
                              disk_plan=self._plan_with_swap(swap_uuid="abc"))
        self.assertIn(["mkswap", "-U", "abc", "/dev/sda2"], _argvs(steps))

    def test_swapfile_steps_in_order(self):
        dp = _disk_plan("uefi")
        import dataclasses
        dp = dataclasses.replace(dp, swapfile_mib=4096)
        argvs = _argvs(compile_steps(_plan(), init_id="dinit", disk_plan=dp))
        seq = [["fallocate", "-l", "4096M", "/mnt/swapfile"],
               ["chmod", "600", "/mnt/swapfile"],
               ["mkswap", "/mnt/swapfile"],
               ["swapon", "/mnt/swapfile"]]
        idx = [argvs.index(a) for a in seq]
        self.assertEqual(idx, sorted(idx))
        self.assertLess(idx[-1], argvs.index(
            next(a for a in argvs if a[0] == "basestrap")))


if __name__ == "__main__":
    unittest.main()
