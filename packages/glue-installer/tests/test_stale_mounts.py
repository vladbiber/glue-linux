"""Leftover artix-chroot mounts under the Calamares target."""

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
_JOB = Path(__file__).parent.parent.parent / "glue-calamares-config" / "modules" / "gluemounts"

from glue_installer.stale_mounts import parse_mountinfo, stacked_mounts  # noqa: E402

T = "/tmp/calamares-root-5mxk727g"
# what the VM showed after a busy /sys: Calamares' sys + efivarfs, then a
# read-only sys from artix-chroot stacked over them
VM = f"""\
22 1 0:21 / / rw - overlay overlay rw
90 22 254:0 / {T} rw,noatime - ext4 /dev/mapper/luks-x rw
91 90 254:1 / {T}/boot rw - vfat /dev/vda1 rw
92 90 0:5 / {T}/dev rw - devtmpfs dev rw
93 90 0:23 / {T}/proc rw - proc proc rw
94 90 0:24 / {T}/sys rw - sysfs sys rw
95 94 0:25 / {T}/sys/firmware/efi/efivars rw - efivarfs efivarfs rw
130 94 0:24 / {T}/sys ro,nosuid - sysfs sys ro
"""


class StaleMountsTest(unittest.TestCase):
    def test_leftover_read_only_sys_is_found(self):
        self.assertEqual(stacked_mounts(parse_mountinfo(VM), T), [f"{T}/sys"])

    def test_clean_target_has_nothing(self):
        clean = "\n".join(VM.splitlines()[:-1])
        self.assertEqual(stacked_mounts(parse_mountinfo(clean), T + "/"), [])

    def test_only_the_lowest_copy_of_a_chain(self):
        chain = VM + f"131 130 0:26 / {T}/sys/firmware/efi/efivars rw - efivarfs e rw\n" \
                     f"140 130 0:24 / {T}/sys ro - sysfs sys ro\n"
        self.assertEqual(stacked_mounts(parse_mountinfo(chain), T), [f"{T}/sys"])

    def test_several_paths_newest_first(self):
        more = VM + f"150 93 0:23 / {T}/proc rw - proc proc rw\n"
        self.assertEqual(stacked_mounts(parse_mountinfo(more), T),
                         [f"{T}/proc", f"{T}/sys"])

    def test_mounts_outside_the_target_are_ignored(self):
        other = VM + "160 94 0:24 / /sys rw - sysfs sys rw\n161 160 0:24 / /sys rw - sysfs s rw\n"
        self.assertEqual(stacked_mounts(parse_mountinfo(other), T), [f"{T}/sys"])

    def test_escaped_paths(self):
        text = "1 0 0:1 / /mnt/a\\040b rw - x x rw\n2 1 0:2 / /mnt/a\\040b/sys rw - s s rw\n" \
               "3 2 0:2 / /mnt/a\\040b/sys ro - s s ro\n"
        self.assertEqual(stacked_mounts(parse_mountinfo(text), "/mnt/a b"), ["/mnt/a b/sys"])


class GluemountsJobTest(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("libcalamares", None)

    def run_job(self, gs, mountinfo_text, rc=0):
        spec = importlib.util.spec_from_file_location("gluemounts_main", _JOB / "main.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cala = types.ModuleType("libcalamares")
        cala.globalstorage = types.SimpleNamespace(value=gs.get)
        cala.job = types.SimpleNamespace(setprogress=lambda v: None)
        cala.log = []
        cala.utils = types.SimpleNamespace(debug=cala.log.append, warning=cala.log.append)
        sys.modules["libcalamares"] = cala
        called = []

        def umount(path):
            called.append(path)
            return types.SimpleNamespace(returncode=rc, stderr="target is busy\n")
        with tempfile.NamedTemporaryFile("w", suffix=".mountinfo") as fh:
            fh.write(mountinfo_text)
            fh.flush()
            self.assertIsNone(mod.run(mountinfo=fh.name, umount=umount))
        return called, cala.log

    def test_unmounts_the_leftover_sys(self):
        called, log = self.run_job({"rootMountPoint": T}, VM)
        self.assertEqual(called, [f"{T}/sys"])
        self.assertIn(f"gluemounts: unmounted leftover {T}/sys", log)

    def test_failure_is_only_logged(self):
        called, log = self.run_job({"rootMountPoint": T}, VM, rc=32)
        self.assertEqual(called, [f"{T}/sys"])
        self.assertIn(f"gluemounts: {T}/sys: target is busy", log)

    def test_without_root_nothing_runs(self):
        self.assertEqual(self.run_job({}, VM)[0], [])

    def test_module_desc(self):
        self.assertIn('name: "gluemounts"', (_JOB / "module.desc").read_text())


if __name__ == "__main__":
    unittest.main()
