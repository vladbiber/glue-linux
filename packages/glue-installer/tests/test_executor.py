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



# ---------------------------------------------------------------------------
# config_steps extraction (3.6): parity with the pre-refactor compile_steps
# ---------------------------------------------------------------------------

from glue_installer.executor import config_steps  # noqa: E402

_PARITY_PLAN = InstallPlan(
    packages=["pkg-a", "pkg-b"],
    services=["NetworkManager", "bluetoothd", "openntpd"],
    files=[PlannedFile("/etc/a.conf", "a\n", 0o644),
           PlannedFile("/etc/skel/.bashrc", "b\n", 0o600)],
    warnings=[],
)
_LINKS = {
    "dinit": ("/etc/dinit.d", "/etc/dinit.d/boot.d",
              {"NetworkManager": "NetworkManager", "bluetoothd": "bluetoothd",
               "openntpd": "ntpd"}),
    "runit": ("/etc/runit/sv", "/etc/runit/runsvdir/default",
              {"NetworkManager": "NetworkManager", "bluetoothd": "bluetoothd",
               "openntpd": "openntpd"}),
    "openrc": ("/etc/init.d", "/etc/runlevels/default",
               {"NetworkManager": "NetworkManager", "bluetoothd": "bluetooth",
                "openntpd": "ntpd"}),
}
_MM_SCRIPT = ('f=/usr/share/dbus-1/system-services/org.freedesktop.ModemManager1.service; '
              '[ -e "$f" ] && mv -f "$f" "$f.glue-disabled"; true')


def _expected_pre_refactor(init_id):
    """The exact step list compile_steps produced before config_steps existed."""
    src, dst, names = _LINKS[init_id]
    steps = [
        RunCommand(argv=["basestrap", "/mnt", "pkg-a", "pkg-b"],
                   description="Install 2 packages with basestrap"),
        RunCommand(argv=["sh", "-c", "fstabgen -U /mnt >> /mnt/etc/fstab"],
                   description="Generate fstab → /mnt/etc/fstab"),
        WriteTargetFile(path="/mnt/etc/a.conf", content="a\n", mode=0o644,
                        description="Write /etc/a.conf"),
        WriteTargetFile(path="/mnt/etc/skel/.bashrc", content="b\n", mode=0o600,
                        description="Write /etc/skel/.bashrc"),
    ]
    for svc in ("NetworkManager", "bluetoothd", "openntpd"):
        isvc = names[svc]
        steps.append(RunCommand(
            argv=["artix-chroot", "/mnt", "ln", "-sf", f"{src}/{isvc}", f"{dst}/{isvc}"],
            description=f"Enable service {svc} ({init_id})"))
    steps.append(RunCommand(argv=["artix-chroot", "/mnt", "sh", "-c", _MM_SCRIPT],
                            description="Disable ModemManager D-Bus activation (console spam)"))
    return steps


class TestConfigStepsParity(unittest.TestCase):
    def test_compile_steps_unchanged_per_init(self):
        for init_id in ("dinit", "runit", "openrc"):
            self.assertEqual(compile_steps(_PARITY_PLAN, target="/mnt", init_id=init_id),
                             _expected_pre_refactor(init_id), init_id)

    def test_config_steps_is_the_tail_of_compile_steps(self):
        for init_id in ("dinit", "runit", "openrc"):
            cfg = config_steps(_PARITY_PLAN, target="/mnt", init_id=init_id)
            self.assertEqual(cfg, _expected_pre_refactor(init_id)[2:], init_id)
            self.assertEqual(compile_steps(_PARITY_PLAN, init_id=init_id)[2:], cfg)

    def test_only_if_present_guards_each_enable(self):
        for init_id, (src, dst, names) in _LINKS.items():
            cfg = config_steps(_PARITY_PLAN, init_id=init_id, only_if_present=True)
            enables = [s for s in cfg if "Enable service" in s.description]
            self.assertEqual(len(enables), 3)
            for s, svc in zip(enables, ("NetworkManager", "bluetoothd", "openntpd")):
                isvc = names[svc]
                self.assertEqual(s.argv, [
                    "artix-chroot", "/mnt", "sh", "-c",
                    f"[ -e {src}/{isvc} ] && ln -sf {src}/{isvc} {dst}/{isvc} || true"])
                self.assertIn("if present", s.description)
            # files and the ModemManager guard are identical either way
            self.assertEqual([s for s in cfg if "Enable service" not in s.description],
                             [s for s in config_steps(_PARITY_PLAN, init_id=init_id)
                              if "Enable service" not in s.description])

    def test_unknown_init_raises(self):
        with self.assertRaises(ExecutorError):
            config_steps(_PARITY_PLAN, init_id="systemd")
        with self.assertRaises(ExecutorError):
            config_steps(_PARITY_PLAN, init_id="s6", only_if_present=True)

    def test_single_definition(self):
        src = (_PKG_ROOT / "glue_installer" / "executor.py").read_text()
        self.assertEqual(src.count("def config_steps"), 1)
