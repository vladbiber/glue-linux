"""
Tests for the glue-settings integration (Faza 1.1).

Covers: plan inclusion rules for the glue-settings package and its
glue-tuning oneshot, sysctl key non-duplication between the package's
70-glue.conf and any planned sysctl file, the exact ROADMAP 1.1 sysctl
values, PKGBUILD source completeness, and the no-systemd guarantee.
"""

import re
import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.plan import Selection, resolve_plan

from tests.test_plan import _make_catalog

_REPO_ROOT = _PKG_ROOT.parent.parent
_SETTINGS_DIR = _REPO_ROOT / "packages" / "glue-settings"

_INITS = ("dinit", "runit", "openrc")

# ROADMAP 1.1 — the exact sysctl contract of 70-glue.conf
_EXPECTED_SYSCTL = {
    "vm.swappiness": "100",
    "vm.vfs_cache_pressure": "50",
    "vm.dirty_bytes": "268435456",
    "vm.dirty_background_bytes": "67108864",
    "vm.dirty_writeback_centisecs": "1500",
    "vm.page-cluster": "0",
    "kernel.nmi_watchdog": "0",
    "kernel.unprivileged_userns_clone": "1",
    "kernel.kptr_restrict": "2",
    "net.core.netdev_max_backlog": "4096",
    "fs.file-max": "2097152",
    "kernel.sysrq": "1",
    "kernel.printk": "3 3 3 3",
}


def _parse_sysctl(text: str) -> dict:
    """key=value / key = value lines -> dict; comments and blanks skipped."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def _sel(sessions, gaming, init_id="dinit"):
    return Selection("k-main", init_id, sessions, {}, [], gaming, False)


class TestPlanInclusion(unittest.TestCase):

    def setUp(self):
        self.catalog = _make_catalog()

    def test_sessions_include_glue_settings_on_all_inits(self):
        for init_id in _INITS:
            plan = resolve_plan(self.catalog, _sel(["wm-bare"], False, init_id))
            self.assertIn("glue-settings", plan.packages, init_id)
            self.assertIn("glue-tuning", plan.services, init_id)

    def test_gaming_without_sessions_includes_glue_settings(self):
        for init_id in _INITS:
            plan = resolve_plan(self.catalog, _sel([], True, init_id))
            self.assertIn("glue-settings", plan.packages, init_id)
            self.assertIn("glue-tuning", plan.services, init_id)

    def test_server_like_install_excludes_glue_settings(self):
        for init_id in _INITS:
            plan = resolve_plan(self.catalog, _sel([], False, init_id))
            self.assertNotIn("glue-settings", plan.packages, init_id)
            self.assertNotIn("glue-tuning", plan.services, init_id)

    def test_no_init_suffixed_glue_settings_package(self):
        # arch=any ships all three init scripts itself — no glue-settings-<init>
        for init_id in _INITS:
            plan = resolve_plan(self.catalog, _sel(["wm-bare"], True, init_id))
            self.assertNotIn(f"glue-settings-{init_id}", plan.packages)

    def test_quiet_sysctl_fallback_only_without_glue_settings(self):
        quiet = "/etc/sysctl.d/20-glue-quiet.conf"
        with_pkg = resolve_plan(self.catalog, _sel(["wm-bare"], False))
        self.assertNotIn(quiet, [f.path for f in with_pkg.files])
        without_pkg = resolve_plan(self.catalog, _sel([], False))
        self.assertIn(quiet, [f.path for f in without_pkg.files])


class TestSysctlNoDuplication(unittest.TestCase):

    def setUp(self):
        self.catalog = _make_catalog()
        text = (_SETTINGS_DIR / "70-glue.conf").read_text()
        self.pkg_keys = set(_parse_sysctl(text))

    def _planned_sysctl_keys(self, plan) -> set:
        keys = set()
        for pf in plan.files:
            if "/sysctl.d/" in pf.path:
                keys.update(_parse_sysctl(pf.content))
        return keys

    def test_no_key_defined_in_package_and_plan_at_once(self):
        # every plan shape: with sessions, gaming-only, and server-like
        selections = [
            _sel(["wm-bare"], False),
            _sel(["wm-bare", "wm-shell"], True),
            _sel([], True),
            _sel([], False),
        ]
        selections[1].shell_choice = {"wm-shell": "shell-a"}
        for sel in selections:
            plan = resolve_plan(self.catalog, sel)
            planned = self._planned_sysctl_keys(plan)
            if "glue-settings" in plan.packages:
                dup = self.pkg_keys & planned
                self.assertFalse(
                    dup, f"sysctl keys defined twice: {sorted(dup)}"
                )


class TestPackageContent(unittest.TestCase):

    def test_70_glue_conf_matches_roadmap_exactly(self):
        parsed = _parse_sysctl((_SETTINGS_DIR / "70-glue.conf").read_text())
        self.assertEqual(parsed, _EXPECTED_SYSCTL)

    def test_pkgbuild_source_files_exist(self):
        pkgbuild = (_SETTINGS_DIR / "PKGBUILD").read_text()
        match = re.search(r"source=\(([^)]*)\)", pkgbuild)
        self.assertIsNotNone(match, "PKGBUILD has no source=() array")
        names = re.findall(r"'([^']+)'", match.group(1))
        self.assertTrue(names, "source=() is empty")
        for name in names:
            self.assertTrue(
                (_SETTINGS_DIR / name).is_file(), f"missing source: {name}"
            )

    def test_init_service_files_exist_and_run_thp_tune(self):
        services = [
            _SETTINGS_DIR / "dinit.d" / "glue-tuning",
            _SETTINGS_DIR / "runit" / "glue-tuning" / "run",
            _SETTINGS_DIR / "openrc" / "glue-tuning",
        ]
        for svc in services:
            self.assertTrue(svc.is_file(), f"missing service file: {svc}")
            self.assertIn("/usr/bin/glue-thp-tune", svc.read_text())

    def test_thp_tune_values_and_missing_file_guards(self):
        text = (_SETTINGS_DIR / "glue-thp-tune").read_text()
        self.assertIn("defer+madvise", text)
        self.assertIn("max_ptes_none", text)
        self.assertIn("409", text)
        # every sysfs write is guarded by a writability test
        self.assertIn('[ -w "$thp/defrag" ]', text)
        self.assertIn('[ -w "$thp/khugepaged/max_ptes_none" ]', text)

    def test_game_performance_contract(self):
        text = (_SETTINGS_DIR / "game-performance").read_text()
        self.assertNotIn("systemd-inhibit", text)
        self.assertIn("powerprofilesctl get", text)
        self.assertIn("powerprofilesctl set performance", text)
        self.assertIn("trap restore EXIT", text)
        # runs the command directly when powerprofilesctl is missing
        self.assertIn('exec "$@"', text)

    def test_no_systemd_anywhere_in_package(self):
        offenders = []
        for path in sorted(_SETTINGS_DIR.rglob("*")):
            if path.is_file() and "systemd" in path.read_text().lower():
                offenders.append(str(path))
        self.assertEqual(offenders, [])

    def test_make_iso_builds_glue_settings(self):
        text = (_REPO_ROOT / "scripts" / "make-iso.sh").read_text()
        build_line = next(
            line for line in text.splitlines()
            if line.strip().startswith("for pkg in")
        )
        self.assertIn("glue-settings", build_line)

    def test_all_package_files_under_500_lines(self):
        for path in sorted(_SETTINGS_DIR.rglob("*")):
            if path.is_file():
                lines = path.read_text().count("\n")
                self.assertLess(lines, 500, f"{path} has {lines} lines")


if __name__ == "__main__":
    unittest.main()
