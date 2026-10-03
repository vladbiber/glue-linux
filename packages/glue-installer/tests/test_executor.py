"""Unit tests for glue_installer.executor's step compiler (compile_steps)."""

import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.catalog import (
    Catalog, Gaming, Init, Kernel, Keybinding, Minimal,
    Session, Shell, SupportToggle, load_catalog,
)
from glue_installer.plan import (
    InstallPlan, PlannedFile, Selection, resolve_plan,
)
from glue_installer.executor import (
    ExecutorError, RunCommand, WriteTargetFile, Step,
    compile_steps, keyring_steps,
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
            elif isinstance(s, RunCommand) and "ModemManager" in " ".join(s.argv):
                kinds.append("modemmanager")
            elif isinstance(s, RunCommand):
                kinds.append("service")
        self.assertEqual(
            kinds, ["basestrap", "fstab", "file", "service", "modemmanager"]
        )


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
            compile_steps(plan, target="/mnt", init_id="systemd")
        self.assertIn("systemd", str(ctx.exception))

    def test_openrc_enable_uses_runlevels_symlink(self):
        plan = _plan_with_service("greetd")
        steps = compile_steps(plan, target="/mnt", init_id="openrc")
        svc = [s for s in steps if isinstance(s, RunCommand) and "greetd" in " ".join(s.argv)][0]
        self.assertIn("/etc/init.d/greetd", svc.argv)
        self.assertIn("/etc/runlevels/default/greetd", svc.argv)

    def test_openrc_bluetooth_service_is_renamed(self):
        # Artix bluez-openrc ships /etc/init.d/bluetooth (not bluetoothd)
        plan = _plan_with_service("bluetoothd")
        steps = compile_steps(plan, target="/mnt", init_id="openrc")
        svc = [s for s in steps if isinstance(s, RunCommand) and "bluetooth" in " ".join(s.argv)][0]
        self.assertIn("/etc/init.d/bluetooth", svc.argv)
        self.assertIn("/etc/runlevels/default/bluetooth", svc.argv)

    def test_openntpd_service_renamed_per_init(self):
        # Artix ships openntpd's scripts as /etc/dinit.d/ntpd and
        # /etc/init.d/ntpd, but /etc/runit/sv/openntpd — the canonical
        # plan name "openntpd" must map onto the real script per init.
        plan = _plan_with_service("openntpd")
        for init_id, path in (
            ("dinit", "/etc/dinit.d/ntpd"),
            ("openrc", "/etc/init.d/ntpd"),
            ("runit", "/etc/runit/sv/openntpd"),
        ):
            steps = compile_steps(plan, target="/mnt", init_id=init_id)
            svc = [st for st in steps if isinstance(st, RunCommand)
                   and "ntpd" in " ".join(st.argv)][0]
            self.assertIn(path, svc.argv, init_id)

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
        # Only basestrap + fstab + ModemManager-disable steps expected
        run_cmds = [s for s in steps if isinstance(s, RunCommand)]
        self.assertEqual(len(run_cmds), 3)

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
# compile_steps: pacman_conf parameter
# ---------------------------------------------------------------------------

class TestCompilePacmanConf(unittest.TestCase):

    def test_pacman_conf_inserts_dash_C_after_basestrap(self):
        plan = _minimal_plan()
        steps = compile_steps(plan, target="/mnt", init_id="dinit",
                              pacman_conf="/usr/share/glue/pacman.conf")
        cmd = steps[0]
        self.assertIsInstance(cmd, RunCommand)
        self.assertEqual(cmd.argv[0], "basestrap")
        self.assertIn("-C", cmd.argv)
        idx = cmd.argv.index("-C")
        self.assertEqual(cmd.argv[idx + 1], "/usr/share/glue/pacman.conf")

    def test_pacman_conf_dash_C_immediately_after_basestrap(self):
        plan = _minimal_plan()
        steps = compile_steps(plan, target="/mnt", init_id="dinit",
                              pacman_conf="/custom/pacman.conf")
        cmd = steps[0]
        self.assertEqual(cmd.argv[1], "-C")
        self.assertEqual(cmd.argv[2], "/custom/pacman.conf")

    def test_no_pacman_conf_produces_no_dash_C(self):
        plan = _minimal_plan()
        steps = compile_steps(plan, target="/mnt", init_id="dinit")
        cmd = steps[0]
        self.assertNotIn("-C", cmd.argv)

    def test_pacman_conf_none_explicit_produces_no_dash_C(self):
        plan = _minimal_plan()
        steps = compile_steps(plan, target="/mnt", init_id="dinit", pacman_conf=None)
        cmd = steps[0]
        self.assertNotIn("-C", cmd.argv)

    def test_target_still_correct_position_with_pacman_conf(self):
        # argv must be: basestrap -C <conf> <target> [packages...]
        plan = _minimal_plan()
        steps = compile_steps(plan, target="/mnt", init_id="dinit",
                              pacman_conf="/usr/share/glue/pacman.conf")
        cmd = steps[0]
        self.assertEqual(cmd.argv[3], "/mnt")

    def test_packages_still_present_with_pacman_conf(self):
        packages = ["pkg-x", "pkg-y"]
        plan = _minimal_plan(packages=packages)
        steps = compile_steps(plan, target="/mnt", init_id="dinit",
                              pacman_conf="/usr/share/glue/pacman.conf")
        cmd = steps[0]
        for pkg in packages:
            self.assertIn(pkg, cmd.argv)

    def test_step_count_unchanged_with_pacman_conf(self):
        plan = _minimal_plan(services=["svc1"])
        steps_without = compile_steps(plan, target="/mnt", init_id="dinit")
        steps_with = compile_steps(plan, target="/mnt", init_id="dinit",
                                   pacman_conf="/usr/share/glue/pacman.conf")
        self.assertEqual(len(steps_without), len(steps_with))


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
            session_ids=["nvwm"], shell_choice={},
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


# ---------------------------------------------------------------------------
# keyring_steps: host keyring prep (regression: cachyos key unknown at basestrap)
# ---------------------------------------------------------------------------

class TestKeyringSteps(unittest.TestCase):
    def test_two_run_command_steps(self):
        steps = keyring_steps()
        self.assertEqual(len(steps), 2)
        for s in steps:
            self.assertIsInstance(s, RunCommand)

    def test_init_before_populate(self):
        steps = keyring_steps()
        self.assertEqual(steps[0].argv, ["pacman-key", "--init"])
        self.assertEqual(
            steps[1].argv, ["pacman-key", "--populate", "artix", "cachyos"]
        )

    def test_pure_and_deterministic(self):
        self.assertEqual(keyring_steps(), keyring_steps())


if __name__ == "__main__":
    unittest.main()

