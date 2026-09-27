"""Unit tests for wheatley_installer.grub_filter (pure filtering core)."""

import json
import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from wheatley_installer.grub_filter import (
    filter_grub_cfg, main, removable_identifiers,
)

_INTERNAL_UUID = "1111aaaa-2222-3333-4444-555566667777"
_WINDOWS_UUID = "8888-BBBB"
_USB_UUID = "9999-CCCC"

_SAMPLE_CFG = f"""\
### BEGIN /etc/grub.d/10_linux ###
menuentry 'Wheatley Linux' --class wheatley {{
    search --no-floppy --fs-uuid --set=root {_INTERNAL_UUID}
    linux /boot/vmlinuz-linux-cachyos root=UUID={_INTERNAL_UUID}
}}
submenu 'Advanced options for Wheatley Linux' {{
    menuentry 'Wheatley Linux, fallback' {{
        search --no-floppy --fs-uuid --set=root {_INTERNAL_UUID}
    }}
}}
### END /etc/grub.d/10_linux ###
### BEGIN /etc/grub.d/30_os-prober ###
menuentry 'Windows Boot Manager (on /dev/nvme0n1p1)' --class windows {{
    search --no-floppy --fs-uuid --set=root {_WINDOWS_UUID}
    chainloader /EFI/Microsoft/Boot/bootmgfw.efi
}}
menuentry 'Wheatley Linux Installer (on /dev/sdb1)' --class gnu-linux {{
    search --no-floppy --fs-uuid --set=root {_USB_UUID}
    linux /boot/vmlinuz
}}
submenu 'Advanced options for Wheatley Linux Installer (on /dev/sdb1)' {{
    menuentry 'Wheatley Linux Installer (live)' {{
        search --no-floppy --fs-uuid --set=root {_USB_UUID}
    }}
}}
### END /etc/grub.d/30_os-prober ###
"""

_LSBLK_JSON = json.dumps({"blockdevices": [
    {"path": "/dev/nvme0n1", "type": "disk", "rm": False, "children": [
        {"path": "/dev/nvme0n1p1", "type": "part", "uuid": _WINDOWS_UUID,
         "partuuid": "aaaa0001-0000-0000-0000-000000000001"},
        {"path": "/dev/nvme0n1p2", "type": "part", "uuid": _INTERNAL_UUID,
         "partuuid": "aaaa0002-0000-0000-0000-000000000002"},
    ]},
    {"path": "/dev/sdb", "type": "disk", "rm": True, "children": [
        {"path": "/dev/sdb1", "type": "part", "uuid": _USB_UUID,
         "partuuid": "bbbb0001-0000-0000-0000-000000000001"},
        {"path": "/dev/sdb2", "type": "part", "uuid": None, "partuuid": None},
    ]},
]})


class TestRemovableIdentifiers(unittest.TestCase):

    def test_collects_only_removable_disk_partitions(self):
        ids = removable_identifiers(_LSBLK_JSON)
        self.assertIn("/dev/sdb1", ids)
        self.assertIn(_USB_UUID, ids)
        self.assertIn("bbbb0001-0000-0000-0000-000000000001", ids)
        self.assertNotIn("/dev/nvme0n1p1", ids)
        self.assertNotIn(_WINDOWS_UUID, ids)
        self.assertNotIn(_INTERNAL_UUID, ids)

    def test_extra_disks_pull_in_non_removable_live_disk(self):
        ids = removable_identifiers(_LSBLK_JSON, extra_disks=["/dev/nvme0n1"])
        self.assertIn(_WINDOWS_UUID, ids)

    def test_exclude_disks_wins_over_removable(self):
        # target = a removable USB drive: its partitions must NOT be filtered
        ids = removable_identifiers(_LSBLK_JSON, exclude_disks=["/dev/sdb"])
        self.assertEqual(ids, [])

    def test_null_and_short_ids_dropped(self):
        ids = removable_identifiers(_LSBLK_JSON)
        for ident in ids:
            self.assertGreaterEqual(len(ident), 4)

    def test_malformed_json_yields_empty(self):
        self.assertEqual(removable_identifiers("{not json"), [])
        self.assertEqual(removable_identifiers("{}"), [])


class TestFilterGrubCfg(unittest.TestCase):

    def test_drops_usb_entries_keeps_windows_and_wheatley(self):
        ids = removable_identifiers(_LSBLK_JSON)
        out = filter_grub_cfg(_SAMPLE_CFG, ids)
        self.assertIn("menuentry 'Wheatley Linux'", out)
        self.assertIn("Advanced options for Wheatley Linux'", out)
        self.assertIn("Windows Boot Manager", out)
        self.assertNotIn("Installer", out)
        self.assertNotIn(_USB_UUID, out)

    def test_submenu_with_usb_reference_dropped_whole(self):
        ids = ["/dev/sdb1", _USB_UUID]
        out = filter_grub_cfg(_SAMPLE_CFG, ids)
        self.assertNotIn("Wheatley Linux Installer (live)", out)
        # section markers survive (only whole menu blocks are removed)
        self.assertIn("### BEGIN /etc/grub.d/30_os-prober ###", out)
        self.assertIn("### END /etc/grub.d/30_os-prober ###", out)

    def test_no_identifiers_returns_text_unchanged(self):
        self.assertEqual(filter_grub_cfg(_SAMPLE_CFG, []), _SAMPLE_CFG)
        self.assertEqual(filter_grub_cfg(_SAMPLE_CFG, ["", "ab"]), _SAMPLE_CFG)

    def test_clean_cfg_passes_through_byte_identical(self):
        out = filter_grub_cfg(_SAMPLE_CFG, ["zzzz-not-present-anywhere"])
        self.assertEqual(out, _SAMPLE_CFG)


class TestMainBestEffort(unittest.TestCase):

    def test_missing_file_still_exits_zero(self):
        self.assertEqual(main(["/nonexistent/dir/grub.cfg"]), 0)


if __name__ == "__main__":
    unittest.main()
