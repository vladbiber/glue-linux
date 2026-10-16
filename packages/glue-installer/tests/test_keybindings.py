"""The keys every Glue desktop shares (catalog/keybindings.json, ADR-026).

The window managers' default configs come from upstream at build time, so the
packaging enforces the table: each PKGBUILD's prepare() greps the built config
for every line of it and fails the build when one is missing. These tests check
that those guard lists carry the whole table, that the catalog shows the same
keys for every session, and that glue-action does what the keys ask for.
"""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
_PACKAGES = _PKG_ROOT.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.catalog import load_catalog  # noqa: E402

_TABLE = json.loads((_PKG_ROOT / "catalog" / "keybindings.json").read_text())["shared"]
_ACTION = _PACKAGES / "glue-branding" / "glue-action"

# session -> the window manager whose config carries its keys
_SESSIONS = {"gluewc": "gluewc", "gluewc-noctalia": "gluewc", "gluewc-caelestia": "gluewc",
             "nvwm": "nvwm", "hyprland-ii": "ii"}
_PKGBUILDS = {"gluewc": "gluewc", "nvwm": "nvwm", "ii": "ii-vynx"}


def _guarded_lines(wm):
    """The lines prepare() checks after '# every line of the shared table'."""
    text = (_PACKAGES / _PKGBUILDS[wm] / "PKGBUILD").read_text()
    block = text[text.index("# every line of the shared table"):]
    block = block[block.index("for b in ") + len("for b in "):block.index("; do")]
    return shlex.split(block.replace("\\\n", " "))


class TestSharedTable(unittest.TestCase):

    def test_the_table_covers_the_agreed_keys(self):
        keys = [e["keys"] for e in _TABLE]
        for k in ("Super+Return", "Super+Space", "Super+Q", "Super+F", "Super+V",
                  "Super+E", "Super+B", "Super+1-9", "Super+Shift+1-9", "Super+L",
                  "Super+Shift+S", "Print", "Ctrl+Alt+Delete", "Super+Shift+F1"):
            self.assertIn(k, keys)
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_window_manager_enforces_every_line(self):
        for wm in _PKGBUILDS:
            guarded = _guarded_lines(wm)
            for entry in _TABLE:
                self.assertIn(entry[wm], guarded, (wm, entry["keys"]))

    def test_no_key_is_bound_twice_by_the_patches(self):
        # gluewc and nvwm: a key added by the patch must not also keep its old bind
        gluewc = (_PACKAGES / "gluewc" / "PKGBUILD").read_text()
        self.assertIn("/^bind_insert = mod+m = wm:quit$/d", gluewc)
        self.assertIn("mod+shift+b = wm:toggle_decorations", gluewc)
        for wm in ("gluewc", "nvwm"):
            text = (_PACKAGES / wm / "PKGBUILD").read_text()
            self.assertIn("s/^bind_insert = mod+q = spawn:alacritty$/bind_insert = mod+q = wm:kill/", text)
            self.assertIn("s/^bind_insert = mod+l = wm:focus_right$/", text)
        ii = (_PACKAGES / "ii-vynx" / "PKGBUILD").read_text()
        self.assertIn('[ "$(grep -c \'^hl.bind("SUPER + V"\' "$kb")" = 1 ]', ii)

    def test_catalog_lists_the_shared_keys_first(self):
        catalog = load_catalog(_PKG_ROOT / "catalog" / "catalog.json")
        sessions = {s.id: s for s in catalog.sessions}
        shared = [(e["keys"], e["action"]) for e in _TABLE]
        for sid in _SESSIONS:
            got = [(k.keys, k.action) for k in sessions[sid].keybindings][:len(shared)]
            self.assertEqual(got, shared, sid)

    def test_sessions_carry_what_the_fallbacks_run(self):
        catalog = load_catalog(_PKG_ROOT / "catalog" / "catalog.json")
        sessions = {s.id: s for s in catalog.sessions}
        # the catalog may not name sway* (removed session): gluewc depends on swaylock
        self.assertIn("'swaylock'", (_PACKAGES / "gluewc" / "PKGBUILD").read_text())
        for sid in ("gluewc", "gluewc-noctalia", "gluewc-caelestia"):
            self.assertIn("gluewc", sessions[sid].packages, sid)
        for pkg in ("i3lock", "maim", "xclip", "rofi"):
            self.assertIn(pkg, sessions["nvwm"].packages, pkg)


class TestGlueAction(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "calls"
        for tool in ("qs", "noctalia", "rofi", "swaylock", "i3lock", "loginctl", "nautilus",
                     "xdg-settings", "gtk-launch", "firefox", "alacritty", "pkill",
                     "gluewc-msg", "notify-send", "glue-apps", "xdg-open", "slurp", "grim",
                     "wl-copy", "maim", "xclip"):
            script = self.bin / tool
            script.write_text('#!/bin/sh\nprintf "%s %s\\n" "${0##*/}" "$*" >> "$CALLS"\n'
                              'case "${0##*/}" in pkill) exit 1 ;; slurp) echo "0,0 10x10" ;; '
                              'xdg-settings) [ -n "${BROWSER_DESKTOP:-}" ] && echo "$BROWSER_DESKTOP" ;; esac\n'
                              'exit 0\n')
            script.chmod(0o755)
        self.conf = self.root / "session.conf"
        self.conf.write_text("[session]\nshell.gluewc=glueqs\nshell.gluewc-noctalia=noctalia\n"
                             "shell.gluewc-caelestia=caelestia\nshell.hyprland-ii=ii\n")

    def run_action(self, action, session, wayland=True, **extra):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.root),
               "CALLS": str(self.calls), "GLUE_SESSION": session,
               "GLUE_SESSION_CONF": str(self.conf), **extra}
        if wayland:
            env["WAYLAND_DISPLAY"] = "wayland-1"
        result = subprocess.run(["sh", str(_ACTION), action], env=env,
                                capture_output=True, text=True)
        calls = self.calls.read_text().splitlines() if self.calls.exists() else []
        self.calls.unlink(missing_ok=True)
        return result, calls

    def test_syntax_and_installed(self):
        result = subprocess.run(["sh", "-n", str(_ACTION)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        pkgbuild = (_PACKAGES / "glue-branding" / "PKGBUILD").read_text()
        self.assertIn('install -Dm755 "$srcdir/glue-action" "$pkgdir/usr/bin/glue-action"', pkgbuild)

    def test_every_spawned_action_exists(self):
        text = _ACTION.read_text()
        for wm in ("gluewc", "nvwm"):
            for line in _guarded_lines(wm):
                if "spawn:glue-action " in line:
                    action = line.split("spawn:glue-action ")[1]
                    self.assertIn(f"\n{action})\n", text, (wm, action))

    def test_launcher_uses_the_shell(self):
        self.assertEqual(self.run_action("launcher", "gluewc-caelestia")[1],
                         ["qs -c caelestia ipc call drawers toggle launcher"])
        self.assertEqual(self.run_action("launcher", "gluewc-noctalia")[1],
                         ["noctalia msg panel-toggle launcher"])
        calls = self.run_action("launcher", "gluewc")[1]
        self.assertEqual(calls[-1], "rofi -show drun")

    def test_lock_uses_the_shell_then_swaylock_or_i3lock(self):
        self.assertEqual(self.run_action("lock", "gluewc-caelestia")[1],
                         ["qs -c caelestia ipc call lock lock"])
        self.assertEqual(self.run_action("lock", "gluewc-noctalia")[1],
                         ["noctalia msg session lock"])
        self.assertEqual(self.run_action("lock", "gluewc")[1], ["swaylock -f -c 2B2F33"])
        self.assertEqual(self.run_action("lock", "nvwm", wayland=False)[1],
                         ["i3lock -c 2B2F33"])

    def test_session_menu(self):
        self.assertEqual(self.run_action("session", "gluewc-caelestia")[1],
                         ["qs -c caelestia ipc call drawers toggle session"])
        self.assertEqual(self.run_action("session", "gluewc-noctalia")[1],
                         ["noctalia msg panel-toggle session"])
        calls = self.run_action("session", "gluewc")[1]
        self.assertTrue(calls[-1].startswith("rofi -dmenu"), calls)

    def test_files_browser_terminal(self):
        self.assertEqual(self.run_action("files", "gluewc")[1], ["nautilus --new-window"])
        calls = self.run_action("browser", "gluewc", BROWSER_DESKTOP="firefox.desktop")[1]
        self.assertEqual(calls, ["xdg-settings get default-web-browser", "gtk-launch firefox"])
        self.assertEqual(self.run_action("terminal", "nvwm", wayland=False)[1], ["alacritty "])

    def test_region_screenshot_per_display_server(self):
        calls = self.run_action("screenshot-region", "gluewc")[1]
        self.assertEqual(calls[0], "slurp ")
        self.assertIn("wl-copy -t image/png", calls)
        calls = self.run_action("screenshot-region", "nvwm", wayland=False)[1]
        self.assertIn("maim -s", calls)
        self.assertIn("xclip -selection clipboard -t image/png", calls)

    def test_unknown_action_prints_usage(self):
        result, _ = self.run_action("nope", "gluewc")
        self.assertEqual(result.returncode, 2)
        self.assertIn("launcher", result.stderr)


if __name__ == "__main__":
    unittest.main()
