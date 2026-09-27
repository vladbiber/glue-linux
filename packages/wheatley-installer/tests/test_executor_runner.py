"""Unit tests for wheatley_installer.executor's runner (execute)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from wheatley_installer.plan import InstallPlan, PlannedFile
from wheatley_installer.executor import (
    ExecutorError, RunCommand, WriteTargetFile,
    compile_steps, execute,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _minimal_plan(packages=None, services=None, files=None) -> InstallPlan:
    return InstallPlan(
        packages=sorted(packages or ["pkg-a", "pkg-b"]),
        services=sorted(services or []),
        files=sorted(files or [], key=lambda f: f.path),
        warnings=[],
    )


def _plan_with_file(path: str, content: str = "hello\n", mode: int = 0o644) -> InstallPlan:
    return _minimal_plan(files=[PlannedFile(path=path, content=content, mode=mode)])


# ---------------------------------------------------------------------------
# execute: dry-run is side-effect-free
# ---------------------------------------------------------------------------

class TestDryRun(unittest.TestCase):

    def test_dry_run_calls_log_per_step(self):
        steps = compile_steps(_minimal_plan(), target="/mnt", init_id="dinit")
        logged = []
        execute(steps, dry_run=True, log=logged.append)
        self.assertEqual(len(logged), len(steps))

    def test_dry_run_log_format(self):
        plan = _minimal_plan()
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        logged = []
        execute(steps, dry_run=True, log=logged.append)
        for line in logged:
            self.assertTrue(line.startswith("DRY-RUN: "), f"Bad line: {line!r}")

    def test_dry_run_no_subprocess_called(self):
        steps = compile_steps(_minimal_plan(), target="/mnt", init_id="dinit")

        def _forbidden(*args, **kwargs):
            raise AssertionError("subprocess.run must NOT be called in dry_run mode")

        with patch("wheatley_installer.executor.subprocess.run", side_effect=_forbidden):
            execute(steps, dry_run=True, log=lambda _: None)

    def test_dry_run_no_files_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = _plan_with_file("/etc/skel/.bashrc")
            steps = compile_steps(plan, target=tmpdir, init_id="dinit")
            execute(steps, dry_run=True, log=lambda _: None)
            # No files should appear under tmpdir (beyond what was there before)
            created = list(Path(tmpdir).rglob("*"))
            self.assertEqual(created, [], f"Unexpected files: {created}")


# ---------------------------------------------------------------------------
# execute: real execution with harmless commands
# ---------------------------------------------------------------------------

class TestExecuteRealRun(unittest.TestCase):

    def test_write_target_file_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            content = "hello wheatley\n"
            step = WriteTargetFile(
                path=os.path.join(tmpdir, "etc", "test.conf"),
                content=content,
                mode=0o644,
                description="Write /etc/test.conf",
            )
            execute([step], dry_run=False, log=lambda _: None)
            written = Path(tmpdir) / "etc" / "test.conf"
            self.assertTrue(written.exists())
            self.assertEqual(written.read_text(), content)

    def test_write_target_file_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            step = WriteTargetFile(
                path=os.path.join(tmpdir, "a", "b", "c", "file.txt"),
                content="deep\n",
                mode=0o644,
                description="Write deep file",
            )
            execute([step], dry_run=False, log=lambda _: None)
            self.assertTrue(Path(tmpdir, "a", "b", "c", "file.txt").exists())

    def test_write_target_file_sets_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target_path = os.path.join(tmpdir, "exec.sh")
            step = WriteTargetFile(
                path=target_path,
                content="#!/bin/sh\n",
                mode=0o755,
                description="Write exec.sh",
            )
            execute([step], dry_run=False, log=lambda _: None)
            actual_mode = os.stat(target_path).st_mode & 0o777
            self.assertEqual(actual_mode, 0o755)

    def test_failing_command_raises_executor_error(self):
        step = RunCommand(argv=["false"], description="This always fails")
        with self.assertRaises(ExecutorError) as ctx:
            execute([step], dry_run=False, log=lambda _: None)
        self.assertIn("This always fails", str(ctx.exception))

    def test_successful_command_does_not_raise(self):
        step = RunCommand(argv=["true"], description="Always succeeds")
        execute([step], dry_run=False, log=lambda _: None)  # must not raise


# ---------------------------------------------------------------------------
# execute: path validation
# ---------------------------------------------------------------------------

class TestPathValidation(unittest.TestCase):

    def _write_step(self, path: str) -> WriteTargetFile:
        return WriteTargetFile(path=path, content="x\n", mode=0o644, description="test")

    def test_relative_path_rejected(self):
        with self.assertRaises(ExecutorError):
            execute([self._write_step("relative/path")], dry_run=False, log=lambda _: None)

    def test_dotdot_path_rejected(self):
        with self.assertRaises(ExecutorError):
            execute([self._write_step("/mnt/../etc/passwd")], dry_run=False, log=lambda _: None)

    def test_dotdot_short_path_rejected(self):
        with self.assertRaises(ExecutorError):
            execute([self._write_step("../x")], dry_run=False, log=lambda _: None)

    def test_absolute_path_without_dotdot_accepted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            step = self._write_step(os.path.join(tmpdir, "valid.txt"))
            execute([step], dry_run=False, log=lambda _: None)
            self.assertTrue(Path(tmpdir, "valid.txt").exists())


if __name__ == "__main__":
    unittest.main()


class TestMissingBinary(unittest.TestCase):
    def test_missing_binary_raises_executor_error_not_oserror(self):
        # regression: identity steps used arch-chroot (missing on Artix) and
        # the runner crashed with a raw FileNotFoundError traceback
        steps = [RunCommand(argv=["/nonexistent/definitely-missing-bin"],
                            description="run missing binary")]
        with self.assertRaises(ExecutorError) as ctx:
            execute(steps, dry_run=False)
        self.assertIn("run missing binary", str(ctx.exception))
