import os
import stat
import tempfile
import unittest

from glue_installer.executor import (
    PACKAGE_SHARE, RunCommand, WriteTargetFile, execute, step_weights)
from glue_installer.pkgprogress import PacmanProgress

# real output of `pacman -r ROOT -Sy ...` without a terminal (build image)
SAMPLE = """\
:: Synchronizing package databases...
 system downloading...
 world downloading...
resolving dependencies...
looking for conflicting packages...

Package (4)               New Version                 Net Change  Download Size

system/glibc              2.44+r50+g1848099f063e-1     50.82 MiB
system/iana-etc           20260617-1                    4.02 MiB       0.39 MiB
system/bash               5.3.20-1                      9.60 MiB       1.91 MiB
system/filesystem         2026.09.10-1                  0.05 MiB

Total Download Size:    2.30 MiB
Total Installed Size:  64.49 MiB

:: Proceed with installation? [Y/n]
:: Retrieving packages...
 bash-5.3.20-1-x86_64 downloading...
 iana-etc-20260617-1-any downloading...
checking keyring...
checking package integrity...
loading package files...
checking for file conflicts...
:: Processing package changes...
installing iana-etc...
installing filesystem...
installing glibc...
Optional dependencies for glibc
    gd: for memusagestat
installing bash...
:: Running post-transaction hooks...
(1/2) Generating the configured locale...
(2/2) Configuring dynamic linker run-time bindings...
"""


def _feed(text):
    tracker = PacmanProgress()
    moves = [m for m in (tracker.feed(line) for line in text.splitlines(True)) if m]
    return tracker, moves


class PacmanProgressTest(unittest.TestCase):
    def test_reads_the_transaction_table(self):
        tracker, _ = _feed(SAMPLE)
        self.assertEqual(tracker.total, 4)
        self.assertEqual(tracker.to_download, 2)
        self.assertEqual(tracker.downloaded, 2)
        self.assertEqual(tracker.installed, 4)

    def test_bar_only_moves_forward_and_ends_full(self):
        _, moves = _feed(SAMPLE)
        values = [v for v, _ in moves]
        self.assertEqual(values, sorted(values))
        self.assertAlmostEqual(values[-1], 1.0)

    def test_downloads_fill_the_first_part(self):
        _, moves = _feed(SAMPLE)
        texts = dict((t, v) for v, t in moves)
        self.assertAlmostEqual(texts["Downloading iana-etc (2/2)"], 0.45)
        self.assertIn("Installing bash (4/4)", texts)
        self.assertAlmostEqual(texts["Installing bash (4/4)"], 0.95)

    def test_database_refresh_does_not_count_as_a_package(self):
        tracker, _ = _feed(" system downloading...\n world downloading...\n")
        self.assertEqual(tracker.downloaded, 0)

    def test_unknown_output_is_ignored(self):
        _, moves = _feed("==> Creating install root at /mnt\nsomething else\n")
        self.assertEqual(moves, [])

    def test_hooks_before_install_are_not_mistaken(self):
        tracker = PacmanProgress()
        self.assertIsNone(tracker.feed("(1/3) checking keys in keyring\n"))


class WeightedExecuteTest(unittest.TestCase):
    def test_package_step_takes_its_share(self):
        steps = [RunCommand(["true"], "a"), RunCommand(["basestrap", "/mnt", "x"], "b"),
                 WriteTargetFile("/x", "", 0o644, "c"), RunCommand(["true"], "d")]
        w = step_weights(steps)
        self.assertAlmostEqual(w[1] / sum(w), PACKAGE_SHARE)
        self.assertEqual(w[2], 0.1)

    def test_basestrap_output_moves_the_bar(self):
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "basestrap")
            with open(os.path.join(d, "out.txt"), "w") as fh:
                fh.write(SAMPLE)
            with open(fake, "w") as fh:
                fh.write(f"#!/bin/sh\ncat {d}/out.txt\n")
            os.chmod(fake, stat.S_IRWXU)
            seen = []
            log = os.path.join(d, "log")
            execute([RunCommand(["true"], "first"), RunCommand([fake], "Install")],
                    output_path=log, fraction=lambda v, t: seen.append((v, t)))
            values = [v for v, _ in seen]
            self.assertEqual(values, sorted(values))
            self.assertEqual(seen[-1], (1.0, "Done"))
            self.assertTrue(any(t.startswith("Installing glibc") for _, t in seen))
            # half-way through the installs the bar is well past the start
            mid = next(v for v, t in seen if t.startswith("Installing glibc"))
            self.assertGreater(mid, 0.6)
            with open(log) as fh:
                self.assertIn("installing bash...", fh.read())

    def test_failure_still_raises(self):
        from glue_installer.executor import ExecutorError
        with self.assertRaises(ExecutorError):
            execute([RunCommand(["basestrap-does-not-exist"], "x")],
                    fraction=lambda v, t: None)
        with self.assertRaises(ExecutorError):
            execute([RunCommand(["sh", "-c", "echo hi; exit 3"], "y")],
                    fraction=lambda v, t: None)


if __name__ == "__main__":
    unittest.main()
