"""Unit tests for osdetect.py: os-prober -> /etc/glue/boot.d."""

import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.limine import BootSpec, _CONFIG_KEYS, emitted_keys, limine_conf
from glue_installer.osdetect import (
    boot_d_files, detect_other_os, foreign_entries, live_disk, parse_os_prober,
    summary_lines,
)

_WIN_PU = "deadbeef-1111-2222-3333-444444444444"
_WIN2_PU = "cafebabe-1111-2222-3333-444444444444"
_LIVE_PU = "0badf00d-1111-2222-3333-444444444444"
_GPT1 = "11111111-2222-3333-4444-555555555555"


def _disk(path, parts, rm=False, pttype="gpt", ptuuid=_GPT1):
    name = path.rsplit("/", 1)[-1]
    return {"path": path, "type": "disk", "rm": rm, "hotplug": rm, "pkname": None,
            "uuid": None, "partuuid": None, "pttype": pttype, "ptuuid": ptuuid,
            "partn": None, "children": [
                {"path": f"{path}{n}", "type": "part", "rm": rm, "hotplug": rm,
                 "pkname": name, "uuid": uuid, "partuuid": pu, "pttype": None,
                 "ptuuid": ptuuid, "partn": n}
                for n, uuid, pu in parts]}


def _lsblk(*disks):
    import json
    return json.dumps({"blockdevices": list(disks)})

_EFI = "/dev/sda1@/EFI/Microsoft/Boot/bootmgfw.efi:Windows Boot Manager:Windows:efi\n"
_SDA = _disk("/dev/sda", [(1, "A1B2-C3D4", _WIN_PU), (2, "r00t-uuid", "22222222-0000-0000-0000-000000000002")])
_SDB_LIVE = _disk("/dev/sdb", [(1, "LIVE-0001", _LIVE_PU)], rm=True)
_SDC = _disk("/dev/sdc", [(1, "E5F6-0001", _WIN2_PU)], ptuuid="66666666-2222-3333-4444-555555555555")
_MBR = _disk("/dev/sdd", [(1, "ntfs-0001", "a1b2c3d4-01"), (2, "ntfs-0002", "a1b2c3d4-02")],
             pttype="dos", ptuuid="a1b2c3d4")


def _detect(probe, lsblk, firmware="uefi", **kw):
    kw.setdefault("live_disk", "/dev/sdb")
    kw.setdefault("target_root", "/dev/sda2")
    kw.setdefault("target_boot", "/dev/sda1")
    return foreign_entries(probe, lsblk, firmware=firmware, **kw)


class TestParse(unittest.TestCase):

    def test_efi_chain_linux_lines(self):
        lines = parse_os_prober(_EFI + "/dev/sdd1:Windows 10:Windows:chain\n"
                                "/dev/sda3:Ubuntu 24.04:Ubuntu:linux\n")
        self.assertEqual([(l.device, l.loader, l.long_name, l.short_name, l.kind) for l in lines], [
            ("/dev/sda1", "/EFI/Microsoft/Boot/bootmgfw.efi", "Windows Boot Manager", "Windows", "efi"),
            ("/dev/sdd1", None, "Windows 10", "Windows", "chain"),
            ("/dev/sda3", None, "Ubuntu 24.04", "Ubuntu", "linux")])

    def test_malformed_lines_dropped(self):
        self.assertEqual(parse_os_prober("garbage\n/dev/sda1:only two\n\n# c\nsda1:a:b:efi\n"), [])
        self.assertEqual(parse_os_prober(""), [])


class TestDetect(unittest.TestCase):

    def test_windows_on_target_disk_and_reused_esp_is_kept(self):
        r = _detect(_EFI, _lsblk(_SDA, _SDB_LIVE))
        self.assertEqual(len(r.entries), 1)
        e = r.entries[0]
        self.assertEqual((e.title, e.device, e.partuuid, e.fs_uuid, e.disk, e.kind, e.loader),
                         ("Windows Boot Manager", "/dev/sda1", _WIN_PU, "A1B2-C3D4", "/dev/sda",
                          "efi", "/EFI/Microsoft/Boot/bootmgfw.efi"))
        self.assertEqual(r.skipped, [])

    def test_live_disk_and_removable_excluded(self):
        probe = _EFI + "/dev/sdb1@/EFI/BOOT/BOOTX64.EFI:Live stick:Live:efi\n"
        r = _detect(probe, _lsblk(_SDA, _SDB_LIVE))
        self.assertEqual([e.device for e in r.entries], ["/dev/sda1"])
        self.assertEqual([(s.device, s.reason) for s in r.skipped], [("/dev/sdb1", "on the live medium")])
        # removable that is not live and not target: excluded too
        r = _detect(probe, _lsblk(_SDA, _SDB_LIVE), live_disk=None)
        self.assertEqual([(s.device, s.reason) for s in r.skipped], [("/dev/sdb1", "on a removable disk")])
        # ...unless it IS the target disk
        r = _detect(probe, _lsblk(_SDA, _SDB_LIVE), live_disk=None, target_root="/dev/sdb1")
        self.assertEqual([s.reason for s in r.skipped], ["this partition is formatted for Glue Linux"])

    def test_target_root_partition_excluded(self):
        probe = "/dev/sda2@/EFI/BOOT/BOOTX64.EFI:Old Linux:old:efi\n"
        r = _detect(probe, _lsblk(_SDA))
        self.assertEqual(r.entries, [])
        self.assertEqual(r.skipped[0].reason, "this partition is formatted for Glue Linux")

    def test_erased_disk_excluded(self):
        r = _detect(_EFI, _lsblk(_SDA), erased_disk="/dev/sda")
        self.assertEqual(r.entries, [])
        self.assertIn("erased", r.skipped[0].reason)

    def test_duplicate_os_reported_once(self):
        r = _detect(_EFI + _EFI, _lsblk(_SDA))
        self.assertEqual(len(r.entries), 1)
        self.assertEqual(r.skipped, [])

    def test_two_esps_on_different_disks_two_entries_two_files(self):
        probe = _EFI + "/dev/sdc1@/EFI/Microsoft/Boot/bootmgfw.efi:Windows Boot Manager:Windows:efi\n"
        r = _detect(probe, _lsblk(_SDA, _SDC))
        self.assertEqual([e.partuuid for e in r.entries], [_WIN_PU, _WIN2_PU])
        names = [n for n, _ in boot_d_files(r)]
        self.assertEqual(names, ["10-windows-boot-manager-cafebabe.conf",
                                 "10-windows-boot-manager-deadbeef.conf"])

    def test_missing_partuuid_and_unknown_device_skipped(self):
        sda = _disk("/dev/sda", [(1, "A1B2-C3D4", ""), (2, "x", "22222222-0000-0000-0000-000000000002")])
        r = _detect(_EFI + "/dev/sdz1@/EFI/x.efi:Ghost:Ghost:efi\n", _lsblk(sda))
        self.assertEqual(r.entries, [])
        self.assertEqual([(s.device, s.reason) for s in r.skipped],
                         [("/dev/sda1", "no PARTUUID"), ("/dev/sdz1", "not listed by lsblk")])

    def test_linux_without_loader_needs_linux_boot_prober(self):
        sda = _disk("/dev/sda", [(1, "A1B2-C3D4", _WIN_PU), (3, "u", "33333333-0000-0000-0000-000000000003")])
        r = _detect("/dev/sda3:Ubuntu 24.04:Ubuntu:linux\n", _lsblk(sda), target_root="/dev/sda2")
        self.assertEqual([(s.device, s.reason) for s in r.skipped], [("/dev/sda3", "needs linux-boot-prober")])

    def test_uefi_rejects_chain_and_bios_rejects_efi(self):
        probe = _EFI + "/dev/sdd1:Windows 10:Windows:chain\n"
        r = _detect(probe, _lsblk(_SDA, _MBR))
        self.assertEqual([s.reason for s in r.skipped], ["BIOS boot record, not bootable from UEFI"])
        r = _detect(probe, _lsblk(_SDA, _MBR), firmware="bios")
        self.assertEqual([e.device for e in r.entries], ["/dev/sdd1"])
        self.assertEqual([s.reason for s in r.skipped], ["EFI loader, not bootable from BIOS"])

    def test_bios_mbr_and_gpt_fragments_use_verified_keys(self):
        probe = "/dev/sdd1:Windows 10:Windows:chain\n/dev/sdd2:Windows 7:Windows:chain\n/dev/sdc1:Other:Other:chain\n"
        r = _detect(probe, _lsblk(_SDA, _MBR, _SDC), firmware="bios")
        files = boot_d_files(r)
        self.assertEqual([n for n, _ in files], ["10-windows-10-a1b2c3d4.conf", "10-windows-7-a1b2c3d4.conf",
                                                  "30-other-cafebabe.conf"])
        texts = dict(files)
        self.assertEqual(texts["10-windows-10-a1b2c3d4.conf"],
                         "# Windows 10 on /dev/sdd1 (os-prober)\n/Windows 10\nprotocol: bios\n"
                         "mbr_id: a1b2c3d4\npartition: 1\n")
        self.assertIn("gpt_uuid: 66666666-2222-3333-4444-555555555555\npartition: 1\n",
                      texts["30-other-cafebabe.conf"])
        for text in texts.values():
            self.assertTrue(set(emitted_keys(text)) <= _CONFIG_KEYS)
        # unsupported table -> skipped, never improvised
        weird = _disk("/dev/sde", [(1, "u", "77777777-0000-0000-0000-000000000007")], pttype="", ptuuid="")
        r = _detect("/dev/sde1:Old:Old:chain\n", _lsblk(_SDA, weird), firmware="bios")
        self.assertEqual(r.skipped[0].reason, "unsupported partition table for BIOS chainload")

    def test_efi_fragment_text_and_limine_keys(self):
        r = _detect(_EFI, _lsblk(_SDA))
        name, text = boot_d_files(r)[0]
        self.assertEqual(name, "10-windows-boot-manager-deadbeef.conf")
        self.assertEqual(text, "# Windows Boot Manager on /dev/sda1 (os-prober)\n/Windows Boot Manager\n"
                               f"protocol: efi\nimage_path: guid({_WIN_PU}):/EFI/Microsoft/Boot/bootmgfw.efi\n")
        self.assertNotIn("artix", text.lower())
        conf = limine_conf(BootSpec("linux-cachyos"), "r-1", foreign_entries=(text,))
        self.assertTrue(conf.endswith("\n\n" + text))
        self.assertTrue(set(emitted_keys(conf)) <= _CONFIG_KEYS)

    def test_non_windows_efi_gets_20_prefix(self):
        r = _detect("/dev/sdc1@/EFI/fedora/shimx64.efi:Fedora Linux 42:Fedora:efi\n", _lsblk(_SDA, _SDC))
        self.assertEqual(boot_d_files(r)[0][0], "20-fedora-linux-42-cafebabe.conf")

    def test_summary_lines_english(self):
        r = _detect(_EFI + "/dev/sdb1@/EFI/BOOT/BOOTX64.EFI:Live:Live:efi\n", _lsblk(_SDA, _SDB_LIVE))
        self.assertEqual(summary_lines(r), [
            "Other operating systems kept in the boot menu: Windows Boot Manager (/dev/sda1)",
            "Not added to the boot menu: /dev/sdb1 (on the live medium)"])
        self.assertEqual(summary_lines(_detect("", _lsblk(_SDA))),
                         ["No other operating system was detected."])

    def test_live_disk_from_findmnt_source(self):
        self.assertEqual(live_disk(_lsblk(_SDA, _SDB_LIVE), "/dev/sdb1\n"), "/dev/sdb")
        self.assertIsNone(live_disk(_lsblk(_SDA), "/dev/loop0"))
        self.assertIsNone(live_disk("not json", "/dev/sdb1"))

    def test_bad_firmware_raises(self):
        with self.assertRaises(ValueError):
            foreign_entries("", "{}", firmware="efi", live_disk=None, target_root=None, target_boot=None)


class _Plan:
    def __init__(self):
        from glue_installer.disks import PartitionSpec
        self.firmware, self.mode, self.device_path = "uefi", "erase", "/dev/sdc"
        self.partitions = [PartitionSpec(1, "/dev/sdc1", "ef00", "+512M", "vfat", "/boot"),
                           PartitionSpec(2, "/dev/sdc2", "8300", "0", "ext4", "/")]


class TestDriver(unittest.TestCase):

    def test_env_injection_no_commands(self):
        calls = []
        env = {"GLUE_OSPROBER_OUTPUT": _EFI, "GLUE_LSBLK_JSON": _lsblk(_SDA, _SDC), "GLUE_LIVE_DISK": "/dev/sdb"}
        r, warns = detect_other_os(_Plan(), env=env, capture=lambda a: calls.append(a) or "")
        self.assertEqual(calls, [])
        self.assertEqual(warns, [])
        self.assertEqual([e.device for e in r.entries], ["/dev/sda1"])

    def test_missing_os_prober_warns_and_continues(self):
        def capture(argv):
            if argv[0] == "os-prober":
                raise OSError("No such file or directory: 'os-prober'")
            return _lsblk(_SDA) if argv[0] == "lsblk" else "/dev/sdb1\n"
        r, warns = detect_other_os(_Plan(), env={}, capture=capture)
        self.assertEqual(r.entries, [])
        self.assertEqual(len(warns), 1)
        self.assertIn("os-prober unavailable", warns[0])


if __name__ == "__main__":
    unittest.main()
