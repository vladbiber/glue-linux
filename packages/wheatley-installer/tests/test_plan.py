"""
Unit tests for wheatley_installer.plan.

Run with:
  cd packages/wheatley-installer
  python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from wheatley_installer.catalog import (
    Catalog, Gaming, Init, Kernel, Keybinding, Minimal,
    Session, Shell, SupportToggle, load_catalog,
)
from wheatley_installer.plan import (
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
        ],
        sessions=[
            Session("wm-bare", "Bare WM", "wm", "desc", 3, 5, _KBS, None,
                    ["wm-bare-pkg"], [], []),
            Session("wm-shell", "Shell WM", "wm", "desc", 4, 4, _KBS, None,
                    ["wm-shell-pkg"], ["wm-shell-svc"], ["shell-a", "shell-b"]),
            Session("de-full", "Full DE", "de", "desc", 5, 2, _KBS, None,
                    ["de-pkg", "de-extra"], ["de-svc"], []),
        ],
        shells=[
            Shell("shell-a", "Shell A", "desc", 4, 4, _KBS, None, ["shell-a-pkg"]),
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
        plan = resolve_plan(self.catalog, sel)
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
        self.assertIn("GPU driver", plan.warnings[1])

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

    def test_gaming_gpu_autodetect_warning_present(self):
        sel = Selection("k-main", "dinit", [], {}, [], True, False)
        plan = resolve_plan(self.catalog, sel)
        self.assertEqual(len([w for w in plan.warnings if "GPU driver" in w]), 1)

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

    def test_mangowc_niri_with_shell_choices(self):
        """linux-cachyos + dinit + mangowc (imperative-dots) + niri (noctalia)."""
        sel = Selection(
            kernel_id="linux-cachyos",
            init_id="dinit",
            session_ids=["mangowc", "niri"],
            shell_choice={
                "mangowc": "imperative-dots",
                "niri": "noctalia",
            },
            support_ids=[],
            gaming=False,
            minimal=False,
        )
        plan = resolve_plan(self.catalog, sel)
        # Required packages
        self.assertIn("greetd", plan.packages)
        self.assertIn("fastfetch", plan.packages)
        self.assertIn("mangowc", plan.packages)
        self.assertIn("niri", plan.packages)
        self.assertIn("quickshell", plan.packages)        # from imperative-dots shell
        self.assertIn("noctalia-shell", plan.packages)   # from noctalia shell
        # Required services
        self.assertIn("greetd", plan.services)
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
        plan = resolve_plan(self.catalog, sel)
        self.assertIn("steam", plan.packages)
        self.assertIn("gamemode", plan.packages)
        self.assertEqual(len([w for w in plan.warnings if "GPU driver" in w]), 1)


if __name__ == "__main__":
    unittest.main()
