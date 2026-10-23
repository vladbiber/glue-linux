"""
Tests for the glue-settings integration.

Covers: plan inclusion rules for the glue-settings package and its
glue-tuning oneshot, sysctl key non-duplication between the package's
70-glue.conf and any planned sysctl file, the exact sysctl
values, PKGBUILD source completeness, and the no-systemd guarantee.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.plan import Selection, resolve_plan

from tests.test_plan import _make_catalog

_REPO_ROOT = _PKG_ROOT.parent.parent
_SETTINGS_DIR = _REPO_ROOT / "packages" / "glue-settings"

_INITS = ("dinit", "runit", "openrc")

# - the exact sysctl contract of 70-glue.conf
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
        # a leading '-' only tells sysctl to ignore a failure
        out[key.strip().lstrip("-")] = value.strip()
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

    def test_net_udev_replay_only_on_dinit(self):
        for init_id in _INITS:
            plan = resolve_plan(self.catalog, _sel(["wm-bare"], False, init_id))
            self.assertEqual("glue-net-udev" in plan.services,
                             init_id == "dinit", init_id)

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
        # arch=any ships all three init scripts itself - no glue-settings-<init>
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

    def test_70_glue_conf_exact_contract(self):
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

    def test_net_udev_replay_dinit_service(self):
        svc = (_SETTINGS_DIR / "dinit.d" / "glue-net-udev").read_text()
        self.assertIn("/usr/lib/glue/net-udev-replay", svc)
        self.assertIn("waits-for = udevd", svc)
        self.assertIn("before = pre-network.target", svc)
        text = (_SETTINGS_DIR / "net-udev-replay").read_text()
        self.assertIn("--subsystem-match=net --initialized-nomatch --action=add", text)
        self.assertIn("udevadm settle", text)
        self.assertNotIn("set -e", text)

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

    def _precache(self, cfg, *args):
        stub = Path(cfg).parent.parent.parent / "bin"
        stub.mkdir(exist_ok=True)
        (stub / "pgrep").write_text("#!/bin/sh\nexit 1\n")
        (stub / "pgrep").chmod(0o755)
        return subprocess.run(
            ["sh", str(_SETTINGS_DIR / "glue-steam-precache"), *args],
            capture_output=True, text=True, timeout=20,
            env={"PATH": f"{stub}:/usr/bin:/bin", "STEAM_CONFIG": cfg})

    def test_steam_precache_auto_seeds_a_new_steam(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = f"{tmp}/Steam/config/config.vdf"
            self.assertEqual(self._precache(cfg, "auto").returncode, 0)
            self.assertEqual(self._precache(cfg, "status").stdout, "off\n")
            self.assertIn('"InstallConfigStore"', Path(cfg).read_text())

    def test_steam_precache_auto_keeps_a_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = f"{tmp}/Steam/config/config.vdf"
            self._precache(cfg, "auto")
            self._precache(cfg, "on")
            before = Path(cfg).read_text()
            self._precache(cfg, "auto")
            self.assertEqual(Path(cfg).read_text(), before)
            self.assertEqual(self._precache(cfg, "status").stdout, "on\n")

    def test_steam_precache_auto_adds_the_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp, "Steam", "config", "config.vdf")
            cfg.parent.mkdir(parents=True)
            cfg.write_text('"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n'
                           '\t\t{\n\t\t\t"Steam"\n\t\t\t{\n\t\t\t\t"x"\t\t"1"\n'
                           '\t\t\t}\n\t\t}\n\t}\n}\n')
            self.assertEqual(self._precache(str(cfg), "auto").returncode, 0)
            self.assertEqual(self._precache(str(cfg), "status").stdout, "off\n")
            self.assertIn('"x"\t\t"1"', cfg.read_text())

    def test_game_watch_contract(self):
        text = (_SETTINGS_DIR / "glue-game-watch").read_text()
        self.assertEqual(subprocess.run(["sh", "-n", str(_SETTINGS_DIR / "glue-game-watch")])
                         .returncode, 0)
        self.assertIn("flock -n 9 || exit 0", text)
        # pre-caching and the Flatpak GL extension only on the NVIDIA driver
        self.assertIn("if [ -r /sys/module/nvidia/version ]; then", text)
        self.assertLess(text.index("/sys/module/nvidia/version ]"),
                        text.index("has steam && glue-steam-precache auto"))
        self.assertIn("flatpak --user install", text)
        self.assertNotIn("sudo", text)

    def test_game_watch_registers_each_steam_game_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "bin").mkdir()
            (tmp / "run").mkdir()
            (tmp / "home" / ".steam").mkdir(parents=True)
            for name, body in (("steam", "exit 0"),
                               ("gamemoded", 'echo "$@" >> "$HOME/log"')):
                (tmp / "bin" / name).write_text(f"#!/bin/sh\n{body}\n")
                (tmp / "bin" / name).chmod(0o755)
            steam = subprocess.Popen(["sleep", "30"])
            (tmp / "home" / ".steam" / "steam.pid").write_text(f"{steam.pid}\n")
            argv0 = f"{tmp}/Steam/ubuntu12_32/reaper SteamLaunch AppId=730 -- game"
            game = subprocess.Popen([argv0, "30"], executable="/usr/bin/sleep")
            other = subprocess.Popen(["/x/reaper SteamLaunch AppId=1", "30"],
                                     executable="/usr/bin/sleep")
            env = {"PATH": f"{tmp}/bin:/usr/bin:/bin", "HOME": str(tmp / "home"),
                   "XDG_RUNTIME_DIR": str(tmp / "run")}
            watch = subprocess.Popen(["sh", str(_SETTINGS_DIR / "glue-game-watch")], env=env)
            try:
                time.sleep(1.5)
                second = subprocess.run(["sh", str(_SETTINGS_DIR / "glue-game-watch")],
                                        env=env, timeout=10)
                self.assertEqual(second.returncode, 0)
                self.assertEqual((tmp / "home" / "log").read_text(),
                                 f"--request={game.pid}\n")
            finally:
                for p in (watch, game, other, steam):
                    p.kill()
                    p.wait()

    def _regdom(self, zone, zonetab="/usr/share/zoneinfo/zone.tab"):
        with tempfile.TemporaryDirectory() as tmp:
            lt = Path(tmp, "localtime")
            lt.symlink_to(f"../usr/share/zoneinfo/{zone}")
            tab = Path(tmp, "zone.tab")
            tab.write_text("# comment\nRO\t+4425+02606\tEurope/Bucharest\n"
                           "US\t+404251-0740023\tAmerica/New_York\n"
                           "DE\t+5230+01322\tEurope/Berlin\n")
            return subprocess.run(
                ["sh", str(_SETTINGS_DIR / "glue-regdom")],
                capture_output=True, text=True, timeout=10,
                env={"PATH": "/usr/bin:/bin", "GLUE_LOCALTIME": str(lt),
                     "GLUE_ZONETAB": str(tab), "GLUE_REGDOM_DRYRUN": "1"})

    def test_regdom_country_from_timezone(self):
        for zone, cc in (("Europe/Bucharest", "RO\n"), ("America/New_York", "US\n"),
                         ("posix/Europe/Berlin", "DE\n"), ("UTC", ""), ("Etc/UTC", "")):
            res = self._regdom(zone)
            self.assertEqual((res.returncode, res.stdout), (0, cc), zone)

    def test_regdom_runs_first_in_glue_tuning(self):
        for svc in (_SETTINGS_DIR / "dinit.d" / "glue-tuning",
                    _SETTINGS_DIR / "runit" / "glue-tuning" / "run",
                    _SETTINGS_DIR / "openrc" / "glue-tuning"):
            text = svc.read_text()
            self.assertIn("/usr/bin/glue-regdom", text, svc)
            self.assertLess(text.index("/usr/bin/glue-regdom"),
                            text.index("/usr/bin/glue-thp-tune"), svc)

    def test_wifi_powersave_and_bluetooth_defaults(self):
        pkgbuild = (_SETTINGS_DIR / "PKGBUILD").read_text()
        self.assertIn("'iw'", pkgbuild)
        self.assertIn("/usr/lib/NetworkManager/conf.d/50-glue-wifi-powersave.conf", pkgbuild)
        self.assertIn("/usr/share/wireplumber/wireplumber.conf.d/50-glue-bluetooth.conf",
                      pkgbuild)
        nm = (_SETTINGS_DIR / "50-glue-wifi-powersave.conf").read_text()
        self.assertIn("[connection]\n", nm)
        self.assertIn("wifi.powersave = 2", nm)
        wp = (_SETTINGS_DIR / "50-glue-bluetooth.conf").read_text()
        self.assertRegex(wp, r"wireplumber\.settings = \{\s*"
                             r"bluetooth\.autoswitch-to-headset-profile = false\s*\}")

    def test_netlog_records_link_changes_only(self):
        self.assertIn("/etc/NetworkManager/dispatcher.d/60-glue-netlog",
                      (_SETTINGS_DIR / "PKGBUILD").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp, "net.log")
            for event, gw in (("up", "192.168.1.1"), ("pre-up", "x"), ("dhcp4-change", "")):
                subprocess.run(["sh", str(_SETTINGS_DIR / "60-glue-netlog"), "eth0", event],
                               check=True, timeout=10,
                               env={"PATH": "/usr/bin:/bin", "GLUE_NETLOG": str(log),
                                    "IP4_GATEWAY": gw})
            lines = [l for l in log.read_text().splitlines() if not l.startswith(" ")]
        self.assertEqual(len(lines), 2)
        self.assertIn("eth0 up gw=192.168.1.1", lines[0])
        self.assertIn("eth0 dhcp4-change gw=-", lines[1])

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
