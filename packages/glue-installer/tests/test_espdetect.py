import json
import unittest

from glue_installer.espdetect import _esp_loaders, esp_entries, merge_esp_entries
from glue_installer.osdetect import (
    ESP_LIST_SCRIPT, LSBLK_ARGV, boot_d_files, detect_other_os, foreign_entries)

# lsblk of the ASUS test laptop (2026-10-16): Glue on nvme0n1, CachyOS (btrfs)
# and another Linux on nvme1n1 with their own ESP holding Limine
LAPTOP = '{"blockdevices":[{"path":"/dev/zram0","type":"disk","rm":false,"hotplug":false,"pkname":null,"uuid":"d284d1af-5699-4af5-bd13-3bde47d04862","partuuid":null,"pttype":null,"ptuuid":null,"partn":null,"parttype":null},{"path":"/dev/nvme0n1","type":"disk","rm":false,"hotplug":false,"pkname":null,"uuid":null,"partuuid":null,"pttype":"gpt","ptuuid":"704cb9e8-72dc-4e81-bfb5-af54d63860e1","partn":null,"parttype":null},{"path":"/dev/nvme0n1p1","type":"part","rm":false,"hotplug":false,"pkname":"nvme0n1","uuid":"5640-247B","partuuid":"1b03db9e-3ddf-46c1-aaa6-e5b380ac78b3","pttype":"gpt","ptuuid":"704cb9e8-72dc-4e81-bfb5-af54d63860e1","partn":1,"parttype":"c12a7328-f81f-11d2-ba4b-00a0c93ec93b"},{"path":"/dev/nvme0n1p2","type":"part","rm":false,"hotplug":false,"pkname":"nvme0n1","uuid":"43f26537-ddf0-44bc-af0f-c1a8f2e96e6c","partuuid":"1eee5244-2470-4308-8cc9-e2f6a570dcf0","pttype":"gpt","ptuuid":"704cb9e8-72dc-4e81-bfb5-af54d63860e1","partn":2,"parttype":"0fc63daf-8483-4772-8e79-3d69d8477de4"},{"path":"/dev/nvme0n1p3","type":"part","rm":false,"hotplug":false,"pkname":"nvme0n1","uuid":"9821026b-f0d6-4b53-9104-cb8b3bb471ab","partuuid":"25fe019a-79fd-4922-ba1a-2d348201a260","pttype":"gpt","ptuuid":"704cb9e8-72dc-4e81-bfb5-af54d63860e1","partn":3,"parttype":"0657fd6d-a4ab-43c4-84e5-0933c84b4f4f"},{"path":"/dev/nvme1n1","type":"disk","rm":false,"hotplug":false,"pkname":null,"uuid":null,"partuuid":null,"pttype":"gpt","ptuuid":"45fc152c-cc51-44db-98c7-cb4ad91d0ff4","partn":null,"parttype":null},{"path":"/dev/nvme1n1p1","type":"part","rm":false,"hotplug":false,"pkname":"nvme1n1","uuid":"0398-3641","partuuid":"63baf4f7-f9ed-4b0b-bf47-5f1f38b4f43a","pttype":"gpt","ptuuid":"45fc152c-cc51-44db-98c7-cb4ad91d0ff4","partn":1,"parttype":"c12a7328-f81f-11d2-ba4b-00a0c93ec93b"},{"path":"/dev/nvme1n1p2","type":"part","rm":false,"hotplug":false,"pkname":"nvme1n1","uuid":"1d877826-758f-4361-9a94-45e565f9fc6a","partuuid":"9b6b2fa8-2949-4e5b-98a9-483851d9b488","pttype":"gpt","ptuuid":"45fc152c-cc51-44db-98c7-cb4ad91d0ff4","partn":2,"parttype":"0fc63daf-8483-4772-8e79-3d69d8477de4"},{"path":"/dev/nvme1n1p3","type":"part","rm":false,"hotplug":false,"pkname":"nvme1n1","uuid":"f07b6e2a-31de-450f-bbd0-95cf211478fc","partuuid":"20e0a4da-742c-4705-97c6-1a62b3e86836","pttype":"gpt","ptuuid":"45fc152c-cc51-44db-98c7-cb4ad91d0ff4","partn":3,"parttype":"0fc63daf-8483-4772-8e79-3d69d8477de4"}]}'
# real `find` output of nvme1n1p1, through ESP_LIST_SCRIPT
LAPTOP_ESP = ["./EFI/limine/limine_x64.efi", "./EFI/limine/BOOTX64.EFI",
              "./EFI/BOOT/BOOTX64.EFI"]
PROBE_LINUX = ("/dev/nvme1n1p2@/@:CachyOS:CachyOS:linux\n"
               "/dev/nvme1n1p3:Finix:Finix:linux\n")


def _detect(probe="", listing=None, boot="/dev/nvme0n1p1", root="/dev/nvme0n1p2",
            erased=None):
    listing = {"/dev/nvme1n1p1": LAPTOP_ESP} if listing is None else listing
    return esp_entries(probe, LAPTOP, lambda dev: listing[dev], live_disk="/dev/sda",
                       target_root=root, target_boot=boot, erased_disk=erased)


class LoaderPickTest(unittest.TestCase):
    def test_limine_dir_wins_over_the_fallback(self):
        self.assertEqual(_esp_loaders(LAPTOP_ESP), [("limine", "/EFI/limine/BOOTX64.EFI")])

    def test_windows_and_distros(self):
        found = _esp_loaders(["./EFI/Microsoft/Boot/bootmgfw.efi",
                              "./EFI/Microsoft/Boot/memtest.efi",
                              "./EFI/ubuntu/grubx64.efi", "./EFI/ubuntu/shimx64.efi",
                              "./EFI/BOOT/BOOTX64.EFI"])
        self.assertEqual(found, [("microsoft", "/EFI/Microsoft/Boot/bootmgfw.efi"),
                                 ("ubuntu", "/EFI/ubuntu/shimx64.efi")])

    def test_fallback_only_when_nothing_else(self):
        self.assertEqual(_esp_loaders(["./EFI/BOOT/BOOTX64.EFI"]),
                         [("boot", "/EFI/BOOT/BOOTX64.EFI")])
        self.assertEqual(_esp_loaders(["./EFI/tools/shell.txt"]), [])


class LaptopTest(unittest.TestCase):
    def test_cachyos_limine_becomes_an_entry(self):
        r = _detect(PROBE_LINUX)
        self.assertEqual(len(r.entries), 1)
        e = r.entries[0]
        self.assertEqual(e.device, "/dev/nvme1n1p1")
        self.assertEqual(e.loader, "/EFI/limine/BOOTX64.EFI")
        self.assertEqual(e.title, "CachyOS, Finix (Limine)")
        name, text = boot_d_files(r)[0]
        self.assertTrue(name.startswith("20-cachyos-finix-limine-"))
        self.assertIn("protocol: efi\n", text)
        self.assertIn(f"image_path: guid({e.partuuid}):/EFI/limine/BOOTX64.EFI", text)

    def test_without_os_prober_the_loader_names_the_entry(self):
        self.assertEqual(_detect().entries[0].title, "Limine")

    def test_without_os_prober_the_esp_menu_names_the_entry(self):
        # the real top-level entries of that ESP's limine.conf
        listing = LAPTOP_ESP + ["TITLE:CachyOS", "TITLE:Other systems and bootloaders",
                                "TITLE:EFI fallback", "TITLE:finix default profile",
                                "TITLE:Windows Boot Manager", "TITLE:Glue Linux",
                                "TITLE:CachyOS"]
        r = _detect(listing={"/dev/nvme1n1p1": listing})
        self.assertEqual(r.entries[0].title, "CachyOS, finix default profile (Limine)")
        self.assertLess(len(boot_d_files(r)[0][0]), 60)
        # os-prober names win when there are some
        r = _detect(PROBE_LINUX, listing={"/dev/nvme1n1p1": listing})
        self.assertEqual(r.entries[0].title, "CachyOS, Finix (Limine)")

    def test_title_lines_are_not_loaders(self):
        self.assertEqual(_esp_loaders(["TITLE:EFI/x/y.efi"]), [])

    def test_own_esp_is_never_listed(self):
        r = _detect(listing={"/dev/nvme1n1p1": LAPTOP_ESP})
        self.assertNotIn("/dev/nvme0n1p1", [e.device for e in r.entries])

    def test_erased_disk_is_skipped(self):
        r = _detect(erased="/dev/nvme1n1", boot=None, root=None)
        self.assertEqual(r.entries, [])
        self.assertIn("/dev/nvme1n1p1", [s.device for s in r.skipped])

    def test_unreadable_esp_only_skips_itself(self):
        def boom(dev):
            raise RuntimeError("mount failed")
        r = esp_entries("", LAPTOP, boom, live_disk=None, target_root="/dev/nvme0n1p2",
                        target_boot="/dev/nvme0n1p1")
        self.assertEqual(r.entries, [])
        self.assertIn("could not be read", r.skipped[0].reason)


class MergeTest(unittest.TestCase):
    def test_windows_from_os_prober_is_not_doubled(self):
        probe = "/dev/nvme1n1p1@/EFI/Microsoft/Boot/bootmgfw.efi:Windows Boot Manager:Windows:efi\n"
        result = foreign_entries(probe, LAPTOP, firmware="uefi", live_disk=None,
                                 target_root="/dev/nvme0n1p2", target_boot="/dev/nvme0n1p1")
        extra = _detect(probe, {"/dev/nvme1n1p1": ["./EFI/Microsoft/Boot/bootmgfw.efi"]})
        merge_esp_entries(result, extra)
        self.assertEqual([e.title for e in result.entries], ["Windows Boot Manager"])


class DriverTest(unittest.TestCase):
    class _Plan:
        def __init__(self):
            from glue_installer.disks import PartitionSpec
            self.firmware, self.mode, self.device_path = "uefi", "manual", "/dev/nvme0n1"
            self.partitions = [
                PartitionSpec(1, "/dev/nvme0n1p1", "ef00", "+1G", "vfat", "/boot"),
                PartitionSpec(2, "/dev/nvme0n1p2", "8300", "0", "ext4", "/")]

    def test_env_listing_and_lsblk_has_parttype(self):
        self.assertIn("PARTTYPE", LSBLK_ARGV[-1])
        env = {"GLUE_OSPROBER_OUTPUT": PROBE_LINUX, "GLUE_LSBLK_JSON": LAPTOP,
               "GLUE_LIVE_DISK": "/dev/sda",
               "GLUE_ESP_LISTING": json.dumps({"/dev/nvme1n1p1": LAPTOP_ESP})}
        calls = []
        r, warns = detect_other_os(self._Plan(), env=env,
                                   capture=lambda a: calls.append(a) or "")
        self.assertEqual(calls, [])
        self.assertEqual([e.title for e in r.entries], ["CachyOS, Finix (Limine)"])

    def test_default_listing_mounts_read_only_through_capture(self):
        env = {"GLUE_OSPROBER_OUTPUT": "", "GLUE_LSBLK_JSON": LAPTOP,
               "GLUE_LIVE_DISK": "/dev/sda"}
        calls = []

        def capture(argv):
            calls.append(list(argv))
            return "\n".join(LAPTOP_ESP)
        r, _ = detect_other_os(self._Plan(), env=env, capture=capture)
        self.assertEqual(calls, [["sh", "-c", ESP_LIST_SCRIPT, "sh", "/dev/nvme1n1p1"]])
        self.assertIn("mount -o ro", ESP_LIST_SCRIPT)
        self.assertEqual(len(r.entries), 1)


if __name__ == "__main__":
    unittest.main()
