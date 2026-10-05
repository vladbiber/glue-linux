"""Unit tests for glue_installer.disks (pure parsing/planning; no root)."""

import dataclasses
import json
import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.disks import (
    MIN_DISK_BYTES, BlockDevice, DiskError, DiskPlan, PartitionSpec,
    parse_lsblk, partition_path, plan_disk,
)

_GIB = 1024 ** 3

# Realistic `lsblk --json -b -o NAME,PATH,SIZE,TYPE,MODEL,RM,MOUNTPOINTS`
# output: one mounted SATA disk (with swap), one empty NVMe disk, a loop
# device, and a removable USB stick.
_LSBLK_FIXTURE = json.dumps({
    "blockdevices": [
        {
            "name": "sda", "path": "/dev/sda", "size": 500 * _GIB,
            "type": "disk", "model": "Samsung SSD 860", "rm": False,
            "mountpoints": [None],
            "children": [
                {
                    "name": "sda1", "path": "/dev/sda1", "size": 512 * 1024 ** 2,
                    "type": "part", "model": None, "rm": False,
                    "mountpoints": ["/boot"],
                },
                {
                    "name": "sda2", "path": "/dev/sda2", "size": 491 * _GIB,
                    "type": "part", "model": None, "rm": False,
                    "mountpoints": ["/"],
                },
                {
                    "name": "sda3", "path": "/dev/sda3", "size": 8 * _GIB,
                    "type": "part", "model": None, "rm": False,
                    "mountpoints": ["[SWAP]"],
                },
            ],
        },
        {
            "name": "nvme0n1", "path": "/dev/nvme0n1", "size": 1000 * _GIB,
            "type": "disk", "model": "WD_BLACK SN850X", "rm": False,
            "mountpoints": [None],
        },
        {
            "name": "loop0", "path": "/dev/loop0", "size": 700 * 1024 ** 2,
            "type": "loop", "model": None, "rm": False,
            "mountpoints": ["/run/archiso/sfs"],
        },
        {
            "name": "sdb", "path": "/dev/sdb", "size": 16 * _GIB,
            "type": "disk", "model": "SanDisk Ultra", "rm": True,
            "mountpoints": [None],
            "children": [
                {
                    "name": "sdb1", "path": "/dev/sdb1", "size": 16 * _GIB,
                    "type": "part", "model": None, "rm": True,
                    "mountpoints": [None],
                },
            ],
        },
    ],
})


def _device(size_bytes=64 * _GIB, path="/dev/sda", mounted=False) -> BlockDevice:
    return BlockDevice(
        name=path.rsplit("/", 1)[-1], path=path, size_bytes=size_bytes,
        model="Test Disk", is_removable=False,
        has_mounted_partitions=mounted,
    )


# ---------------------------------------------------------------------------
# parse_lsblk
# ---------------------------------------------------------------------------

class TestParseLsblk(unittest.TestCase):
    def test_happy_path_returns_only_disks(self):
        devices = parse_lsblk(_LSBLK_FIXTURE)
        self.assertEqual([d.name for d in devices], ["sda", "nvme0n1", "sdb"])

    def test_loop_and_part_types_filtered_out(self):
        devices = parse_lsblk(_LSBLK_FIXTURE)
        self.assertNotIn("loop0", [d.name for d in devices])
        self.assertNotIn("sda1", [d.name for d in devices])

    def test_fields_mapped_correctly(self):
        nvme = parse_lsblk(_LSBLK_FIXTURE)[1]
        self.assertEqual(nvme.path, "/dev/nvme0n1")
        self.assertEqual(nvme.size_bytes, 1000 * _GIB)
        self.assertEqual(nvme.model, "WD_BLACK SN850X")
        self.assertFalse(nvme.is_removable)

    def test_disk_with_mounted_children_flagged(self):
        sda = parse_lsblk(_LSBLK_FIXTURE)[0]
        self.assertTrue(sda.has_mounted_partitions)

    def test_disk_without_mounts_not_flagged(self):
        nvme = parse_lsblk(_LSBLK_FIXTURE)[1]
        self.assertFalse(nvme.has_mounted_partitions)

    def test_unmounted_children_not_flagged(self):
        sdb = parse_lsblk(_LSBLK_FIXTURE)[2]
        self.assertTrue(sdb.is_removable)
        self.assertFalse(sdb.has_mounted_partitions)

    def test_swap_counts_as_mounted(self):
        fixture = json.dumps({"blockdevices": [{
            "name": "sdz", "path": "/dev/sdz", "size": 32 * _GIB,
            "type": "disk", "model": None, "rm": False, "mountpoints": [None],
            "children": [{
                "name": "sdz1", "path": "/dev/sdz1", "size": 32 * _GIB,
                "type": "part", "model": None, "rm": False,
                "mountpoints": ["[SWAP]"],
            }],
        }]})
        self.assertTrue(parse_lsblk(fixture)[0].has_mounted_partitions)

    def test_disk_itself_mounted_flagged(self):
        fixture = json.dumps({"blockdevices": [{
            "name": "sdz", "path": "/dev/sdz", "size": 32 * _GIB,
            "type": "disk", "model": None, "rm": False,
            "mountpoints": ["/mnt/whole-disk"],
        }]})
        self.assertTrue(parse_lsblk(fixture)[0].has_mounted_partitions)

    def test_nested_grandchild_mount_flagged(self):
        fixture = json.dumps({"blockdevices": [{
            "name": "sdz", "path": "/dev/sdz", "size": 32 * _GIB,
            "type": "disk", "model": None, "rm": False, "mountpoints": [None],
            "children": [{
                "name": "sdz1", "path": "/dev/sdz1", "size": 32 * _GIB,
                "type": "part", "model": None, "rm": False,
                "mountpoints": [None],
                "children": [{
                    "name": "cryptroot", "path": "/dev/mapper/cryptroot",
                    "size": 32 * _GIB, "type": "crypt", "model": None,
                    "rm": False, "mountpoints": ["/"],
                }],
            }],
        }]})
        self.assertTrue(parse_lsblk(fixture)[0].has_mounted_partitions)

    def test_null_model_becomes_empty_string(self):
        fixture = json.dumps({"blockdevices": [{
            "name": "vda", "path": "/dev/vda", "size": 32 * _GIB,
            "type": "disk", "model": None, "rm": False, "mountpoints": [None],
        }]})
        self.assertEqual(parse_lsblk(fixture)[0].model, "")

    def test_malformed_json_raises_disk_error(self):
        with self.assertRaises(DiskError):
            parse_lsblk("{not json")

    def test_missing_blockdevices_key_raises(self):
        with self.assertRaises(DiskError):
            parse_lsblk('{"version": 1}')

    def test_non_dict_top_level_raises(self):
        with self.assertRaises(DiskError):
            parse_lsblk('["a", "b"]')

    def test_missing_required_key_raises(self):
        fixture = json.dumps({"blockdevices": [
            {"name": "sda", "type": "disk", "size": 32 * _GIB},  # no path
        ]})
        with self.assertRaises(DiskError):
            parse_lsblk(fixture)

    def test_non_integer_size_raises(self):
        fixture = json.dumps({"blockdevices": [{
            "name": "sda", "path": "/dev/sda", "size": "500G",
            "type": "disk", "model": None, "rm": False, "mountpoints": [None],
        }]})
        with self.assertRaises(DiskError):
            parse_lsblk(fixture)

    def test_empty_blockdevices_gives_empty_list(self):
        self.assertEqual(parse_lsblk('{"blockdevices": []}'), [])


# ---------------------------------------------------------------------------
# partition_path
# ---------------------------------------------------------------------------

class TestPartitionPath(unittest.TestCase):
    def test_sda_style_appends_number(self):
        self.assertEqual(partition_path("/dev/sda", 1), "/dev/sda1")
        self.assertEqual(partition_path("/dev/sdb", 2), "/dev/sdb2")

    def test_nvme_style_inserts_p(self):
        self.assertEqual(partition_path("/dev/nvme0n1", 1), "/dev/nvme0n1p1")
        self.assertEqual(partition_path("/dev/nvme0n1", 2), "/dev/nvme0n1p2")

    def test_mmcblk_style_inserts_p(self):
        self.assertEqual(partition_path("/dev/mmcblk0", 1), "/dev/mmcblk0p1")

    def test_vda_style_appends_number(self):
        self.assertEqual(partition_path("/dev/vda", 2), "/dev/vda2")

    def test_empty_path_raises(self):
        with self.assertRaises(DiskError):
            partition_path("", 1)


# ---------------------------------------------------------------------------
# plan_disk
# ---------------------------------------------------------------------------

class TestPlanDisk(unittest.TestCase):
    def test_uefi_layout(self):
        plan = plan_disk(_device(), "uefi")
        self.assertEqual(plan.firmware, "uefi")
        self.assertEqual(plan.device_path, "/dev/sda")
        self.assertEqual(len(plan.partitions), 2)
        esp, root = plan.partitions
        self.assertEqual(
            (esp.number, esp.path, esp.type_code, esp.size, esp.filesystem, esp.mountpoint),
            (1, "/dev/sda1", "ef00", "+512M", "vfat", "/boot"),
        )
        self.assertEqual(
            (root.number, root.path, root.type_code, root.size, root.filesystem, root.mountpoint),
            (2, "/dev/sda2", "8300", "0", "ext4", "/"),
        )

    def test_bios_layout(self):
        plan = plan_disk(_device(), "bios")
        self.assertEqual(plan.firmware, "bios")
        self.assertEqual(len(plan.partitions), 2)
        boot, root = plan.partitions
        self.assertEqual(
            (boot.number, boot.type_code, boot.size, boot.filesystem, boot.mountpoint),
            (1, "ef02", "+1M", "", ""),
        )
        self.assertEqual(
            (root.type_code, root.filesystem, root.mountpoint),
            ("8300", "ext4", "/"),
        )

    def test_nvme_partition_paths_in_plan(self):
        plan = plan_disk(_device(path="/dev/nvme0n1"), "uefi")
        self.assertEqual(plan.partitions[0].path, "/dev/nvme0n1p1")
        self.assertEqual(plan.partitions[1].path, "/dev/nvme0n1p2")

    def test_bad_firmware_raises(self):
        for bad in ("efi", "UEFI", "legacy", "", "auto"):
            with self.assertRaises(DiskError):
                plan_disk(_device(), bad)

    def test_too_small_disk_raises(self):
        with self.assertRaises(DiskError):
            plan_disk(_device(size_bytes=8 * _GIB - 1), "uefi")

    def test_exactly_8_gib_is_accepted(self):
        plan = plan_disk(_device(size_bytes=MIN_DISK_BYTES), "bios")
        self.assertEqual(len(plan.partitions), 2)

    def test_mounted_disk_raises(self):
        with self.assertRaises(DiskError):
            plan_disk(_device(mounted=True), "uefi")

    def test_plan_is_frozen(self):
        plan = plan_disk(_device(), "uefi")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            plan.firmware = "bios"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            plan.partitions[0].size = "+1G"

    def test_plan_is_deterministic(self):
        self.assertEqual(plan_disk(_device(), "uefi"), plan_disk(_device(), "uefi"))
        self.assertEqual(plan_disk(_device(), "bios"), plan_disk(_device(), "bios"))

    def test_block_device_is_frozen(self):
        device = _device()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            device.size_bytes = 0


# ---------------------------------------------------------------------------
# Module purity (acceptance criterion 4)
# ---------------------------------------------------------------------------

class TestModulePurity(unittest.TestCase):
    def test_no_curses_and_subprocess_only_in_discover(self):
        source = (_PKG_ROOT / "glue_installer" / "disks.py").read_text()
        self.assertNotIn("curses", source)
        top_level = [ln.strip() for ln in source.splitlines()
                     if ln and not ln.startswith((" ", "\t"))]
        self.assertNotIn("import subprocess", top_level)
        self.assertFalse(any(ln.startswith("from subprocess") for ln in top_level))
        # imported locally exactly once, inside discover()
        self.assertEqual(source.count("    import subprocess"), 1)

    def test_pure_functions_never_raise_system_exit(self):
        with self.assertRaises(DiskError):
            parse_lsblk("nope")
        with self.assertRaises(DiskError):
            plan_disk(_device(), "not-a-firmware")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Existing-partition mode (choose a partition / manual cfdisk flow)
# ---------------------------------------------------------------------------

from glue_installer.disks import (  # noqa: E402
    Partition, disk_steps, parse_lsblk_partitions, plan_existing_partition,
)
from glue_installer.executor import RunCommand  # noqa: E402

_ESP_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"

_PARTS_FIXTURE = json.dumps({
    "blockdevices": [
        {
            "name": "sda", "path": "/dev/sda", "size": 500 * _GIB,
            "type": "disk", "mountpoints": [None],
            "children": [
                {"name": "sda1", "path": "/dev/sda1", "size": 512 * 1024 ** 2,
                 "type": "part", "mountpoints": [None],
                 "fstype": "vfat", "parttype": _ESP_GUID},
                {"name": "sda2", "path": "/dev/sda2", "size": 100 * _GIB,
                 "type": "part", "mountpoints": ["/"],
                 "fstype": "ext4", "parttype": None},
                {"name": "sda3", "path": "/dev/sda3", "size": 60 * _GIB,
                 "type": "part", "mountpoints": [None],
                 "fstype": None, "parttype": None},
            ],
        },
    ]
})


def _parts():
    return parse_lsblk_partitions(_PARTS_FIXTURE)


class TestParsePartitions(unittest.TestCase):

    def test_parses_children_with_esp_and_mount_flags(self):
        parts = _parts()
        self.assertEqual([p.path for p in parts],
                         ["/dev/sda1", "/dev/sda2", "/dev/sda3"])
        self.assertTrue(parts[0].is_esp)
        self.assertTrue(parts[1].is_mounted)
        self.assertFalse(parts[2].is_mounted)
        self.assertEqual(parts[2].fstype, "")
        self.assertEqual(parts[2].parent_path, "/dev/sda")


class TestPlanExistingPartition(unittest.TestCase):

    def test_real_partition_numbers_for_efibootmgr(self):
        parts = _parts()
        plan = plan_existing_partition(parts[2], "uefi", parts)
        self.assertEqual([(p.number, p.path) for p in plan.partitions],
                         [(1, "/dev/sda1"), (3, "/dev/sda3")])

    def test_small_esp_warns_large_esp_does_not(self):
        parts = _parts()
        esp = parts[0]
        small = [Partition(name=esp.name, path=esp.path, parent_path=esp.parent_path,
                           size_bytes=100 * 1024 ** 2, fstype="vfat", is_esp=True,
                           is_mounted=False)] + parts[1:]
        plan = plan_existing_partition(small[2], "uefi", small)
        self.assertEqual(len(plan.warnings), 1)
        self.assertIn("EFI partition is small", plan.warnings[0])
        big = [Partition(name=esp.name, path=esp.path, parent_path=esp.parent_path,
                         size_bytes=512 * 1024 ** 2, fstype="vfat", is_esp=True,
                         is_mounted=False)] + parts[1:]
        self.assertEqual(plan_existing_partition(big[2], "uefi", big).warnings, ())

    def test_uefi_reuses_esp_and_formats_only_root(self):
        parts = _parts()
        plan = plan_existing_partition(parts[2], "uefi", parts)
        self.assertEqual(plan.mode, "existing")
        esp = next(p for p in plan.partitions if p.mountpoint == "/boot")
        root = next(p for p in plan.partitions if p.mountpoint == "/")
        self.assertEqual(esp.path, "/dev/sda1")
        self.assertEqual(esp.filesystem, "")   # reuse - never reformatted
        self.assertEqual(root.path, "/dev/sda3")
        self.assertEqual(root.filesystem, "ext4")

    def test_uefi_without_esp_raises(self):
        parts = [p for p in _parts() if not p.is_esp]
        with self.assertRaises(DiskError) as ctx:
            plan_existing_partition(parts[-1], "uefi", parts)
        self.assertIn("EFI System Partition", str(ctx.exception))

    def test_mounted_partition_raises(self):
        parts = _parts()
        with self.assertRaises(DiskError):
            plan_existing_partition(parts[1], "bios", parts)

    def test_too_small_partition_raises(self):
        small = Partition(name="sdb1", path="/dev/sdb1", parent_path="/dev/sdb",
                          size_bytes=2 * _GIB, fstype="", is_esp=False,
                          is_mounted=False)
        with self.assertRaises(DiskError):
            plan_existing_partition(small, "bios", [small])

    def test_existing_steps_never_touch_partition_table(self):
        parts = _parts()
        plan = plan_existing_partition(parts[2], "uefi", parts)
        steps = disk_steps(plan, target="/mnt")
        argvs = [" ".join(s.argv) for s in steps if isinstance(s, RunCommand)]
        self.assertFalse(any("sgdisk" in a for a in argvs))
        self.assertFalse(any("mkfs.fat" in a for a in argvs))   # ESP untouched
        self.assertTrue(any("mkfs.ext4 -F /dev/sda3" in a for a in argvs))
        self.assertTrue(any(a.startswith("mount /dev/sda3 /mnt") for a in argvs))
        self.assertTrue(any("mount /dev/sda1 /mnt/boot" in a for a in argvs))
