"""Unit tests for wheatley_installer.executor."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from wheatley_installer.catalog import (
    Catalog, Gaming, Init, Kernel, Keybinding, Minimal,
    Session, Shell, SupportToggle, load_catalog,
)
from wheatley_installer.plan import (
    InstallPlan, PlannedFile, Selection, resolve_plan,
)
from wheatley_installer.executor import (
    ExecutorError, RunCommand, WriteTargetFile, Step,
    compile_steps, execute,
)

_CATALOG_PATH = _PKG_ROOT / "catalog" / "catalog.json"

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


def _plan_with_service(svc: str) -> InstallPlan:
    return _minimal_plan(services=[svc])


def _plan_with_file(path: str, content: str = "hello\n", mode: int = 0o644) -> InstallPlan:
    return _minimal_plan(files=[PlannedFile(path=path, content=content, mode=mode)])


# ---------------------------------------------------------------------------
# compile_steps: golden ordering
# ---------------------------------------------------------------------------

class TestCompileOrdering(unittest.TestCase):

    def test_basestrap_is_first_step(self):
        plan = _minimal_plan()
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        self.assertIsInstance(steps[0], RunCommand)
        self.assertIn("basestrap", steps[0].argv[0])

    def test_fstab_is_second_step(self):
        plan = _minimal_plan()
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        self.assertIsInstance(steps[1], RunCommand)
        self.assertIn("fstabgen", steps[1].argv[-1])

    def test_files_come_before_services(self):
        plan = _minimal_plan(
            services=["myservice"],
            files=[PlannedFile("/etc/skel/.bashrc", "# hi\n", 0o644)],
        )
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        # basestrap=0, fstab=1, file=2, service=3
        file_idx = next(i for i, s in enumerate(steps) if isinstance(s, WriteTargetFile))
        svc_idx = next(
            i for i, s in enumerate(steps)
            if isinstance(s, RunCommand) and "myservice" in " ".join(s.argv)
        )
        self.assertLess(file_idx, svc_idx)

    def test_files_sorted_by_path(self):
        plan = _minimal_plan(files=[
            PlannedFile("/etc/skel/.zshrc", "zsh\n", 0o644),
            PlannedFile("/etc/skel/.bashrc", "bash\n", 0o644),
        ])
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        file_steps = [s for s in steps if isinstance(s, WriteTargetFile)]
        paths = [s.path for s in file_steps]
        self.assertEqual(paths, sorted(paths))

    def test_golden_order_basestrap_fstab_files_services(self):
        plan = _minimal_plan(
            services=["svc1"],
            files=[PlannedFile("/etc/skel/.bashrc", "# rc\n", 0o644)],
        )
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        kinds = []
        for s in steps:
            if isinstance(s, RunCommand) and "basestrap" in s.argv[0]:
                kinds.append("basestrap")
            elif isinstance(s, RunCommand) and "fstabgen" in s.argv[-1]:
                kinds.append("fstab")
            elif isinstance(s, WriteTargetFile):
                kinds.append("file")
            elif isinstance(s, RunCommand):
                kinds.append("service")
        self.assertEqual(kinds, ["basestrap", "fstab", "file", "service"])


# ---------------------------------------------------------------------------
# compile_steps: determinism
# ---------------------------------------------------------------------------

class TestCompileDeterminism(unittest.TestCase):

    def test_two_compiles_are_identical(self):
        plan = _minimal_plan(
            packages=["pkg-z", "pkg-a", "pkg-m"],
            services=["svc-b", "svc-a"],
            files=[
                PlannedFile("/etc/skel/.zshrc", "zsh\n", 0o644),
                PlannedFile("/etc/skel/.bashrc", "bash\n", 0o644),
            ],
        )
        steps1 = compile_steps(plan, target="/mnt", init_id="dinit")
        steps2 = compile_steps(plan, target="/mnt", init_id="dinit")
        self.assertEqual(steps1, steps2)


# ---------------------------------------------------------------------------
# compile_steps: basestrap argv
# ---------------------------------------------------------------------------

class TestCompileBasestrap(unittest.TestCase):

    def test_basestrap_argv_contains_all_packages(self):
        packages = ["linux-cachyos", "dinit", "fastfetch"]
        plan = _minimal_plan(packages=packages)
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        cmd = steps[0]
        self.assertIsInstance(cmd, RunCommand)
        self.assertEqual(cmd.argv[0], "basestrap")
        self.assertEqual(cmd.argv[1], "/mnt")
        for pkg in packages:
            self.assertIn(pkg, cmd.argv)

    def test_basestrap_argv_uses_target(self):
        plan = _minimal_plan()
        steps = compile_steps(plan, target="/target", init_id="dinit")
        self.assertEqual(steps[0].argv[1], "/target")

    def test_basestrap_description_shows_count(self):
        plan = _minimal_plan(packages=["a", "b", "c"])
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        self.assertIn("3", steps[0].description)
        self.assertIn("basestrap", steps[0].description)

    def test_basestrap_singular_package(self):
        plan = _minimal_plan(packages=["only-one"])
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        desc = steps[0].description
        self.assertIn("1 package", desc)
        self.assertNotIn("packages", desc)

    def test_basestrap_target_trailing_slash_stripped(self):
        plan = _minimal_plan()
        steps = compile_steps(plan, target="/mnt/", init_id="dinit")
        self.assertEqual(steps[0].argv[1], "/mnt")


# ---------------------------------------------------------------------------
# compile_steps: fstab
# ---------------------------------------------------------------------------

class TestCompileFstab(unittest.TestCase):

    def test_fstab_uses_sh_c(self):
        plan = _minimal_plan()
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        cmd = steps[1]
        self.assertEqual(cmd.argv[:2], ["sh", "-c"])

    def test_fstab_shell_command_contains_fstabgen(self):
        plan = _minimal_plan()
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        shell_cmd = steps[1].argv[2]
        self.assertIn("fstabgen", shell_cmd)
        self.assertIn("/mnt", shell_cmd)
        self.assertIn("/mnt/etc/fstab", shell_cmd)


# ---------------------------------------------------------------------------
# compile_steps: dinit vs runit service enabling
# ---------------------------------------------------------------------------

class TestCompileInitServices(unittest.TestCase):

    def test_dinit_service_step_uses_symlink(self):
        plan = _plan_with_service("bluetoothd")
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        svc_steps = [s for s in steps if isinstance(s, RunCommand) and "bluetoothd" in " ".join(s.argv)]
        self.assertEqual(len(svc_steps), 1)
        argv = svc_steps[0].argv
        self.assertEqual(argv[0], "artix-chroot")
        self.assertIn("ln", argv)
        self.assertIn("-sf", argv)
        self.assertIn("/etc/dinit.d/bluetoothd", argv)
        self.assertIn("/etc/dinit.d/boot.d/bluetoothd", argv)

    def test_runit_service_step_uses_symlink(self):
        plan = _plan_with_service("bluetoothd")
        steps = compile_steps(plan, target="/mnt", init_id="runit")
        svc_steps = [s for s in steps if isinstance(s, RunCommand) and "bluetoothd" in " ".join(s.argv)]
        self.assertEqual(len(svc_steps), 1)
        argv = svc_steps[0].argv
        self.assertEqual(argv[0], "artix-chroot")
        self.assertIn("ln", argv)
        self.assertIn("-sf", argv)
        self.assertIn("/etc/runit/sv/bluetoothd", argv)
        self.assertIn("/etc/runit/runsvdir/default/bluetoothd", argv)

    def test_unsupported_init_raises(self):
        plan = _plan_with_service("svc")
        with self.assertRaises(ExecutorError) as ctx:
            compile_steps(plan, target="/mnt", init_id="openrc")
        self.assertIn("openrc", str(ctx.exception))

    def test_dinit_description_names_init(self):
        plan = _plan_with_service("greetd")
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        svc = [s for s in steps if isinstance(s, RunCommand) and "greetd" in " ".join(s.argv)][0]
        self.assertIn("dinit", svc.description)
        self.assertIn("greetd", svc.description)

    def test_runit_description_names_init(self):
        plan = _plan_with_service("greetd")
        steps = compile_steps(plan, target="/mnt", init_id="runit")
        svc = [s for s in steps if isinstance(s, RunCommand) and "greetd" in " ".join(s.argv)][0]
        self.assertIn("runit", svc.description)

    def test_empty_services_no_service_steps(self):
        plan = _minimal_plan(services=[])
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        # Only basestrap + fstab steps expected
        run_cmds = [s for s in steps if isinstance(s, RunCommand)]
        self.assertEqual(len(run_cmds), 2)

    def test_empty_files_no_file_steps(self):
        plan = _minimal_plan(files=[])
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        file_steps = [s for s in steps if isinstance(s, WriteTargetFile)]
        self.assertEqual(len(file_steps), 0)


# ---------------------------------------------------------------------------
# compile_steps: file content and mode
# ---------------------------------------------------------------------------

class TestCompileFileSteps(unittest.TestCase):

    def test_file_content_preserved(self):
        content = "# some config\nexport PATH=/usr/bin\n"
        plan = _plan_with_file("/etc/skel/.bashrc", content=content)
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        file_step = next(s for s in steps if isinstance(s, WriteTargetFile))
        self.assertEqual(file_step.content, content)

    def test_file_mode_preserved(self):
        plan = _plan_with_file("/etc/skel/.bashrc", mode=0o755)
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        file_step = next(s for s in steps if isinstance(s, WriteTargetFile))
        self.assertEqual(file_step.mode, 0o755)

    def test_file_path_joined_under_target(self):
        plan = _plan_with_file("/etc/skel/.bashrc")
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        file_step = next(s for s in steps if isinstance(s, WriteTargetFile))
        self.assertEqual(file_step.path, "/mnt/etc/skel/.bashrc")

    def test_file_description_shows_target_relative_path(self):
        plan = _plan_with_file("/etc/skel/.bashrc")
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        file_step = next(s for s in steps if isinstance(s, WriteTargetFile))
        self.assertIn("/etc/skel/.bashrc", file_step.description)


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


# ---------------------------------------------------------------------------
# Integration: real catalog + resolve_plan + compile_steps
# ---------------------------------------------------------------------------

class TestIntegration(unittest.TestCase):

    def setUp(self):
        if not _CATALOG_PATH.exists():
            self.skipTest("catalog/catalog.json not found")
        self._catalog = load_catalog(_CATALOG_PATH)

    def _sel(self, init_id):
        return Selection(
            kernel_id="linux-cachyos", init_id=init_id,
            session_ids=["apeturewm"], shell_choice={},
            support_ids=["bluetooth"], gaming=False, minimal=False,
        )

    def test_bluetooth_service_step_present_with_dinit(self):
        plan = resolve_plan(self._catalog, self._sel("dinit"))
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        bt_steps = [s for s in steps if isinstance(s, RunCommand) and "bluetoothd" in " ".join(s.argv)]
        self.assertGreater(len(bt_steps), 0, "No bluetoothd enable step found")
        argv = bt_steps[0].argv
        self.assertIn("/etc/dinit.d/bluetoothd", argv)
        self.assertIn("/etc/dinit.d/boot.d/bluetoothd", argv)

    def test_bashrc_write_step_present(self):
        plan = resolve_plan(self._catalog, self._sel("dinit"))
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        bashrc_steps = [s for s in steps if isinstance(s, WriteTargetFile) and s.path.endswith("/etc/skel/.bashrc")]
        self.assertEqual(len(bashrc_steps), 1)
        self.assertIn("fastfetch", bashrc_steps[0].content)

    def test_integration_compile_deterministic(self):
        plan = resolve_plan(self._catalog, self._sel("runit"))
        steps1 = compile_steps(plan, target="/mnt", init_id="runit")
        steps2 = compile_steps(plan, target="/mnt", init_id="runit")
        self.assertEqual(steps1, steps2)

    def test_runit_bluetooth_service_step(self):
        plan = resolve_plan(self._catalog, self._sel("runit"))
        steps = compile_steps(plan, target="/mnt", init_id="runit")
        bt_steps = [s for s in steps if isinstance(s, RunCommand) and "bluetoothd" in " ".join(s.argv)]
        self.assertGreater(len(bt_steps), 0)
        argv = bt_steps[0].argv
        self.assertIn("/etc/runit/sv/bluetoothd", argv)
        self.assertIn("/etc/runit/runsvdir/default/bluetoothd", argv)


if __name__ == "__main__":
    unittest.main()
