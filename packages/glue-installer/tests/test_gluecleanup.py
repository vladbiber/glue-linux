"""Leftovers of a failed install that block partitioning on the next try."""

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
_JOB = Path(__file__).parent.parent.parent / "glue-calamares-config" / "modules" / "gluecleanup"

from glue_installer.stale_mounts import disk_swaps, leftover_roots, parse_mountinfo  # noqa: E402

OLD = "/tmp/calamares-root-a1b2c3"
# keyring step failed: partition + mount ran, umount never did
MOUNTS = f"""\
22 1 0:21 / / rw - overlay overlay rw
90 22 8:2 / {OLD} rw - ext4 /dev/sda2 rw
91 90 8:1 / {OLD}/boot rw - vfat /dev/sda1 rw
92 90 0:5 / {OLD}/dev rw - devtmpfs dev rw
100 22 0:40 / /tmp/calamares-root-zz/x rw - tmpfs t rw
"""
SWAPS = """\
Filename				Type		Size		Used		Priority
/dev/zram0                              partition	8000000		0		100
/dev/sda3                               partition	4000000		0		-2
"""


class PureTest(unittest.TestCase):
    def test_leftover_roots_are_the_target_tops(self):
        self.assertEqual(leftover_roots(parse_mountinfo(MOUNTS)), [OLD])

    def test_clean_live_system_has_none(self):
        self.assertEqual(leftover_roots(parse_mountinfo(MOUNTS.splitlines()[0])), [])

    def test_disk_swap_only(self):
        self.assertEqual(disk_swaps(SWAPS), ["/dev/sda3"])
        self.assertEqual(disk_swaps(SWAPS.splitlines()[0]), [])


class JobTest(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("libcalamares", None)

    def run_job(self, rc=0):
        spec = importlib.util.spec_from_file_location("gluecleanup_main", _JOB / "main.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cala = types.ModuleType("libcalamares")
        cala.job = types.SimpleNamespace(setprogress=lambda v: None)
        cala.log = []
        cala.utils = types.SimpleNamespace(debug=cala.log.append, warning=cala.log.append)
        sys.modules["libcalamares"] = cala
        called = []

        def cmd(argv):
            called.append(argv)
            return types.SimpleNamespace(returncode=rc, stderr="busy\n")
        with tempfile.NamedTemporaryFile("w") as mi, tempfile.NamedTemporaryFile("w") as sw:
            mi.write(MOUNTS)
            sw.write(SWAPS)
            mi.flush()
            sw.flush()
            self.assertIsNone(mod.run(mountinfo=mi.name, proc_swaps=sw.name, cmd=cmd))
        return called, cala.log

    def test_unmounts_and_swapoffs(self):
        called, log = self.run_job()
        self.assertEqual(called, [["umount", "-R", "-l", OLD], ["swapoff", "/dev/sda3"]])
        self.assertIn(f"gluecleanup: unmounted leftover {OLD}", log)

    def test_failure_is_only_logged(self):
        _called, log = self.run_job(rc=32)
        self.assertIn(f"gluecleanup: {OLD}: busy", log)

    def test_module_desc(self):
        self.assertIn('name: "gluecleanup"', (_JOB / "module.desc").read_text())


if __name__ == "__main__":
    unittest.main()
