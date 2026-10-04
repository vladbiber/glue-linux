"""Live tty1 choice (roadmap 10.1): decide() table, script parity, profile wiring."""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.catalog import load_catalog
from glue_installer.clone import AUTOLOGIN_ARG, LIVE_ONLY_FILES, cleanup_script
from glue_installer.live_session import DESKTOP, TUI, decide
from glue_installer.plan_greeter import (
    _SHELL_AUTOSTART_TEMPLATE, _session_wrapper_content,
)

_REPO = _PKG_ROOT.parent.parent
_OVERLAY = _REPO / "iso-profile" / "glue" / "root-overlay"
_SCRIPT = _OVERLAY / "usr" / "local" / "bin" / "glue-live-session"
_PROFILE_D = _OVERLAY / "etc" / "profile.d" / "glue-live.sh"
_AGETTY = _OVERLAY / "etc" / "runit" / "sv" / "agetty-tty1" / "conf"
_LIVE_CONF = _OVERLAY / "usr" / "share" / "glue" / "live" / "gluewc-live.conf"
_PROFILE = _REPO / "iso-profile" / "glue" / "profile.yaml"
_CARD = ["/dev/dri/card0"]


class TestDecide(unittest.TestCase):
    def test_happy_path(self):
        c = decide("quiet splash", _CARD, {}, True)
        self.assertEqual(c.kind, DESKTOP)
        self.assertTrue(c.shell)

    def test_glue_tui_word(self):
        self.assertEqual(decide("quiet glue.tui", _CARD, {}, True).kind, TUI)

    def test_substring_is_not_the_flag(self):
        for cmd in ("glue.tuix", "xglue.tui", "foo=glue.tui", "console=tui",
                    "glue.tui=1"):
            self.assertEqual(decide(cmd, _CARD, {}, True).kind, DESKTOP, cmd)

    def test_no_kms(self):
        self.assertEqual(decide("quiet", [], {}, True).kind, TUI)
        self.assertEqual(decide("quiet", [""], {}, True).kind, TUI)

    def test_noauto(self):
        self.assertEqual(decide("", _CARD, {"GLUE_NOAUTO": "1"}, True).kind, TUI)

    def test_empty_noauto_is_unset(self):
        self.assertEqual(decide("", _CARD, {"GLUE_NOAUTO": ""}, True).kind, DESKTOP)

    def test_reason_is_short_and_set(self):
        for args in (("glue.tui", _CARD, {}), ("", [], {}),
                     ("", _CARD, {"GLUE_NOAUTO": "1"}), ("", _CARD, {})):
            r = decide(*args, True).reason
            self.assertTrue(0 < len(r) < 60)

    def test_shell_missing_still_desktop_without_bar(self):
        c = decide("", _CARD, {}, False)
        self.assertEqual((c.kind, c.shell), (DESKTOP, False))


class TestScriptParity(unittest.TestCase):
    def _run(self, cmdline, cards, noauto):
        with tempfile.TemporaryDirectory() as d:
            cf = Path(d, "cmdline")
            cf.write_text(cmdline + "\n")
            dri = Path(d, "dri")
            dri.mkdir()
            for c in cards:
                (dri / c).touch()
            env = {"PATH": os.environ["PATH"], "GLUE_CMDLINE_FILE": str(cf),
                   "GLUE_DRI_DIR": str(dri)}
            if noauto is not None:
                env["GLUE_NOAUTO"] = noauto
            out = subprocess.run(["sh", str(_SCRIPT), "--decide"], env=env,
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        kind, _, reason = out.stdout.strip().partition(" ")
        return kind, reason

    def test_same_rules_as_decide(self):
        cases = [
            ("quiet", ["card0"], None), ("quiet glue.tui", ["card0"], None),
            ("glue.tuix foo=glue.tui", ["card0"], None), ("quiet", [], None),
            ("quiet", ["renderD128"], None), ("quiet", ["card0", "card1"], None),
            ("quiet", ["card0"], "1"), ("quiet", ["card0"], ""),
            ("glue.tui", [], "1"), ("", [], None),
        ]
        for cmdline, cards, noauto in cases:
            env = {"GLUE_NOAUTO": noauto} if noauto is not None else {}
            drm = [f"/dev/dri/{c}" for c in cards if c.startswith("card")]
            want = decide(cmdline, drm, env, True)
            got = self._run(cmdline, cards, noauto)
            self.assertEqual(got, (want.kind, want.reason), (cmdline, cards, noauto))

    def test_tui_exits_10_without_starting_anything(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"PATH": os.environ["PATH"], "GLUE_CMDLINE_FILE": "/dev/null",
                   "GLUE_DRI_DIR": d}
            r = subprocess.run(["sh", str(_SCRIPT)], env=env, capture_output=True,
                               timeout=20)
        self.assertEqual(r.returncode, 10)

    def test_script_executable_posix_sh(self):
        self.assertTrue(os.access(_SCRIPT, os.X_OK))
        self.assertTrue(_SCRIPT.read_text().startswith("#!/bin/sh\n"))
        r = subprocess.run(["sh", "-n", str(_SCRIPT)], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestWrapperParity(unittest.TestCase):
    """The static --inner block must keep the essentials of the installed wrapper."""

    def setUp(self):
        self.script = _SCRIPT.read_text()
        cat = load_catalog(_PKG_ROOT / "catalog" / "catalog.json")
        sess = {s.id: s for s in cat.sessions}["gluewc"]
        self.wrapper = _session_wrapper_content(sess, "glueqs", "glueqs")

    def test_polling_autostart_block_identical(self):
        block = _SHELL_AUTOSTART_TEMPLATE.format(shell_cmd="glueqs")
        self.assertIn(block, self.script)

    def test_essential_lines(self):
        for tool in ("pipewire", "wireplumber", "pipewire-pulse"):
            self.assertIn(f"start_once {tool}", self.script)
            self.assertIn(f"{tool} &", self.wrapper)
        # live: the graphical installer first, Welcome after it closes
        self.assertIn("(wait_wayland && { command -v glue-install-gui", self.script)
        self.assertIn("exec glue-welcome --autostart; }) &", self.script)
        self.assertLess(self.script.index("glue-install-gui"),
                        self.script.index("exec glue-welcome --autostart"))
        self.assertIn("exec glue-welcome --autostart) &", self.wrapper)
        self.assertIn("(wait_wayland && command -v glue-welcome", self.wrapper)
        polkit = re.search(r"/usr/lib/polkit-gnome/\S+", self.wrapper).group(0)
        self.assertIn(polkit, self.script)
        self.assertIn("exec gluewc-session", self.wrapper)
        self.assertIn("exec gluewc-session", self.script)
        # live: plain sh, a login shell would re-source profile.d/glue-live.sh and loop
        self.assertIn('dbus-run-session -- sh "$0" --inner', self.script)
        self.assertNotIn("sh -l", self.script)
        self.assertIn("export GLUE_LIVE_SESSION=1", self.script)
        self.assertIn('dbus-run-session -- sh -l "$0" --inner', self.wrapper)
        self.assertIn("/usr/bin/glue-wallpaper-init", self.script)


class TestNoReentry(unittest.TestCase):
    """The inner session must not start a second live session (VM boot loop, 2026-10-04)."""

    def _run_hook(self, env_extra):
        import os, subprocess, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "started")
            for name, body in (("tty", "echo /dev/tty1"),
                               ("glue-live-session", f"touch {marker}; exit 10"),
                               ("glue-install", "exit 0"), ("clear", "exit 0"),
                               ("sudo", "exit 0")):
                path = os.path.join(tmp, name)
                with open(path, "w") as f:
                    f.write(f"#!/bin/sh\n{body}\n")
                os.chmod(path, 0o755)
            env = {"PATH": f"{tmp}:/usr/bin:/bin", "HOME": tmp, **env_extra}
            subprocess.run(["sh", "-c", f". {_PROFILE_D}"], env=env, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            return os.path.exists(marker)

    def test_tty1_login_starts_session(self):
        self.assertTrue(self._run_hook({}))

    def test_inside_session_does_not_start_again(self):
        self.assertFalse(self._run_hook({"GLUE_LIVE_SESSION": "1"}))


class TestProfileWiring(unittest.TestCase):
    def test_agetty_autologin_glue_and_clone_strips_it(self):
        conf = _AGETTY.read_text()
        self.assertIn(AUTOLOGIN_ARG, conf)
        self.assertEqual(AUTOLOGIN_ARG, "--autologin glue")
        self.assertNotIn("--autologin root", conf)
        self.assertIn(f"sed -i 's/ {AUTOLOGIN_ARG}//' /etc/runit/sv/agetty-tty1/conf",
                      cleanup_script())
        # what the sed removes must be exactly what the conf contains
        stripped = conf.replace(f" {AUTOLOGIN_ARG}", "")
        self.assertNotIn("autologin", stripped)

    def test_live_conf_binds_welcome(self):
        text = _LIVE_CONF.read_text()
        self.assertIn("bind_insert = mod+shift+F1 = spawn:glue-welcome\n", text)
        self.assertIn("gluewc-live.conf", _SCRIPT.read_text())

    def test_default_config_untouched_by_live_conf(self):
        # the fragment is appended to the stock config, never replaces it
        script = _SCRIPT.read_text()
        self.assertRegex(script, r'cat "\$dir/config\.def\.conf" "\$LIVE_CONF"')

    def test_live_files_removed_from_clone(self):
        self.assertIn("/usr/local/bin/glue-live-session", LIVE_ONLY_FILES)
        self.assertIn("/usr/share/glue/live/gluewc-live.conf", LIVE_ONLY_FILES)

    def test_greetd_not_enabled_on_live(self):
        text = _PROFILE.read_text()
        m = re.search(r"^live-session:\n(.*?)^rootfs:", text, re.S | re.M)
        self.assertIsNotNone(m)
        self.assertNotIn("greetd", m.group(1))
        # no service list anywhere in the profile may enable greetd
        for blk in re.finditer(r"^\s*(?:services|user-services):\n((?:\s+- .*\n)+)",
                               text, re.M):
            self.assertNotIn("greetd", blk.group(1))
        self.assertFalse((_OVERLAY / "etc" / "runit" / "runsvdir").exists())

    def test_scripts_never_start_greetd(self):
        for p in (_SCRIPT, _PROFILE_D):
            self.assertNotIn("greetd", p.read_text(), p.name)
            self.assertNotIn("greetd", p.read_text().lower())

    def test_no_text_installer_on_live(self):
        # user request 2026-10-04: the live ISO opens the GUI installer only
        text = _PROFILE_D.read_text()
        # GLUE_NOAUTO makes glue-live-session pick the no-desktop path, so the
        # hook must never set it before starting the session (VM boot 2026-10-04)
        self.assertNotIn("export GLUE_NOAUTO", text)
        self.assertLess(text.index("glue-live-session\n"), text.index("export GLUE_LIVE_TRIED=1"))
        self.assertNotIn("glue-install\n", text)
        self.assertNotIn("as_root", text)
        self.assertIn("glue-live-session", text)
        self.assertIn("no /dev/dri/card*", text)

    def test_no_artix_in_new_texts(self):
        for p in (_SCRIPT, _LIVE_CONF, _AGETTY, _PROFILE_D):
            self.assertNotIn("artix", p.read_text().lower(), p.name)

    def test_file_sizes(self):
        for p in (_SCRIPT, _LIVE_CONF, _PKG_ROOT / "glue_installer" / "live_session.py",
                  Path(__file__)):
            self.assertLess(len(p.read_text().splitlines()), 500, p.name)


if __name__ == "__main__":
    unittest.main()
