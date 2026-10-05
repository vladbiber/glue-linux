"""bootstrap_steps extraction: compile_steps output is unchanged."""

import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.disks import DiskPlan, PartitionSpec, disk_steps
from glue_installer.disk_swap import swap_steps, swapfile_fstab_steps
from glue_installer.executor import (
    RunCommand, bootstrap_steps, compile_steps, config_steps)
from glue_installer.limine import BootSpec, bootloader_steps
from glue_installer.plan import InstallPlan, PlannedFile

_PLAN = InstallPlan(
    packages=["zz", "aa", "linux-cachyos"], services=["NetworkManager", "openntpd"],
    files=[PlannedFile("/etc/a.conf", "a\n", 0o644)], warnings=[])
_DISK = DiskPlan(device_path="/dev/sda", firmware="uefi", partitions=(
    PartitionSpec(1, "/dev/sda1", "ef00", "+512M", "vfat", "/boot"),
    PartitionSpec(2, "/dev/sda2", "8300", "0", "ext4", "/")))


def _golden(init_id, pacman_conf=None, disk=None):
    """The exact list compile_steps produced BEFORE bootstrap_steps existed."""
    t = "/mnt"
    steps = []
    packages = list(_PLAN.packages)
    if disk is not None:
        steps += disk_steps(disk, target=t) + swap_steps(disk, target=t)
        packages = sorted(set(packages) | {"efibootmgr", "glue-boot", "limine"})
    argv = ["basestrap"] + (["-C", pacman_conf] if pacman_conf else []) + [t] + packages + ["--overwrite", "boot/*"]
    steps.append(RunCommand(argv=argv, description=f"Install {len(packages)} packages with basestrap"))
    steps.append(RunCommand(argv=["sh", "-c", "fstabgen -U /mnt >> /mnt/etc/fstab"],
                            description="Generate fstab → /mnt/etc/fstab"))
    if disk is not None:
        steps += swapfile_fstab_steps(disk, target=t)
    steps += config_steps(_PLAN, target=t, init_id=init_id)
    if disk is not None:
        steps += bootloader_steps(disk, BootSpec(kernel="linux-cachyos"), target=t)
    return steps


class TestBootstrapParity(unittest.TestCase):
    def test_compile_steps_identical_per_init(self):
        for init_id in ("dinit", "runit", "openrc"):
            self.assertEqual(compile_steps(_PLAN, init_id=init_id), _golden(init_id), init_id)
            self.assertEqual(compile_steps(_PLAN, init_id=init_id, pacman_conf="/p.conf"),
                             _golden(init_id, "/p.conf"), init_id)
            self.assertEqual(compile_steps(_PLAN, init_id=init_id, disk_plan=_DISK),
                             _golden(init_id, disk=_DISK), init_id)

    def test_bootstrap_keeps_package_order_without_extras(self):
        (step,) = bootstrap_steps(_PLAN, target="/mnt/")
        self.assertEqual(step.argv, ["basestrap", "/mnt", "zz", "aa", "linux-cachyos",
                                     "--overwrite", "boot/*"])
        self.assertEqual(step.description, "Install 3 packages with basestrap")

    def test_bootstrap_merges_extras_sorted_and_single_step(self):
        steps = bootstrap_steps(_PLAN, target="/mnt", pacman_conf="/c",
                                extra_packages=["limine", "aa"])
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].argv, ["basestrap", "-C", "/c", "/mnt", "aa", "limine",
                                         "linux-cachyos", "zz", "--overwrite", "boot/*"])
        self.assertEqual(bootstrap_steps(InstallPlan(["x"], [], [], []))[0].description,
                         "Install 1 package with basestrap")

    def test_bootstrap_never_touches_disk_or_fstab(self):
        for step in bootstrap_steps(_PLAN, extra_packages=["limine"]):
            joined = " ".join(step.argv)
            for tool in ("fstabgen", "mkfs", "sgdisk", "mount"):
                self.assertNotIn(tool, joined)


if __name__ == "__main__":
    unittest.main()
