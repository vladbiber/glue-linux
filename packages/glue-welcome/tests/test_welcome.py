import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from glue_welcome.info import parse_sensors
from glue_welcome.keybindings import binding_rows, detect_session, order_rows, session_name


class TestKeybindings(unittest.TestCase):
    def test_current_session_includes_selected_shell(self):
        data = {
            "sessions": [{"id": "gluewc", "keybindings": [
                {"keys": "Super+F1", "action": "Audio"}]}],
            "shells": [{"id": "noctalia", "keybindings": [
                {"keys": "Super+n", "action": "Notifications"}]}],
        }
        session, rows = binding_rows(data, "gluewc", {"shell.gluewc": "noctalia"})
        self.assertEqual(session, "gluewc")
        self.assertEqual(rows, [("Super+F1", "Audio"), ("Super+n", "Notifications")])

    def test_open_close_and_overview_come_first(self):
        rows = [("Super+Return", "Open terminal"), ("Super+1-9", "Switch workspace"),
                ("Super", "Open the overview (tap Super alone)"),
                ("Super+c", "Close focused window"),
                ("Super+Space", "Open application launcher")]
        self.assertEqual([keys for keys, _action in order_rows(rows)],
                         ["Super+Space", "Super+c", "Super", "Super+Return", "Super+1-9"])

    def test_detects_running_window_manager(self):
        ids = ["gluewc", "nvwm", "kde-plasma", "cinnamon"]
        self.assertEqual(detect_session(ids, {"XDG_CURRENT_DESKTOP": "KDE"}, set()), "kde-plasma")
        self.assertEqual(detect_session(ids, {"XDG_CURRENT_DESKTOP": "X-Cinnamon"}, set()),
                         "cinnamon")
        self.assertEqual(detect_session(ids, {}, {"bash", "nvwm"}), "nvwm")
        self.assertIsNone(detect_session(ids, {}, {"bash"}))
        self.assertEqual(detect_session(ids, {"XDG_CURRENT_DESKTOP": "nvwm",
                                              "DESKTOP_SESSION": "gluewc"}, set()), "nvwm")

    def test_catalog_arrows_and_overview(self):
        import json
        catalog = json.loads((ROOT.parent / "glue-installer/catalog/catalog.json").read_text())
        gluewc = next(s for s in catalog["sessions"] if s["id"] == "gluewc")
        keys = [k["keys"] for k in gluewc["keybindings"]]
        self.assertIn("Super+←↑↓→", keys)
        self.assertIn("Super", keys)
        self.assertFalse(any("h/j/k/l" in k for k in keys))


class TestSessionName(unittest.TestCase):
    def test_name_from_the_login_entry(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            Path(d, "kde-plasma.desktop").write_text("[Desktop Entry]\nName=KDE Plasma\n")
            self.assertEqual(session_name("kde-plasma", Path(d)), "KDE Plasma")
            self.assertEqual(session_name("nvwm", Path(d)), "nvwm")


class TestInfo(unittest.TestCase):
    def test_sensor_json_returns_highest_plausible_temperature(self):
        output = '{"coretemp":{"Core 0":{"temp2_input":51.5}},"gpu":{"temp1_input":67}}'
        self.assertEqual(parse_sensors(output), "67 °C")
        self.assertEqual(parse_sensors("bad"), "Unavailable")


class TestPackaging(unittest.TestCase):
    def test_files(self):
        for rel in ("PKGBUILD", "glue-welcome", "data/applications/org.glue.Welcome.desktop",
                    "data/autostart/org.glue.Welcome.desktop", "data/icons/org.glue.Welcome.svg"):
            self.assertTrue((ROOT / rel).is_file(), rel)
        autostart = (ROOT / "data/autostart/org.glue.Welcome.desktop").read_text()
        self.assertIn("Exec=glue-welcome --autostart", autostart)
        self.assertIn("'glue-apps'", (ROOT / "PKGBUILD").read_text())


if __name__ == "__main__":
    unittest.main()
