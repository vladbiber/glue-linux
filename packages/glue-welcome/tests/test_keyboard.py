import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from glue_welcome import keyboard
from glue_welcome.keyboard import Config, Layout

EVDEV = """! model
  pc105           Generic 105-key PC

! layout
  us              English (US)
  ro              Romanian
  de              German

! variant
  std             ro: Romanian (standard)
  nodeadkeys      de: German (no dead keys)

! option
  grp                  Switching to another layout
"""

XORG = """Section "InputClass"
        Identifier "system-keyboard"
        MatchIsKeyboard "on"
        Option "XkbLayout" "ro,us"
        Option "XkbModel" "pc105"
        Option "XkbVariant" "std,"
        Option "XkbOptions" "grp:alt_shift_toggle,ctrl:nocaps"
EndSection
"""

PROFILE = ROOT / "data" / "profile.d" / "glue-keyboard.sh"
HELPER = ROOT / "data" / "helpers" / "keyboard-apply"


class EvdevTest(unittest.TestCase):
    def test_layouts_and_variants(self):
        items = keyboard.parse_evdev(EVDEV)
        keys = {i.key: i.name for i in items}
        self.assertEqual(keys["ro"], "Romanian")
        self.assertEqual(keys["ro(std)"], "Romanian (standard)")
        self.assertEqual(keys["de(nodeadkeys)"], "German (no dead keys)")
        self.assertNotIn("pc105", keys)
        self.assertNotIn("grp", keys)
        self.assertEqual([i.name for i in items], sorted(i.name for i in items))

    def test_search_all_words(self):
        items = keyboard.parse_evdev(EVDEV)
        self.assertEqual([i.key for i in keyboard.search(items, "roman stand")], ["ro(std)"])
        self.assertEqual(len(keyboard.search(items, "")), len(items))

    def test_missing_file_falls_back_to_us(self):
        self.assertEqual(keyboard.load_catalog(Path("/nonexistent"))[0].layout, "us")

    def test_real_evdev_when_present(self):
        if keyboard.EVDEV_LST.exists():
            keys = {i.key for i in keyboard.load_catalog()}
            self.assertIn("ro", keys)
            self.assertIn("us", keys)


class OptionsTest(unittest.TestCase):
    def test_merge_replaces_grp_keeps_rest(self):
        self.assertEqual(
            keyboard.merge_options(["ctrl:nocaps", "grp:caps_toggle"], "grp:win_space_toggle"),
            ["grp:win_space_toggle", "ctrl:nocaps"])
        self.assertEqual(keyboard.merge_options(["ctrl:nocaps"], ""), ["ctrl:nocaps"])

    def test_single_layout_drops_switch(self):
        c = Config([Layout("ro", "std")], ["grp:alt_shift_toggle", "ctrl:nocaps"])
        self.assertEqual(keyboard.xkb_strings(c), ("ro", "std", "ctrl:nocaps"))

    def test_variants_positional(self):
        c = Config([Layout("us"), Layout("ro", "std")], ["grp:alt_shift_toggle"])
        self.assertEqual(keyboard.xkb_strings(c), ("us,ro", ",std", "grp:alt_shift_toggle"))
        c = Config([Layout("us"), Layout("ro")], [])
        self.assertEqual(keyboard.xkb_strings(c)[1], "")


class XorgTest(unittest.TestCase):
    def test_parse(self):
        c = keyboard.parse_xorg(XORG)
        self.assertEqual([l.key for l in c.layouts], ["ro(std)", "us"])
        self.assertEqual(c.options, ["grp:alt_shift_toggle", "ctrl:nocaps"])
        self.assertEqual(c.model, "pc105")
        self.assertEqual(c.switch, "grp:alt_shift_toggle")

    def test_render_roundtrip(self):
        text = keyboard.render_xorg("ro,us", "std,", "grp:alt_shift_toggle", "pc105")
        c = keyboard.parse_xorg(text)
        self.assertEqual(keyboard.xkb_strings(c), ("ro,us", "std,", "grp:alt_shift_toggle"))
        self.assertNotIn("XkbVariant", keyboard.render_xorg("us", "", ""))

    def test_empty_is_us(self):
        self.assertEqual(keyboard.parse_xorg("").layouts, [Layout("us")])

    def test_valid(self):
        self.assertTrue(keyboard.valid("us,ro", ",std", "grp:alt_shift_toggle,lv3:ralt_switch"))
        for bad in ('us"', "us\nX", "us;rm", "US", "a b", "$(x)"):
            self.assertFalse(keyboard.valid(bad), bad)


class SystemWriteTest(unittest.TestCase):
    def test_write_system(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "keymaps/i386/qwerty").mkdir(parents=True)
            (d / "keymaps/i386/qwerty/ro.map.gz").write_bytes(b"")
            xorg = d / "xorg.conf.d/00-keyboard.conf"
            vcon = d / "vconsole.conf"
            vcon.write_text("KEYMAP=us\nFONT=ter-v16n\n")
            keyboard.write_system("ro,us", "std,", "grp:alt_shift_toggle", xorg, vcon,
                                  d / "keymaps")
            self.assertEqual(keyboard.xkb_strings(keyboard.current(xorg)),
                             ("ro,us", "std,", "grp:alt_shift_toggle"))
            self.assertEqual(vcon.read_text(), "KEYMAP=ro\nFONT=ter-v16n\n")
            keyboard.write_system("de", "", "", xorg, vcon, d / "keymaps")
            self.assertEqual(vcon.read_text(), "KEYMAP=ro\nFONT=ter-v16n\n")
            with self.assertRaises(ValueError):
                keyboard.write_system('us" X', "", "", xorg, vcon, d / "keymaps")

    def test_console_keymap_variant_first(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "i386/qwertz").mkdir(parents=True)
            (d / "i386/qwertz/de.map.gz").write_bytes(b"")
            (d / "i386/qwertz/de-latin1-nodeadkeys.map.gz").write_bytes(b"")
            self.assertEqual(keyboard.console_keymap("de", "nodeadkeys", d), "de")
            self.assertEqual(keyboard.console_keymap("xx", "", d), "")

    def test_helper_rejects_bad_args(self):
        for args in ([], ["", "", ""], ['us"', "", ""], ["us", "", "", "x"]):
            r = subprocess.run([sys.executable, str(HELPER), *args], capture_output=True,
                               env=dict(os.environ, PYTHONPATH=str(ROOT)))
            self.assertEqual(r.returncode, 2, args)


class CompositorTest(unittest.TestCase):
    def test_set_conf_keys(self):
        text = "blur = false\n# xkb_layout = de\nxkb_layout = us\n"
        out = keyboard.set_conf_keys(text, {"xkb_layout": "ro,us", "xkb_options": "x"})
        self.assertIn("# xkb_layout = de\n", out)
        self.assertIn("xkb_layout = ro,us\n", out)
        self.assertIn("xkb_options = x\n", out)
        self.assertEqual(out.count("xkb_layout ="), 2)

    def test_detect(self):
        d = keyboard.detect_session
        self.assertEqual(d({"GLUE_SESSION": "gluewc-noctalia"}), "gluewc")
        self.assertEqual(d({"GLUE_SESSION": "gluemac"}), "gluemac")
        self.assertEqual(d({"HYPRLAND_INSTANCE_SIGNATURE": "x"}), "hyprland")
        self.assertEqual(d({"XDG_CURRENT_DESKTOP": "KDE"}), "kde")
        self.assertEqual(d({"XDG_CURRENT_DESKTOP": "X-Cinnamon"}), "cinnamon")
        self.assertEqual(d({"DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "nvwm"}), "x11")

    def test_live_gluewc_seeds_default_config(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "share/gluewc").mkdir(parents=True)
            (d / "share/gluewc/config.def.conf").write_text("blur = true\n")
            path = d / "cfg/gluewc/config.conf"
            keyboard.write_compositor_config(path, "ro,us", "", "grp:caps_toggle",
                                             seeds=(d / "share",))
            text = path.read_text()
            self.assertTrue(text.startswith("blur = true\n"))
            self.assertIn("xkb_layout = ro,us\n", text)
            self.assertIn("xkb_options = grp:caps_toggle\n", text)

    def test_live_hyprland_and_x11(self):
        calls = []

        def runner(argv, timeout=30):
            calls.append(argv)
            return 0, "", ""

        c = Config([Layout("us"), Layout("ro", "std")], ["grp:alt_shift_toggle"])
        self.assertEqual(keyboard.apply_live(c, "hyprland", {}, runner), "")
        self.assertEqual(calls[0], ["hyprctl", "keyword", "input:kb_layout", "us,ro"])
        calls.clear()
        self.assertEqual(keyboard.apply_live(c, "x11", {}, runner), "")
        self.assertEqual(calls[0], ["setxkbmap", "-layout", "us,ro", "-variant", ",std",
                                    "-option", "", "-option", "grp:alt_shift_toggle"])

    def test_system_cancelled(self):
        c = Config([Layout("us")], [])
        self.assertEqual(keyboard.apply_system(c, lambda a, timeout=0: (126, "", "")),
                         "Authorization was cancelled.")


class ProfileTest(unittest.TestCase):
    def run_sh(self, shell, conf, extra=""):
        script = f'{extra}. "{PROFILE}"; env | grep "^XKB_" | sort'
        r = subprocess.run([shell, "-c", script], capture_output=True, text=True,
                           env={"PATH": os.environ["PATH"], "GLUE_KEYBOARD_CONF": str(conf)})
        self.assertEqual(r.stderr, "")
        return r.stdout

    def test_exports_from_conf(self):
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / "00-keyboard.conf"
            conf.write_text(XORG)
            for shell in ("sh", "bash", "zsh"):
                if not any((Path(p) / shell).exists() for p in os.environ["PATH"].split(":")):
                    continue
                out = self.run_sh(shell, conf)
                self.assertIn("XKB_DEFAULT_LAYOUT=ro,us\n", out, shell)
                self.assertIn("XKB_DEFAULT_VARIANT=std,\n", out, shell)
                self.assertIn("XKB_DEFAULT_OPTIONS=grp:alt_shift_toggle,ctrl:nocaps\n", out)
                self.assertIn("XKB_DEFAULT_MODEL=pc105\n", out)

    def test_keeps_existing_env_and_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / "00-keyboard.conf"
            conf.write_text(XORG)
            self.assertEqual(self.run_sh("sh", conf, "export XKB_DEFAULT_LAYOUT=de; "),
                             "XKB_DEFAULT_LAYOUT=de\n")
            self.assertEqual(self.run_sh("sh", Path(d) / "none"), "")

    def test_rejects_unsafe_values(self):
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / "00-keyboard.conf"
            conf.write_text('Option "XkbLayout" "us;touch x"\n')
            self.assertEqual(self.run_sh("sh", conf), "")


if __name__ == "__main__":
    unittest.main()
