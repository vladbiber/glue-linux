"""Tests for disk_swap: RAM probe, swap layouts, mkswap/swapon steps."""

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from glue_installer.disk_swap import (
    plan_disk_with_swap, plan_existing_with_swap, read_ram_bytes, swap_steps,
)
from glue_installer.disks import BlockDevice, DiskPlan, PartitionSpec, plan_disk
from glue_installer.executor import RunCommand
from glue_installer.swap import swap_plan

GIB = 2 ** 30


def _dev(size=500 * GIB, path="/dev/sda"):
    return BlockDevice(
        name=path.rsplit("/", 1)[-1], path=path, size_bytes=size, model="x",
        is_removable=False, has_mounted_partitions=False,
    )


def _rows(plan):
    return [(p.number, p.type_code, p.size, p.filesystem, p.mountpoint, p.path)
            for p in plan.partitions]


def _argvs(steps):
    return [s.argv for s in steps if isinstance(s, RunCommand)]


class TestReadRam(unittest.TestCase):
    def test_env_override(self):
        self.assertEqual(read_ram_bytes(env={"GLUE_RAM_BYTES": "17179869184"}),
                         16 * GIB)

    def test_bad_env_gives_zero(self):
        self.assertEqual(read_ram_bytes(env={"GLUE_RAM_BYTES": "lots"}), 0)

    def test_meminfo_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt") as fh:
            fh.write("MemTotal:       16384000 kB\nMemFree: 1 kB\n")
            fh.flush()
            self.assertEqual(read_ram_bytes(fh.name, env={}), 16384000 * 1024)

    def test_missing_or_garbage_file_gives_zero(self):
        self.assertEqual(read_ram_bytes("/nonexistent/meminfo", env={}), 0)
        with tempfile.NamedTemporaryFile("w", suffix=".txt") as fh:
            fh.write("nothing here\n")
            fh.flush()
            self.assertEqual(read_ram_bytes(fh.name, env={}), 0)

    def test_real_proc_meminfo(self):
        self.assertGreater(read_ram_bytes(env={}), 0)


class TestLayout(unittest.TestCase):
    def test_uefi_three_partitions(self):
        p = plan_disk_with_swap(_dev(), "uefi", swap_plan(16 * GIB, 500 * GIB))
        self.assertEqual(_rows(p), [
            (1, "ef00", "+512M", "vfat", "/boot/efi", "/dev/sda1"),
            (2, "8200", "+4096M", "swap", "swap", "/dev/sda2"),
            (3, "8300", "0", "ext4", "/", "/dev/sda3"),
        ])
        self.assertEqual(p.swap_uuid, "")

    def test_bios_three_partitions_nvme(self):
        p = plan_disk_with_swap(_dev(path="/dev/nvme0n1"), "bios",
                                swap_plan(16 * GIB, 500 * GIB))
        self.assertEqual(_rows(p), [
            (1, "ef02", "+1M", "", "", "/dev/nvme0n1p1"),
            (2, "8200", "+4096M", "swap", "swap", "/dev/nvme0n1p2"),
            (3, "8300", "0", "ext4", "/", "/dev/nvme0n1p3"),
        ])

    def test_small_disk_equals_plan_disk(self):
        d = _dev(10 * GIB)
        p = plan_disk_with_swap(d, "uefi", swap_plan(16 * GIB, 10 * GIB))
        self.assertEqual(len(p.partitions), 2)
        self.assertEqual(p, plan_disk(d, "uefi"))

    def test_hibernate_size_and_uuid(self):
        sp = swap_plan(16 * GIB, 500 * GIB, hibernate=True)
        p = plan_disk_with_swap(_dev(), "uefi", sp, swap_uuid="u-1")
        self.assertEqual(p.partitions[1].size, "+17408M")
        self.assertEqual(p.swap_uuid, "u-1")

    def test_uuid_ignored_without_hibernate(self):
        sp = swap_plan(16 * GIB, 500 * GIB)
        p = plan_disk_with_swap(_dev(), "uefi", sp, swap_uuid="u-1")
        self.assertEqual(p.swap_uuid, "")

    def test_zram_and_none_modes_have_no_swap_partition(self):
        for mode in ("zram", "none"):
            sp = swap_plan(16 * GIB, 500 * GIB, mode=mode)
            p = plan_disk_with_swap(_dev(), "uefi", sp)
            self.assertEqual(p, plan_disk(_dev(), "uefi"))
            self.assertEqual(swap_steps(p), [])


class TestSteps(unittest.TestCase):
    def test_partition_steps_without_uuid(self):
        p = plan_disk_with_swap(_dev(), "uefi", swap_plan(16 * GIB, 500 * GIB))
        self.assertEqual(_argvs(swap_steps(p)),
                         [["mkswap", "/dev/sda2"], ["swapon", "/dev/sda2"]])

    def test_partition_steps_with_uuid(self):
        p = plan_disk_with_swap(_dev(), "uefi",
                                swap_plan(16 * GIB, 500 * GIB, hibernate=True),
                                swap_uuid="abc")
        self.assertEqual(_argvs(swap_steps(p))[0],
                         ["mkswap", "-U", "abc", "/dev/sda2"])

    def test_existing_swapfile(self):
        base = DiskPlan(
            device_path="/dev/sda", firmware="bios", mode="existing",
            partitions=(PartitionSpec(2, "/dev/sda2", "8300", "", "ext4", "/"),),
        )
        p = plan_existing_with_swap(base, swap_plan(16 * GIB, 100 * GIB))
        self.assertEqual(p.swapfile_mib, 4096)
        self.assertEqual(_argvs(swap_steps(p, target="/mnt/")), [
            ["fallocate", "-l", "4096M", "/mnt/swapfile"],
            ["chmod", "600", "/mnt/swapfile"],
            ["mkswap", "/mnt/swapfile"],
            ["swapon", "/mnt/swapfile"],
        ])

    def test_existing_without_disk_swap(self):
        base = DiskPlan("/dev/sda", "bios", (), mode="existing")
        p = plan_existing_with_swap(base, swap_plan(16 * GIB, 100 * GIB, mode="zram"))
        self.assertEqual(p.swapfile_mib, 0)
        self.assertEqual(swap_steps(p), [])


if __name__ == "__main__":
    unittest.main()
