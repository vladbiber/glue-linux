"""
Unit tests for glue_installer.plan.

Run with:
  cd packages/glue-installer
  python -m unittest discover -s tests -v
"""

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
    InstallPlan, PlanError, PlannedFile, Selection, resolve_plan,
)

_CATALOG_PATH = _PKG_ROOT / "catalog" / "catalog.json"

# ---------------------------------------------------------------------------
# Test fixture helpers
# ---------------------------------------------------------------------------

_KBS = [
    Keybinding("Super+Return", "Open terminal"),
    Keybinding("Super+q", "Close window"),
    Keybinding("Super+h", "Focus left"),
    Keybinding("Super+j", "Focus down"),
    Keybinding("Super+k", "Focus up"),
]


def _make_catalog() -> Catalog:
    """Build a minimal in-memory Catalog for testing without touching the filesystem."""
    return Catalog(
        version=1,
        kernels=[
            Kernel("k-main", "Main Kernel", "desc", ["linux-main", "linux-main-headers"], primary=True),
            Kernel("k-alt", "Alt Kernel", "desc", ["linux-alt"], primary=False),
        ],
        inits=[
            Init("dinit", "dinit", "desc", ["dinit"], recommended=True),
            Init("runit", "runit", "desc", ["runit"], recommended=False),
            Init("openrc", "OpenRC", "desc", ["openrc"], recommended=False),
        ],
        sessions=[
            Session("wm-bare", "Bare WM", "wm", "desc", 3, 5, _KBS, None,
                    ["wm-bare-pkg"], [], []),
            Session("wm-shell", "Shell WM", "wm", "desc", 4, 4, _KBS, None,
                    ["wm-shell-pkg"], ["wm-shell-svc"], ["shell-a", "shell-b"],
                    session_type="wayland", exec="shellwm"),
            Session("de-named", "Named DE", "de", "desc", 5, 2, _KBS, None,
                    ["de-named-pkg"], [], [],
                    session_type="wayland", exec="named-session",
                    desktop="NAMED"),
            Session("de-full", "Full DE", "de", "desc", 5, 2, _KBS, None,
                    ["de-pkg", "de-extra"], ["de-svc"], []),
        ],
        shells=[
            Shell("shell-a", "Shell A", "desc", 4, 4, _KBS, None, ["shell-a-pkg"],
                  exec="shell-a-cmd"),
            Shell("shell-b", "Shell B", "desc", 3, 3, _KBS, None, ["shell-b-pkg"]),
        ],
        support=[
            SupportToggle("bluetooth", "Bluetooth", "desc",
                          ["bluez", "blueman"], ["bluetoothd"], default=False),
        ],
        gaming=Gaming(
            name="Gaming Mode", description="Gaming",
            packages=["steam", "gamemode"],
            services=["gamemode-svc"],
            gpu_autodetect=True,
        ),
        minimal=Minimal("Minimal", "Bare system"),
    )


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------

class TestHappyPath(unittest.TestCase):

    def setUp(self):
        self.catalog = _make_catalog()

    def test_single_session_no_shell(self):
        sel = Selection("k-main", "dinit", ["wm-bare"], {}, [], False, False)
        plan = resolve_plan(self.catalog, sel)
        self.assertIn("wm-bare-pkg", plan.packages)
        self.assertIn("linux-main", plan.packages)
        self.assertIn("dinit", plan.packages)
        self.assertIn("fastfetch", plan.packages)
        self.assertIn("greetd", plan.packages)
        self.assertIn("greetd", plan.services)
        self.assertEqual(plan.packages, sorted(plan.packages))
        self.assertEqual(plan.services, sorted(plan.services))
        self.assertEqual(len([w for w in plan.warnings if "Multiple" in w]), 0)

    def test_two_sessions_shell_bluetooth_gaming_exact_lists(self):
        sel = Selection(
            kernel_id="k-main", init_id="dinit",
            session_ids=["wm-bare", "wm-shell"],
            shell_choice={"wm-shell": "shell-a"},
            support_ids=["bluetooth"],
            gaming=True, minimal=False,
        )
        plan = resolve_plan(self.catalog, sel, gpu_vendors=frozenset({"amd"}))
        for pkg in ("wm-bare-pkg", "wm-shell-pkg", "shell-a-pkg",
                    "bluez", "blueman", "steam", "gamemode",
                    "fastfetch", "greetd", "linux-main", "dinit"):
            self.assertIn(pkg, plan.packages, f"Missing: {pkg}")
        self.assertIn("greetd", plan.services)
        self.assertIn("bluetoothd", plan.services)
        self.assertIn("wm-shell-svc", plan.services)
        # Sorted and deduplicated
        self.assertEqual(plan.packages, sorted(plan.packages))
        self.assertEqual(plan.services, sorted(plan.services))
        self.assertEqual(len(plan.packages), len(set(plan.packages)))
        self.assertEqual(len(plan.services), len(set(plan.services)))
        # Warning order: multi-session first, GPU second
        self.assertIn("Multiple sessions installed", plan.warnings[0])
        self.assertIn("GPU detected", plan.warnings[1])

    def test_minimal_install_no_greetd_no_sessions(self):
        sel = Selection("k-main", "dinit", [], {}, [], False, True)
        plan = resolve_plan(self.catalog, sel)
        self.assertIn("fastfetch", plan.packages)
        self.assertNotIn("greetd", plan.packages)
        self.assertNotIn("greetd", plan.services)
        self.assertEqual(len(plan.warnings), 0)

    def test_baseline_files_present_in_minimal(self):
        sel = Selection("k-main", "dinit", [], {}, [], False, True)
        plan = resolve_plan(self.catalog, sel)
        paths = [f.path for f in plan.files]
        self.assertIn("/etc/skel/.bashrc", paths)
        self.assertIn("/etc/skel/.zshrc", paths)

    def test_baseline_files_present_with_sessions(self):
        sel = Selection("k-main", "dinit", ["wm-bare"], {}, [], False, False)
        plan = resolve_plan(self.catalog, sel)
        paths = [f.path for f in plan.files]
        self.assertIn("/etc/skel/.bashrc", paths)
        self.assertIn("/etc/skel/.zshrc", paths)

    def test_bashrc_contains_fastfetch_interactive_guard(self):
        sel = Selection("k-main", "dinit", [], {}, [], False, True)
        plan = resolve_plan(self.catalog, sel)
        bashrc = next(f for f in plan.files if f.path == "/etc/skel/.bashrc")
        self.assertIn("fastfetch", bashrc.content)
        self.assertIn("*i*", bashrc.content)
        self.assertEqual(bashrc.mode, 0o644)

    def test_zshrc_contains_fastfetch_interactive_guard(self):
        sel = Selection("k-main", "dinit", [], {}, [], False, True)
        plan = resolve_plan(self.catalog, sel)
        zshrc = next(f for f in plan.files if f.path == "/etc/skel/.zshrc")
        self.assertIn("fastfetch", zshrc.content)
        self.assertIn("interactive", zshrc.content)
        self.assertEqual(zshrc.mode, 0o644)

    def test_files_sorted_by_path(self):
        sel = Selection("k-main", "dinit", [], {}, [], False, True)
        plan = resolve_plan(self.catalog, sel)
        paths = [f.path for f in plan.files]
        self.assertEqual(paths, sorted(paths))

    def test_single_session_no_multi_session_warning(self):
        sel = Selection("k-main", "dinit", ["wm-bare"], {}, [], False, False)
        plan = resolve_plan(self.catalog, sel)
        self.assertEqual(len([w for w in plan.warnings if "Multiple sessions" in w]), 0)

    def test_two_sessions_multi_session_warning(self):
        sel = Selection("k-main", "dinit", ["wm-bare", "de-full"], {}, [], False, False)
        plan = resolve_plan(self.catalog, sel)
        self.assertEqual(len([w for w in plan.warnings if "Multiple sessions" in w]), 1)

    def test_gaming_gpu_autodetect_pins_drivers(self):
        sel = Selection("k-main", "dinit", [], {}, [], True, False)
        plan = resolve_plan(self.catalog, sel, gpu_vendors=frozenset({"amd"}))
        self.assertIn("vulkan-radeon", plan.packages)
        self.assertIn("lib32-vulkan-radeon", plan.packages)
        self.assertEqual(len([w for w in plan.warnings if "GPU detected" in w]), 1)

    def test_gaming_without_detection_adds_no_gpu_packages(self):
        sel = Selection("k-main", "dinit", [], {}, [], True, False)
        plan = resolve_plan(self.catalog, sel)
        self.assertNotIn("vulkan-radeon", plan.packages)
        self.assertNotIn("nvidia-utils", plan.packages)

    def test_no_gaming_no_gpu_warning(self):
        sel = Selection("k-main", "dinit", [], {}, [], False, False)
        plan = resolve_plan(self.catalog, sel)
        self.assertEqual(len([w for w in plan.warnings if "GPU driver" in w]), 0)

    def test_shell_b_selected_not_shell_a(self):
        sel = Selection("k-main", "dinit", ["wm-shell"], {"wm-shell": "shell-b"}, [], False, False)
        plan = resolve_plan(self.catalog, sel)
        self.assertIn("shell-b-pkg", plan.packages)
        self.assertNotIn("shell-a-pkg", plan.packages)

    def test_alt_kernel_packages(self):
        sel = Selection("k-alt", "dinit", [], {}, [], False, False)
        plan = resolve_plan(self.catalog, sel)
        self.assertIn("linux-alt", plan.packages)
        self.assertNotIn("linux-main", plan.packages)

    def test_minimal_with_support_allowed(self):
        sel = Selection("k-main", "dinit", [], {}, ["bluetooth"], False, True)
        plan = resolve_plan(self.catalog, sel)
        self.assertIn("bluez", plan.packages)
        self.assertIn("bluetoothd", plan.services)


# ---------------------------------------------------------------------------
# Negative tests — each must raise PlanError
# ---------------------------------------------------------------------------

class TestNetworkAndInitServicePackages(unittest.TestCase):
    """Rules 9 & 10: target always gets NetworkManager, and every enabled
    service pulls its init-specific service package (greetd-dinit etc.)."""

    def setUp(self):
        self.catalog = _make_catalog()

    def test_ntp_and_os_prober_always_present(self):
        # openntpd keeps the installed clock right; os-prober gives the GRUB
        # menu its dual-boot entries (the live USB's own cloned entries are
        # stripped afterwards by the grub_filter bootloader step)
        for minimal in (True, False):
            sel = Selection("k-main", "dinit", [] if minimal else ["wm-bare"],
                            {}, [], False, minimal)
            plan = resolve_plan(_make_catalog(), sel)
            self.assertIn("openntpd", plan.packages)
            self.assertIn("openntpd", plan.services)
            self.assertIn("openntpd-dinit", plan.packages)
            self.assertIn("os-prober", plan.packages)

    def test_power_profiles_daemon_with_sessions_only(self):
        # performance/balanced/power-saver modes: daemon + enabled service on
        # any graphical install, absent from minimal installs
        for init in ("dinit", "runit", "openrc"):
            sel = Selection("k-main", init, ["wm-bare"], {}, [], False, False)
            plan = resolve_plan(_make_catalog(), sel)
            self.assertIn("power-profiles-daemon", plan.packages)
            self.assertIn("power-profiles-daemon", plan.services)
            self.assertIn(f"power-profiles-daemon-{init}", plan.packages)
        minimal = Selection("k-main", "dinit", [], {}, [], False, True)
        plan = resolve_plan(_make_catalog(), minimal)
        self.assertNotIn("power-profiles-daemon", plan.packages)
        self.assertNotIn("power-profiles-daemon", plan.services)

    def test_desktop_set_has_qt6_wayland(self):
        sel = Selection("k-main", "dinit", ["wm-shell"], {"wm-shell": "shell-a"},
                        [], False, False)
        plan = resolve_plan(_make_catalog(), sel)
        self.assertIn("qt6-wayland", plan.packages)

    def test_networkmanager_always_present(self):
        sel = Selection("k-main", "dinit", [], {}, [], False, True)  # minimal
        plan = resolve_plan(self.catalog, sel)
        self.assertIn("networkmanager", plan.packages)
        self.assertIn("NetworkManager", plan.services)

    def test_init_service_packages_follow_selected_init(self):
        for init_id in ("dinit", "runit", "openrc"):
            sel = Selection("k-main", init_id, ["wm-bare"], {}, [], False, False)
            plan = resolve_plan(self.catalog, sel)
            self.assertIn(f"greetd-{init_id}", plan.packages, init_id)
            self.assertIn(f"networkmanager-{init_id}", plan.packages, init_id)


class TestNegative(unittest.TestCase):

    def setUp(self):
        self.catalog = _make_catalog()

    def _assert_plan_error(self, sel: Selection, fragment: str = "") -> None:
        with self.assertRaises(PlanError) as ctx:
            resolve_plan(self.catalog, sel)
        if fragment:
            self.assertIn(fragment, str(ctx.exception),
                          f"Expected '{fragment}' in error: {ctx.exception}")

    def test_unknown_kernel_id(self):
        sel = Selection("no-such-kernel", "dinit", [], {}, [], False, False)
        self._assert_plan_error(sel, "kernel_id")

    def test_unknown_init_id(self):
        sel = Selection("k-main", "no-such-init", [], {}, [], False, False)
        self._assert_plan_error(sel, "init_id")

    def test_unknown_session_id(self):
        sel = Selection("k-main", "dinit", ["nonexistent-wm"], {}, [], False, False)
        self._assert_plan_error(sel, "session_id")

    def test_duplicate_session_id(self):
        sel = Selection("k-main", "dinit", ["wm-bare", "wm-bare"], {}, [], False, False)
        self._assert_plan_error(sel, "Duplicate session_id")

    def test_minimal_with_sessions(self):
        sel = Selection("k-main", "dinit", ["wm-bare"], {}, [], False, True)
        self._assert_plan_error(sel, "minimal")

    def test_minimal_with_gaming(self):
        sel = Selection("k-main", "dinit", [], {}, [], True, True)
        self._assert_plan_error(sel, "minimal")

    def test_minimal_with_shell_choice(self):
        sel = Selection("k-main", "dinit", [], {"wm-shell": "shell-a"}, [], False, True)
        self._assert_plan_error(sel, "minimal")

    def test_missing_shell_choice_for_session_with_choices(self):
        # wm-shell requires a shell_choice but none is provided
        sel = Selection("k-main", "dinit", ["wm-shell"], {}, [], False, False)
        self._assert_plan_error(sel, "wm-shell")

    def test_shell_choice_unknown_shell_id(self):
        sel = Selection("k-main", "dinit", ["wm-shell"],
                        {"wm-shell": "nonexistent-shell"}, [], False, False)
        self._assert_plan_error(sel, "nonexistent-shell")

    def test_shell_choice_shell_not_in_sessions_allowed_list(self):
        # Add a third shell to the catalog; wm-shell only allows shell-a and shell-b
        cat = _make_catalog()
        cat.shells.append(Shell("shell-c", "Shell C", "desc", 3, 3, _KBS, None, ["shell-c-pkg"]))
        sel = Selection("k-main", "dinit", ["wm-shell"],
                        {"wm-shell": "shell-c"}, [], False, False)
        with self.assertRaises(PlanError) as ctx:
            resolve_plan(cat, sel)
        self.assertIn("shell-c", str(ctx.exception))

    def test_shell_choice_for_session_without_choices(self):
        # wm-bare has shell_choices=[] but we provide a shell_choice for it
        sel = Selection("k-main", "dinit", ["wm-bare"],
                        {"wm-bare": "shell-a"}, [], False, False)
        self._assert_plan_error(sel, "wm-bare")

    def test_shell_choice_for_unselected_session(self):
        # wm-shell is not in session_ids but appears in shell_choice
        sel = Selection("k-main", "dinit", ["wm-bare"],
                        {"wm-shell": "shell-a"}, [], False, False)
        self._assert_plan_error(sel, "wm-shell")

    def test_unknown_support_id(self):
        sel = Selection("k-main", "dinit", [], {}, ["nonexistent-support"], False, False)
        self._assert_plan_error(sel, "support_id")


# ---------------------------------------------------------------------------
# Determinism tests
# ---------------------------------------------------------------------------

class TestDeterminism(unittest.TestCase):

    def setUp(self):
        self.catalog = _make_catalog()

    def test_two_equal_calls_produce_equal_plans(self):
        sel = Selection(
            kernel_id="k-main", init_id="dinit",
            session_ids=["wm-bare", "wm-shell"],
            shell_choice={"wm-shell": "shell-a"},
            support_ids=["bluetooth"],
            gaming=True, minimal=False,
        )
        plan1 = resolve_plan(self.catalog, sel)
        plan2 = resolve_plan(self.catalog, sel)
        self.assertEqual(plan1.packages, plan2.packages)
        self.assertEqual(plan1.services, plan2.services)
        self.assertEqual(plan1.warnings, plan2.warnings)

    def test_permuted_session_ids_produce_identical_plan(self):
        sel1 = Selection(
            kernel_id="k-main", init_id="dinit",
            session_ids=["wm-bare", "wm-shell"],
            shell_choice={"wm-shell": "shell-a"},
            support_ids=["bluetooth"], gaming=False, minimal=False,
        )
        sel2 = Selection(
            kernel_id="k-main", init_id="dinit",
            session_ids=["wm-shell", "wm-bare"],
            shell_choice={"wm-shell": "shell-a"},
            support_ids=["bluetooth"], gaming=False, minimal=False,
        )
        plan1 = resolve_plan(self.catalog, sel1)
        plan2 = resolve_plan(self.catalog, sel2)
        self.assertEqual(plan1.packages, plan2.packages)
        self.assertEqual(plan1.services, plan2.services)

    def test_permuted_support_ids_produce_identical_plan(self):
        cat = _make_catalog()
        cat.support.append(
            SupportToggle("printing", "Printing", "desc", ["cups"], ["cupsd"], False)
        )
        sel1 = Selection("k-main", "dinit", [], {}, ["bluetooth", "printing"], False, False)
        sel2 = Selection("k-main", "dinit", [], {}, ["printing", "bluetooth"], False, False)
        plan1 = resolve_plan(cat, sel1)
        plan2 = resolve_plan(cat, sel2)
        self.assertEqual(plan1.packages, plan2.packages)
        self.assertEqual(plan1.services, plan2.services)

    def test_packages_sorted_and_deduplicated(self):
        sel = Selection(
            kernel_id="k-main", init_id="dinit",
            session_ids=["wm-bare", "wm-shell"],
            shell_choice={"wm-shell": "shell-a"},
            support_ids=["bluetooth"], gaming=True, minimal=False,
        )
        plan = resolve_plan(self.catalog, sel)
        self.assertEqual(plan.packages, sorted(plan.packages))
        self.assertEqual(len(plan.packages), len(set(plan.packages)))

    def test_services_sorted_and_deduplicated(self):
        sel = Selection(
            kernel_id="k-main", init_id="dinit",
            session_ids=["wm-bare", "wm-shell"],
            shell_choice={"wm-shell": "shell-a"},
            support_ids=["bluetooth"], gaming=True, minimal=False,
        )
        plan = resolve_plan(self.catalog, sel)
        self.assertEqual(plan.services, sorted(plan.services))
        self.assertEqual(len(plan.services), len(set(plan.services)))


# ---------------------------------------------------------------------------
# Integration tests — uses the real catalog/catalog.json
# ---------------------------------------------------------------------------

class TestIntegration(unittest.TestCase):

    def setUp(self):
        self.catalog = load_catalog(_CATALOG_PATH)

    def test_niri_sway_multi_session(self):
        """linux-cachyos + dinit + niri (noctalia shell) + sway. mangowc and
        the glue-bar/imperative-qs quickshell bars stay gone."""
        sel = Selection(
            kernel_id="linux-cachyos",
            init_id="dinit",
            session_ids=["niri", "sway"],
            shell_choice={"niri": "noctalia"},
            support_ids=[],
            gaming=False,
            minimal=False,
        )
        plan = resolve_plan(self.catalog, sel)
        # Required packages
        self.assertIn("greetd", plan.packages)
        self.assertIn("fastfetch", plan.packages)
        self.assertIn("niri", plan.packages)
        self.assertIn("sway", plan.packages)
        self.assertIn("noctalia-shell", plan.packages)
        self.assertIn("power-profiles-daemon", plan.packages)
        for gone in ("mangowm", "glue-bar", "imperative-qs"):
            self.assertNotIn(gone, plan.packages)
        # Required services
        self.assertIn("greetd", plan.services)
        self.assertIn("power-profiles-daemon", plan.services)
        # Sorted and deduplicated
        self.assertEqual(plan.packages, sorted(plan.packages))
        self.assertEqual(len(plan.packages), len(set(plan.packages)))
        self.assertEqual(plan.services, sorted(plan.services))
        self.assertEqual(len(plan.services), len(set(plan.services)))
        # Multi-session warning
        self.assertEqual(len([w for w in plan.warnings if "Multiple sessions" in w]), 1)

    def test_real_minimal_install(self):
        sel = Selection(
            kernel_id="linux-cachyos", init_id="dinit",
            session_ids=[], shell_choice={},
            support_ids=[], gaming=False, minimal=True,
        )
        plan = resolve_plan(self.catalog, sel)
        self.assertIn("fastfetch", plan.packages)
        self.assertNotIn("greetd", plan.packages)
        paths = [f.path for f in plan.files]
        self.assertIn("/etc/skel/.bashrc", paths)
        self.assertIn("/etc/skel/.zshrc", paths)

    def test_real_gaming_mode(self):
        sel = Selection(
            kernel_id="linux-cachyos", init_id="dinit",
            session_ids=[], shell_choice={},
            support_ids=[], gaming=True, minimal=False,
        )
        plan = resolve_plan(self.catalog, sel, gpu_vendors=frozenset({"intel"}))
        self.assertIn("steam", plan.packages)
        self.assertIn("gamemode", plan.packages)
        self.assertIn("vulkan-intel", plan.packages)
        self.assertEqual(len([w for w in plan.warnings if "GPU detected" in w]), 1)


# ---------------------------------------------------------------------------
# Greeter profile (greetd + tuigreet + per-session wrappers)
# ---------------------------------------------------------------------------

class TestGreeterProfile(unittest.TestCase):

    def setUp(self):
        self.catalog = _make_catalog()

    def _plan(self, session_ids, shell_choice=None):
        sel = Selection("k-main", "dinit", session_ids, shell_choice or {},
                        [], False, False)
        return resolve_plan(self.catalog, sel)

    def _file(self, plan, path):
        return next(f for f in plan.files if f.path == path)

    def test_tuigreet_package_with_sessions(self):
        plan = self._plan(["wm-bare"])
        self.assertIn("greetd-tuigreet", plan.packages)

    def test_no_greeter_files_on_minimal(self):
        sel = Selection("k-main", "dinit", [], {}, [], False, True)
        plan = resolve_plan(self.catalog, sel)
        self.assertNotIn("greetd-tuigreet", plan.packages)
        paths = [f.path for f in plan.files]
        self.assertNotIn("/etc/greetd/config.toml", paths)

    def test_greetd_config_on_vt7_pointing_at_glue_sessions(self):
        plan = self._plan(["wm-bare"])
        cfg = self._file(plan, "/etc/greetd/config.toml")
        self.assertIn("vt = 7", cfg.content)
        self.assertIn("tuigreet", cfg.content)
        self.assertIn("--sessions /usr/share/glue/sessions", cfg.content)
        self.assertIn('user = "greeter"', cfg.content)
        self.assertEqual(cfg.mode, 0o644)

    def test_greetd_config_greeting_and_no_legacy_branding(self):
        plan = self._plan(["wm-bare"])
        cfg = self._file(plan, "/etc/greetd/config.toml")
        self.assertIn("--greeting 'Glue Linux'", cfg.content)
        self.assertNotIn("wheatley", cfg.content.lower())
        self.assertNotIn("artix", cfg.content.lower())

    def test_x11_wrapper_has_startx_dbus_and_audio(self):
        plan = self._plan(["wm-bare"])
        wrapper = self._file(plan, "/usr/local/bin/glue-session-wm-bare")
        self.assertEqual(wrapper.mode, 0o755)
        self.assertIn("startx", wrapper.content)
        self.assertIn("dbus-run-session", wrapper.content)
        self.assertIn("pipewire", wrapper.content)
        self.assertIn("exec wm-bare", wrapper.content)  # exec defaults to id
        self.assertIn("XDG_SESSION_TYPE=x11", wrapper.content)

    def test_wayland_wrapper_no_startx_uses_exec(self):
        plan = self._plan(["wm-shell"], {"wm-shell": "shell-b"})
        wrapper = self._file(plan, "/usr/local/bin/glue-session-wm-shell")
        self.assertNotIn("startx", wrapper.content)
        self.assertIn("dbus-run-session", wrapper.content)
        self.assertIn("exec shellwm", wrapper.content)
        self.assertIn("XDG_SESSION_TYPE=wayland", wrapper.content)

    def test_shell_with_exec_is_autostarted(self):
        plan = self._plan(["wm-shell"], {"wm-shell": "shell-a"})
        wrapper = self._file(plan, "/usr/local/bin/glue-session-wm-shell")
        self.assertIn("shell-a-cmd", wrapper.content)
        self.assertIn("WAYLAND_DISPLAY", wrapper.content)
        entry = self._file(plan, "/usr/share/glue/sessions/wm-shell.desktop")
        self.assertIn("Name=Shell WM + Shell A", entry.content)

    def test_shell_without_exec_not_autostarted(self):
        plan = self._plan(["wm-shell"], {"wm-shell": "shell-b"})
        wrapper = self._file(plan, "/usr/local/bin/glue-session-wm-shell")
        self.assertNotIn("WAYLAND_DISPLAY", wrapper.content)

    def test_session_entry_per_selected_session(self):
        plan = self._plan(["wm-bare", "de-full"])
        for sid in ("wm-bare", "de-full"):
            entry = self._file(plan, f"/usr/share/glue/sessions/{sid}.desktop")
            self.assertIn(f"Exec=/usr/local/bin/glue-session-{sid}",
                          entry.content)

    def test_session_with_gpu_vendors_gets_driver_stack(self):
        sel = Selection("k-main", "dinit", ["wm-bare"], {}, [], False, False)
        plan = resolve_plan(self.catalog, sel, gpu_vendors=frozenset({"nvidia"}))
        self.assertIn("mesa", plan.packages)
        self.assertIn("nvidia-open-dkms", plan.packages)
        self.assertIn("egl-wayland", plan.packages)

    def test_desktop_set_has_mesa_and_rtkit(self):
        plan = self._plan(["wm-bare"])
        self.assertIn("mesa", plan.packages)
        self.assertIn("rtkit", plan.packages)
        self.assertIn("alacritty", plan.packages)

    def test_real_catalog_wayland_wm_sessions_have_xwayland_satellite_and_rofi(self):
        catalog = load_catalog(_CATALOG_PATH)
        for session in catalog.sessions:
            if session.session_type == "wayland" and session.kind == "wm":
                self.assertIn("xwayland-satellite", session.packages, session.id)
                self.assertIn("rofi", session.packages, session.id)
                self.assertIn("alacritty", session.packages, session.id)


    def test_wayland_wrapper_allows_software_renderer_fallback(self):
        plan = self._plan(["wm-shell"], {"wm-shell": "shell-b"})
        wrapper = self._file(plan, "/usr/local/bin/glue-session-wm-shell")
        self.assertIn("WLR_RENDERER_ALLOW_SOFTWARE=1", wrapper.content)
        x11 = self._plan(["wm-bare"])
        x11_wrapper = self._file(x11, "/usr/local/bin/glue-session-wm-bare")
        self.assertNotIn("WLR_RENDERER_ALLOW_SOFTWARE", x11_wrapper.content)

    def test_wayland_software_fallback_is_conditional_on_no_render_node(self):
        plan = self._plan(["wm-shell"], {"wm-shell": "shell-b"})
        wrapper = self._file(plan, "/usr/local/bin/glue-session-wm-shell")
        self.assertIn("/dev/dri/renderD", wrapper.content)
        # the fallback export must be inside the no-render-node guard
        self.assertLess(wrapper.content.index("/dev/dri/renderD"),
                        wrapper.content.index("WLR_RENDERER_ALLOW_SOFTWARE=1"))

    def test_no_wrapper_ever_presets_display_or_spawns_satellite(self):
        # Regression guard (booted on real hardware): a preset DISPLAY makes
        # wlroots/dwl and niri pick the nested X11 backend on a real VT and
        # die with "Failed to open xcb connection / couldn't create backend".
        # X11 apps go through each compositor's own XWayland integration.
        plan = self._plan(["wm-shell", "wm-bare", "de-named"],
                          {"wm-shell": "shell-b"})
        for f in plan.files:
            if "/usr/local/bin/glue-session-" in f.path:
                self.assertNotIn("export DISPLAY=", f.content, f.path)
                self.assertNotIn("xwayland-satellite", f.content, f.path)

    def test_desktop_name_overrides_id_in_wrapper_and_entry(self):
        plan = self._plan(["de-named"])
        wrapper = self._file(plan, "/usr/local/bin/glue-session-de-named")
        self.assertIn("export XDG_CURRENT_DESKTOP=NAMED", wrapper.content)
        self.assertIn("export XDG_SESSION_DESKTOP=NAMED", wrapper.content)
        entry = self._file(plan, "/usr/share/glue/sessions/de-named.desktop")
        self.assertIn("DesktopNames=NAMED", entry.content)

    def test_wrapper_without_desktop_field_keeps_id(self):
        plan = self._plan(["wm-bare"])
        wrapper = self._file(plan, "/usr/local/bin/glue-session-wm-bare")
        self.assertIn("export XDG_CURRENT_DESKTOP=wm-bare", wrapper.content)

    def test_real_catalog_gnome_is_minimal_wayland_de(self):
        catalog = load_catalog(_CATALOG_PATH)
        gnome = next(s for s in catalog.sessions if s.id == "gnome")
        self.assertEqual(gnome.kind, "de")
        self.assertEqual(gnome.session_type, "wayland")
        self.assertEqual(gnome.exec, "gnome-session")
        self.assertEqual(gnome.desktop, "GNOME")
        # minimal: core desktop only, no full gnome group, no gdm (greetd
        # is the greeter; gdm also CONFLICTS with gnome-session-sysvinit),
        # nothing that could fight a parallel KDE install
        self.assertNotIn("gnome", gnome.packages)
        self.assertNotIn("gdm", gnome.packages)
        self.assertNotIn("gnome-extra", gnome.packages)
        for pkg in ("gnome-shell", "gnome-session", "gnome-console",
                    "nautilus", "gnome-control-center"):
            self.assertIn(pkg, gnome.packages)
        # GNOME 50's gnome-session leader ALWAYS execs
        # /usr/lib/gnome-session-init-worker; on Artix only the
        # gnome-session-sysvinit package ships that file (init-agnostic
        # worker, provides gnome-session-leader). Without it the session
        # dies instantly with "Failed to exec gnome-session-init-worker".
        self.assertIn("gnome-session-sysvinit", gnome.packages)

    def test_real_catalog_gaming_has_user_requested_extras(self):
        catalog = load_catalog(_CATALOG_PATH)
        for pkg in ("proton-ge-custom-bin", "firefox", "lm_sensors",
                    "gamescope", "lib32-mesa", "lib32-pipewire"):
            self.assertIn(pkg, catalog.gaming.packages, pkg)

    def test_real_catalog_sessions_have_exec_and_type(self):
        catalog = load_catalog(_CATALOG_PATH)
        wayland = {"niri", "sway", "gnome"}
        for session in catalog.sessions:
            self.assertIsNotNone(session.exec, session.id)
            expected = "wayland" if session.id in wayland else "x11"
            self.assertEqual(session.session_type, expected, session.id)
        for shell in catalog.shells:
            self.assertIsNotNone(shell.exec, shell.id)


if __name__ == "__main__":
    unittest.main()


class TestGamingFilesAndPrimeRun(unittest.TestCase):
    """Gaming shader-cache env file + the prime-run dGPU wrapper (Artix has
    no nvidia-prime package, so the installer writes the script itself)."""

    def _paths(self, sel, gpu_vendors=None):
        plan = resolve_plan(_make_catalog(), sel, gpu_vendors=gpu_vendors)
        return plan, [f.path for f in plan.files]

    def test_gaming_gets_shader_cache_env_file(self):
        sel = Selection("k-main", "dinit", ["wm-bare"], {}, [], True, False)
        plan, paths = self._paths(sel)
        self.assertIn("/etc/profile.d/glue-gaming.sh", paths)
        env = next(f for f in plan.files
                   if f.path == "/etc/profile.d/glue-gaming.sh")
        self.assertIn("__GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1", env.content)
        self.assertEqual(env.mode, 0o644)

    def test_no_gaming_no_env_file(self):
        sel = Selection("k-main", "dinit", ["wm-bare"], {}, [], False, False)
        _, paths = self._paths(sel)
        self.assertNotIn("/etc/profile.d/glue-gaming.sh", paths)

    def test_prime_run_written_on_nvidia_systems(self):
        sel = Selection("k-main", "dinit", ["wm-bare"], {}, [], False, False)
        plan, paths = self._paths(sel, gpu_vendors=frozenset({"nvidia", "intel"}))
        self.assertIn("/usr/local/bin/prime-run", paths)
        script = next(f for f in plan.files
                      if f.path == "/usr/local/bin/prime-run")
        self.assertEqual(script.mode, 0o755)
        self.assertTrue(script.content.startswith("#!/bin/sh"))
        for env in ("__NV_PRIME_RENDER_OFFLOAD=1",
                    "__GLX_VENDOR_LIBRARY_NAME=nvidia",
                    "__VK_LAYER_NV_optimus=NVIDIA_only"):
            self.assertIn(env, script.content)
        self.assertIn('exec "$@"', script.content)

    def test_prime_run_absent_without_nvidia_or_without_desktop(self):
        # AMD-only machine: no wrapper
        sel = Selection("k-main", "dinit", ["wm-bare"], {}, [], True, False)
        _, paths = self._paths(sel, gpu_vendors=frozenset({"amd"}))
        self.assertNotIn("/usr/local/bin/prime-run", paths)
        # nvidia but minimal install (no sessions, no gaming): no wrapper
        minimal = Selection("k-main", "dinit", [], {}, [], False, True)
        _, paths = self._paths(minimal, gpu_vendors=frozenset({"nvidia"}))
        self.assertNotIn("/usr/local/bin/prime-run", paths)

    def test_real_catalog_niri_ships_xwayland_satellite_for_steam(self):
        # Steam is an X11 app: niri (>=25.05) auto-starts xwayland-satellite
        # from PATH and sets DISPLAY itself — the guarantee is the package
        # being installed with the session, never a preset DISPLAY.
        catalog = load_catalog(_CATALOG_PATH)
        niri = next(s for s in catalog.sessions if s.id == "niri")
        self.assertIn("xwayland-satellite", niri.packages)


# ---------------------------------------------------------------------------
# Zram (roadmap 1.2) and gaming additions (roadmap 1.3)
# ---------------------------------------------------------------------------

class TestZramAndGaming(unittest.TestCase):
    """1.2: zramen on every install with zstd; 1.3: gaming package additions."""

    def _plan(self, init_id, gaming=False, session_ids=None, gpu_vendors=None):
        catalog = _make_catalog()
        sel = Selection("k-main", init_id, session_ids or [], {}, [], gaming, False)
        return resolve_plan(catalog, sel, gpu_vendors=gpu_vendors)

    def test_zramen_on_non_gaming_dinit(self):
        plan = self._plan("dinit")
        self.assertIn("zramen", plan.packages)
        self.assertIn("zramen-dinit", plan.packages)
        self.assertIn("zramen", plan.services)

    def test_zramen_on_non_gaming_runit(self):
        plan = self._plan("runit")
        self.assertIn("zramen", plan.packages)
        self.assertIn("zramen-runit", plan.packages)
        self.assertIn("zramen", plan.services)

    def test_zramen_on_non_gaming_openrc(self):
        plan = self._plan("openrc")
        self.assertIn("zramen", plan.packages)
        self.assertIn("zramen-openrc", plan.packages)
        self.assertIn("zramen", plan.services)

    def test_zramen_config_dinit_zstd(self):
        plan = self._plan("dinit")
        cfg = next((f for f in plan.files if f.path == "/etc/dinit.d/config/zramen.conf"), None)
        self.assertIsNotNone(cfg, "zramen dinit config file missing")
        self.assertIn("ZRAM_COMP_ALGORITHM", cfg.content)
        self.assertIn("zstd", cfg.content)
        self.assertEqual(cfg.mode, 0o644)

    def test_zramen_config_runit_zstd(self):
        plan = self._plan("runit")
        cfg = next((f for f in plan.files if f.path == "/etc/runit/sv/zramen/conf"), None)
        self.assertIsNotNone(cfg, "zramen runit config file missing")
        self.assertIn("zstd", cfg.content)

    def test_zramen_config_openrc_zstd(self):
        plan = self._plan("openrc")
        cfg = next((f for f in plan.files if f.path == "/etc/conf.d/zramen"), None)
        self.assertIsNotNone(cfg, "zramen openrc config file missing")
        self.assertIn("zstd", cfg.content)

    def test_gaming_includes_new_packages_real_catalog(self):
        catalog = load_catalog(_CATALOG_PATH)
        sel = Selection("linux-cachyos", "dinit", [], {}, [], True, False)
        plan = resolve_plan(catalog, sel)
        for pkg in ("ntsync-autoload", "proton-cachyos-slr", "umu-launcher",
                    "proton-ge-custom-bin"):
            self.assertIn(pkg, plan.packages, f"Gaming plan missing: {pkg}")

    def test_non_gaming_excludes_gaming_packages_real_catalog(self):
        catalog = load_catalog(_CATALOG_PATH)
        sel = Selection("linux-cachyos", "dinit", [], {}, [], False, False)
        plan = resolve_plan(catalog, sel)
        for pkg in ("ntsync-autoload", "proton-cachyos-slr", "umu-launcher",
                    "proton-ge-custom-bin"):
            self.assertNotIn(pkg, plan.packages, f"Non-gaming plan has: {pkg}")
