"""
Tests for Faza 1.11: laptop thermal management, CPU vendor detection,
amd_pstate, sensors-detect oneshot, and game-performance restore contract.
"""

import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.laptop import (
    is_laptop, cpu_vendor, amd_needs_pstate_active,
)
from glue_installer.catalog import (
    Catalog, Gaming, Init, Kernel, Keybinding, Minimal,
    Session, Shell, SupportToggle, load_catalog,
)
from glue_installer.plan import Selection, resolve_plan

_REPO_ROOT = _PKG_ROOT.parent.parent
_SETTINGS_DIR = _REPO_ROOT / "packages" / "glue-settings"
_CATALOG_PATH = _PKG_ROOT / "catalog" / "catalog.json"

_KBS = [
    Keybinding("Super+Return", "Open terminal"),
    Keybinding("Super+q", "Close window"),
    Keybinding("Super+h", "Focus left"),
    Keybinding("Super+j", "Focus down"),
    Keybinding("Super+k", "Focus up"),
]

# /proc/cpuinfo fixtures
_INTEL_CPUINFO = """\
processor\t: 0
vendor_id\t: GenuineIntel
cpu family\t: 6
model name\t: Intel(R) Core(TM) i7-1165G7 @ 2.80GHz
flags\t\t: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov
"""

_AMD_ZEN3_CPUINFO = """\
processor\t: 0
vendor_id\t: AuthenticAMD
cpu family\t: 25
model\t\t: 33
model name\t: AMD Ryzen 5 5600X 6-Core Processor
flags\t\t: fpu vme de pse tsc rdtscp cppc pni clflush
"""

_AMD_ZEN2_CPUINFO = """\
processor\t: 0
vendor_id\t: AuthenticAMD
cpu family\t: 23
model\t\t: 113
model name\t: AMD Ryzen 9 3900X 12-Core Processor
flags\t\t: fpu vme de pse tsc rdtscp cppc pni clflush
"""

_AMD_ZEN1_CPUINFO = """\
processor\t: 0
vendor_id\t: AuthenticAMD
cpu family\t: 23
model\t\t: 1
model name\t: AMD Ryzen 7 1700X Eight-Core Processor
flags\t\t: fpu vme de pse tsc rdtscp pni clflush
"""

_AMD_OLD_CPUINFO = """\
processor\t: 0
vendor_id\t: AuthenticAMD
cpu family\t: 16
model name\t: AMD Phenom II X4 965
flags\t\t: fpu vme de pse tsc
"""

_EMPTY_CPUINFO = ""


def _make_catalog() -> Catalog:
    return Catalog(
        version=1,
        kernels=[
            Kernel("k-main", "Main Kernel", "desc", ["linux-main"], primary=True),
        ],
        inits=[
            Init("dinit", "dinit", "desc", ["dinit"], recommended=True),
            Init("runit", "runit", "desc", ["runit"], recommended=False),
            Init("openrc", "OpenRC", "desc", ["openrc"], recommended=False),
        ],
        sessions=[
            Session("wm-bare", "Bare WM", "wm", "desc", 3, 5, _KBS, None,
                    ["wm-bare-pkg"], [], []),
        ],
        shells=[],
        support=[],
        gaming=Gaming(
            name="Gaming Mode", description="Gaming",
            packages=["steam"], services=[],
            gpu_autodetect=False,
        ),
        minimal=Minimal("Minimal", "Bare system"),
    )


# ---------------------------------------------------------------------------
# is_laptop()
# ---------------------------------------------------------------------------

class TestIsLaptop(unittest.TestCase):

    def test_bat0_detected_as_laptop(self):
        self.assertTrue(is_laptop(["/sys/class/power_supply/BAT0"]))

    def test_bat1_detected_as_laptop(self):
        self.assertTrue(is_laptop(["/sys/class/power_supply/BAT1"]))

    def test_multiple_batteries_detected(self):
        self.assertTrue(is_laptop([
            "/sys/class/power_supply/BAT0",
            "/sys/class/power_supply/BAT1",
        ]))

    def test_no_battery_not_laptop(self):
        self.assertFalse(is_laptop([]))

    def test_empty_bat_list_not_laptop(self):
        self.assertFalse(is_laptop([]))


# ---------------------------------------------------------------------------
# cpu_vendor()
# ---------------------------------------------------------------------------

class TestCpuVendor(unittest.TestCase):

    def test_intel_vendor(self):
        self.assertEqual(cpu_vendor(_INTEL_CPUINFO), "intel")

    def test_amd_vendor_zen3(self):
        self.assertEqual(cpu_vendor(_AMD_ZEN3_CPUINFO), "amd")

    def test_amd_vendor_zen1(self):
        self.assertEqual(cpu_vendor(_AMD_ZEN1_CPUINFO), "amd")

    def test_empty_cpuinfo_returns_other(self):
        self.assertEqual(cpu_vendor(_EMPTY_CPUINFO), "other")

    def test_unknown_vendor_returns_other(self):
        text = "vendor_id\t: GenuineXYZ\ncpu family\t: 6\n"
        self.assertEqual(cpu_vendor(text), "other")

    def test_no_vendor_line_returns_other(self):
        text = "cpu family\t: 6\nflags\t\t: fpu\n"
        self.assertEqual(cpu_vendor(text), "other")


# ---------------------------------------------------------------------------
# amd_needs_pstate_active()
# ---------------------------------------------------------------------------

class TestAmdNeedsPstateActive(unittest.TestCase):

    def test_zen3_with_cppc_returns_true(self):
        self.assertTrue(amd_needs_pstate_active(_AMD_ZEN3_CPUINFO))

    def test_zen2_with_cppc_returns_true(self):
        self.assertTrue(amd_needs_pstate_active(_AMD_ZEN2_CPUINFO))

    def test_zen1_without_cppc_returns_false(self):
        self.assertFalse(amd_needs_pstate_active(_AMD_ZEN1_CPUINFO))

    def test_old_amd_family16_returns_false(self):
        self.assertFalse(amd_needs_pstate_active(_AMD_OLD_CPUINFO))

    def test_intel_returns_false(self):
        self.assertFalse(amd_needs_pstate_active(_INTEL_CPUINFO))

    def test_empty_returns_false(self):
        self.assertFalse(amd_needs_pstate_active(_EMPTY_CPUINFO))

    def test_amd_family23_no_cppc_returns_false(self):
        text = (
            "vendor_id\t: AuthenticAMD\n"
            "cpu family\t: 23\n"
            "flags\t\t: fpu vme de pse\n"
        )
        self.assertFalse(amd_needs_pstate_active(text))


# ---------------------------------------------------------------------------
# Plan rules: thermald (Intel only), ppd on laptop, sensors-detect on laptop
# ---------------------------------------------------------------------------

class TestPlanLaptopRules(unittest.TestCase):

    def setUp(self):
        self.catalog = _make_catalog()

    def _plan(self, init_id="dinit", sessions=None, gaming=False,
              is_laptop_=False, cpu_vendor_id="other", amd_pstate=False):
        sel = Selection(
            "k-main", init_id,
            sessions or [], {}, [], gaming, False,
        )
        return resolve_plan(
            self.catalog, sel,
            is_laptop=is_laptop_, cpu_vendor_id=cpu_vendor_id,
            amd_pstate_active=amd_pstate,
        )

    def test_intel_desktop_has_thermald(self):
        plan = self._plan(cpu_vendor_id="intel")
        self.assertIn("thermald", plan.packages)
        self.assertIn("thermald", plan.services)

    def test_intel_desktop_has_thermald_init_package(self):
        for init_id in ("dinit", "runit", "openrc"):
            plan = self._plan(init_id=init_id, cpu_vendor_id="intel")
            self.assertIn(f"thermald-{init_id}", plan.packages, init_id)

    def test_amd_desktop_no_thermald(self):
        plan = self._plan(cpu_vendor_id="amd")
        self.assertNotIn("thermald", plan.packages)
        self.assertNotIn("thermald", plan.services)

    def test_other_cpu_no_thermald(self):
        plan = self._plan(cpu_vendor_id="other")
        self.assertNotIn("thermald", plan.packages)

    def test_laptop_has_ppd(self):
        plan = self._plan(is_laptop_=True)
        self.assertIn("power-profiles-daemon", plan.packages)
        self.assertIn("power-profiles-daemon", plan.services)

    def test_laptop_ppd_has_init_package(self):
        for init_id in ("dinit", "runit", "openrc"):
            plan = self._plan(init_id=init_id, is_laptop_=True)
            self.assertIn(f"power-profiles-daemon-{init_id}", plan.packages, init_id)

    def test_non_laptop_no_extra_ppd(self):
        # Desktop minimal: ppd not forced (sessions don't add it either here)
        plan = self._plan(is_laptop_=False)
        self.assertNotIn("power-profiles-daemon", plan.packages)

    def test_laptop_has_lm_sensors(self):
        plan = self._plan(is_laptop_=True)
        self.assertIn("lm_sensors", plan.packages)

    def test_non_laptop_no_lm_sensors(self):
        plan = self._plan(is_laptop_=False)
        self.assertNotIn("lm_sensors", plan.packages)

    def test_laptop_has_glue_sensors_detect_service(self):
        plan = self._plan(is_laptop_=True)
        self.assertIn("glue-sensors-detect", plan.services)

    def test_non_laptop_no_sensors_detect_service(self):
        plan = self._plan(is_laptop_=False)
        self.assertNotIn("glue-sensors-detect", plan.services)

    def test_amd_pstate_active_goes_to_cmdline_extra(self):
        plan = self._plan(cpu_vendor_id="amd", amd_pstate=True)
        self.assertEqual(plan.cmdline_extra, ["amd_pstate=active"])

    def test_amd_pstate_active_no_grub_dropin_file(self):
        plan = self._plan(cpu_vendor_id="amd", amd_pstate=True)
        self.assertFalse([f.path for f in plan.files
                          if f.path.startswith("/etc/default/grub")])

    def test_no_amd_pstate_empty_cmdline_extra(self):
        for vendor in ("intel", "amd", "other"):
            plan = self._plan(cpu_vendor_id=vendor, amd_pstate=False)
            self.assertEqual(plan.cmdline_extra, [], f"vendor={vendor}")

    def test_intel_laptop_thermald_and_ppd(self):
        plan = self._plan(cpu_vendor_id="intel", is_laptop_=True)
        self.assertIn("thermald", plan.packages)
        self.assertIn("power-profiles-daemon", plan.packages)
        self.assertIn("lm_sensors", plan.packages)

    def test_real_catalog_intel_laptop(self):
        catalog = load_catalog(_CATALOG_PATH)
        sel = Selection("linux-cachyos", "dinit", [], {}, [], False, False)
        plan = resolve_plan(catalog, sel, is_laptop=True, cpu_vendor_id="intel")
        self.assertIn("thermald", plan.packages)
        self.assertIn("thermald", plan.services)
        self.assertIn("thermald-dinit", plan.packages)
        self.assertIn("power-profiles-daemon", plan.packages)
        self.assertIn("lm_sensors", plan.packages)


# ---------------------------------------------------------------------------
# glue-sensors-detect service files exist and are correct
# ---------------------------------------------------------------------------

class TestSensorsDetectServiceFiles(unittest.TestCase):

    def test_script_exists(self):
        self.assertTrue((_SETTINGS_DIR / "glue-sensors-detect").is_file())

    def test_script_has_marker_guard(self):
        text = (_SETTINGS_DIR / "glue-sensors-detect").read_text()
        self.assertIn("sensors-detected", text)
        self.assertIn("exit 0", text)

    def test_script_calls_sensors_detect(self):
        text = (_SETTINGS_DIR / "glue-sensors-detect").read_text()
        self.assertIn("sensors-detect", text)
        self.assertIn("--auto", text)

    def test_dinit_service_file_exists(self):
        svc = _SETTINGS_DIR / "dinit.d" / "glue-sensors-detect"
        self.assertTrue(svc.is_file())
        self.assertIn("/usr/bin/glue-sensors-detect", svc.read_text())

    def test_runit_service_file_exists(self):
        svc = _SETTINGS_DIR / "runit" / "glue-sensors-detect" / "run"
        self.assertTrue(svc.is_file())
        self.assertIn("/usr/bin/glue-sensors-detect", svc.read_text())

    def test_openrc_service_file_exists(self):
        svc = _SETTINGS_DIR / "openrc" / "glue-sensors-detect"
        self.assertTrue(svc.is_file())
        self.assertIn("/usr/bin/glue-sensors-detect", svc.read_text())

    def test_no_systemd_in_sensors_files(self):
        files = [
            _SETTINGS_DIR / "glue-sensors-detect",
            _SETTINGS_DIR / "dinit.d" / "glue-sensors-detect",
            _SETTINGS_DIR / "runit" / "glue-sensors-detect" / "run",
            _SETTINGS_DIR / "openrc" / "glue-sensors-detect",
        ]
        for path in files:
            self.assertNotIn("systemd", path.read_text().lower(), str(path))


# ---------------------------------------------------------------------------
# nvidia.conf has NVreg_DynamicPowerManagement
# ---------------------------------------------------------------------------

class TestNvidiaConf(unittest.TestCase):

    def test_nvreg_dynamic_power_management(self):
        text = (_SETTINGS_DIR / "nvidia.conf").read_text()
        self.assertIn("NVreg_DynamicPowerManagement=0x02", text)


# ---------------------------------------------------------------------------
# PASUL 4: game-performance restore contract (trap + signal handling)
# ---------------------------------------------------------------------------

class TestGamePerformanceTrapContract(unittest.TestCase):

    def setUp(self):
        self.text = (_SETTINGS_DIR / "game-performance").read_text()

    def test_saves_profile_before_setting_performance(self):
        self.assertIn("powerprofilesctl get", self.text)

    def test_restore_on_exit_trap(self):
        self.assertIn("trap restore EXIT", self.text)

    def test_int_signal_triggers_exit(self):
        self.assertIn("INT", self.text)

    def test_term_signal_triggers_exit(self):
        self.assertIn("TERM", self.text)

    def test_restore_not_hardcoded_balanced(self):
        self.assertNotIn('"balanced"', self.text)
        self.assertNotIn("'balanced'", self.text)

    def test_restore_uses_saved_prev(self):
        self.assertIn("$prev", self.text)

    def test_fallback_when_no_ppd(self):
        self.assertIn('exec "$@"', self.text)


if __name__ == "__main__":
    unittest.main()
