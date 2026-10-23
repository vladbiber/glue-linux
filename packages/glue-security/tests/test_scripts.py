"""glue-snapshot and glue-antivirus on temp folders (no root, no ClamAV)."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.home, self.snaps = t / "home", t / "snaps"
        (self.home / "ana/Documents").mkdir(parents=True)
        (self.home / "ana/Downloads").mkdir()
        (self.home / "ana/Documents/a.txt").write_text("v1")
        (self.home / "ana/Downloads/big.iso").write_text("x")
        self.env = dict(os.environ, GLUE_SNAPSHOT_DIR=str(self.snaps),
                        GLUE_SNAPSHOT_HOME=str(self.home), GLUE_SNAPSHOT_KEEP="2")

    def run_snap(self, *args):
        return subprocess.run(["sh", str(PKG / "glue-snapshot"), *args], env=self.env,
                              capture_output=True, text=True)

    def test_copy_is_private_and_skips_downloads(self):
        self.assertEqual(self.run_snap("now").returncode, 0)
        (snap,) = list(self.snaps.iterdir())
        self.assertEqual((snap / "ana/Documents/a.txt").read_text(), "v1")
        self.assertFalse((snap / "ana/Downloads").exists())
        self.assertEqual(self.snaps.stat().st_mode & 0o777, 0o700)

    def test_due_skips_a_fresh_copy(self):
        self.run_snap("now")
        self.run_snap("due")
        self.assertEqual(len(list(self.snaps.iterdir())), 1)

    def test_rotation_and_hard_links(self):
        for i, name in enumerate(("2026-01-01_0000", "2026-01-02_0000")):
            d = self.snaps / name / "ana/Documents"
            d.mkdir(parents=True)
            (d / "a.txt").write_text("v1")
        self.run_snap("now")
        names = sorted(p.name for p in self.snaps.iterdir())
        self.assertEqual(len(names), 2)
        self.assertEqual(names[0], "2026-01-02_0000")
        self.assertEqual((self.snaps / names[1] / "ana/Documents/a.txt").stat().st_nlink, 2)


class AntivirusTest(unittest.TestCase):
    def test_virus_moved_clean_file_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            dl = t / "home/ana/Downloads"
            dl.mkdir(parents=True)
            (dl / "bad file.exe").write_text("EICAR")
            (dl / "ok.pdf").write_text("fine")
            scan = t / "scan"
            scan.write_text('#!/bin/sh\nrc=0\nfor f; do case "$f" in -*) continue;; esac\n'
                            'grep -q EICAR "$f" && { echo "$f: Eicar-Test-Signature FOUND"; rc=1; }\n'
                            'done\nexit $rc\n')
            scan.chmod(0o755)
            env = dict(os.environ, GLUE_AV_HOME=str(t / "home"),
                       GLUE_AV_QUARANTINE=str(t / "q"), GLUE_AV_SCAN=str(scan))
            r = subprocess.run(["bash", str(PKG / "glue-antivirus"), "check",
                                str(dl / "bad file.exe"), str(dl / "ok.pdf")],
                               env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertEqual(sorted(p.name for p in dl.iterdir()),
                             ["bad file.exe.blocked.txt", "ok.pdf"])
            self.assertIn("Eicar-Test-Signature", (dl / "bad file.exe.blocked.txt").read_text())
            self.assertEqual(len(list((t / "q").iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
