import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from glue_welcome import network
from glue_welcome.network import Network


class FakeRunner:
    def __init__(self, replies=None, tmpdir=None):
        self.replies = replies or {}
        self.calls = []
        self.files = {}

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        assert timeout and timeout > 0
        if "passwd-file" in argv:
            path = argv[argv.index("passwd-file") + 1]
            self.files[path] = (Path(path).read_text(), os.stat(path).st_mode & 0o777)
        for key, reply in self.replies.items():
            if " ".join(argv).startswith(key):
                return reply
        return 0, "", ""


SAVED_NONE = (0, "Wired connection 1:802-3-ethernet\n", "")


class TestParsing(unittest.TestCase):
    def test_split_terse_escapes(self):
        self.assertEqual(network.split_terse(r"*:My\:Net:80:WPA2"), ["*", "My:Net", "80", "WPA2"])
        self.assertEqual(network.split_terse(r" :back\\slash:5:"), [" ", "back\\slash", "5", ""])

    def test_wifi_list(self):
        text = "\n".join([
            r" :Home:40:WPA2",
            r" :Home:70:WPA2",
            r"*:Cafe\:Free:30:",
            r" ::90:WPA2",
            r" :Office:85:WPA1 WPA2 802.1X",
            r" :Open:55:--",
            "",
        ])
        nets = network.parse_wifi_list(text)
        self.assertEqual([n.ssid for n in nets], ["Cafe:Free", "Office", "Home", "Open"])
        self.assertTrue(nets[0].in_use)
        self.assertFalse(nets[0].secured)
        home = nets[2]
        self.assertEqual(home.signal, 70)
        self.assertTrue(home.secured)
        self.assertTrue(nets[1].enterprise)
        self.assertFalse(nets[3].secured)

    def test_dedupe_keeps_in_use_flag(self):
        nets = network.parse_wifi_list("*:Home:20:WPA2\n :Home:90:WPA2\n")
        self.assertEqual(len(nets), 1)
        self.assertEqual(nets[0].signal, 90)
        self.assertTrue(nets[0].in_use)

    def test_signal_icon(self):
        self.assertEqual(network.signal_icon(90), "network-wireless-signal-excellent-symbolic")
        self.assertEqual(network.signal_icon(60), "network-wireless-signal-good-symbolic")
        self.assertEqual(network.signal_icon(30), "network-wireless-signal-ok-symbolic")
        self.assertEqual(network.signal_icon(5), "network-wireless-signal-weak-symbolic")

    def test_devices(self):
        d = network.parse_devices("wlan0:wifi:connected:Home\neth0:ethernet:connected:Wired\n"
                                  "lo:loopback:connected (externally):lo\n")
        self.assertTrue(d.wifi)
        self.assertEqual(d.wifi_connection, "Home")
        self.assertTrue(d.ethernet_connected)
        d = network.parse_devices("eth0:ethernet:unavailable:\nwlan0:wifi:disconnected:\n")
        self.assertTrue(d.wifi)
        self.assertFalse(d.ethernet_connected)
        self.assertEqual(d.wifi_connection, "")
        self.assertFalse(network.parse_devices("eth0:ethernet:connecting (getting IP "
                                               "configuration):x\n").ethernet_connected)
        self.assertFalse(network.parse_devices("").wifi)

    def test_radio_and_connectivity(self):
        self.assertTrue(network.parse_radio("enabled\n"))
        self.assertFalse(network.parse_radio("disabled\n"))
        for word in ("full", "limited", "portal", "none", "unknown"):
            self.assertEqual(network.parse_connectivity(word + "\n"), word)
        self.assertEqual(network.parse_connectivity("garbage"), "unknown")


class TestSnapshot(unittest.TestCase):
    def test_full_snapshot(self):
        runner = FakeRunner({
            "nmcli -t -f DEVICE": (0, "wlan0:wifi:connected:Home\n", ""),
            "nmcli radio wifi": (0, "enabled\n", ""),
            "nmcli networking connectivity": (0, "full\n", ""),
            "nmcli -t -f IN-USE": (0, "*:Home:70:WPA2\n :Next:40:WPA2\n", ""),
            "nmcli -t -f NAME,TYPE": (0, "Home:802-11-wireless\n", ""),
        })
        state = network.snapshot(runner, rescan=True)
        self.assertEqual(state.active_ssid, "Home")
        self.assertEqual(state.connectivity, "full")
        self.assertEqual(state.saved, {"Home"})
        self.assertIn(["nmcli", "device", "wifi", "rescan"], runner.calls)
        self.assertEqual(network.status_text(state), ("Connected to Home — internet works", ""))

    def test_no_networkmanager(self):
        state = network.snapshot(FakeRunner({"nmcli": (127, "", "not found")}))
        self.assertFalse(state.nm_running)
        self.assertEqual(network.status_text(state)[0], "Not connected")

    def test_radio_off_skips_list(self):
        runner = FakeRunner({
            "nmcli -t -f DEVICE": (0, "wlan0:wifi:unavailable:\n", ""),
            "nmcli radio wifi": (0, "disabled\n", ""),
            "nmcli networking connectivity": (0, "none\n", ""),
        })
        state = network.snapshot(runner)
        self.assertFalse(state.wifi_on)
        self.assertFalse(any("list" in c for c in runner.calls))
        self.assertEqual(network.status_text(state),
                         ("Not connected", "Turn on Wi-Fi to see networks."))

    def test_cable(self):
        state = network.State(ethernet=True, connectivity="full")
        self.assertEqual(network.status_text(state)[0], "Connected by cable — internet works")


class TestConnect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def secured(self, sec="WPA2"):
        return Network("Home Net", 70, sec, False)

    def test_open_network(self):
        runner = FakeRunner({"nmcli -t -f NAME,TYPE": SAVED_NONE})
        self.assertEqual(network.connect(Network("Cafe", 50, "", False), "", runner), "")
        self.assertEqual(runner.calls[-1], ["nmcli", "device", "wifi", "connect", "Cafe"])

    def test_saved_connection_just_goes_up(self):
        runner = FakeRunner({"nmcli -t -f NAME,TYPE": (0, "Home Net:802-11-wireless\n", "")})
        self.assertEqual(network.connect(self.secured(), "", runner), "")
        self.assertEqual(runner.calls[-1], ["nmcli", "connection", "up", "id", "Home Net"])
        self.assertEqual(len(runner.calls), 2)

    def test_secured_password_never_on_argv(self):
        password = "s3cret pass:word"
        runner = FakeRunner({"nmcli -t -f NAME,TYPE": SAVED_NONE})
        error = network.connect(self.secured(), password, runner, tmpdir=self.tmp.name)
        self.assertEqual(error, "")
        for call in runner.calls:
            self.assertFalse(any(password in arg for arg in call), call)
        add, up = runner.calls[1], runner.calls[2]
        self.assertEqual(add, ["nmcli", "connection", "add", "type", "wifi", "ifname", "*",
                               "con-name", "Home Net", "ssid", "Home Net",
                               "wifi-sec.key-mgmt", "wpa-psk"])
        self.assertEqual(up[:5], ["nmcli", "connection", "up", "id", "Home Net"])
        self.assertEqual(up[5], "passwd-file")
        path = up[6]
        content, mode = runner.files[path]
        self.assertEqual(content, f"802-11-wireless-security.psk:{password}\n")
        self.assertEqual(mode, 0o600)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_wrong_password_deletes_connection(self):
        runner = FakeRunner({"nmcli -t -f NAME,TYPE": SAVED_NONE,
                             "nmcli connection up": (4, "", "Secrets were required")})
        error = network.connect(self.secured(), "wrongpass", runner, tmpdir=self.tmp.name)
        self.assertEqual(error, network.WRONG_PASSWORD_TEXT)
        self.assertEqual(runner.calls[-1], ["nmcli", "connection", "delete", "id", "Home Net"])
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_tmpfile_removed_when_runner_raises(self):
        def runner(argv, timeout):
            if "passwd-file" in argv:
                raise RuntimeError("boom")
            return SAVED_NONE if "show" in argv else (0, "", "")
        with self.assertRaises(RuntimeError):
            network.connect(self.secured(), "password1", runner, tmpdir=self.tmp.name)
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_new_password_replaces_saved(self):
        runner = FakeRunner({"nmcli -t -f NAME,TYPE": (0, "Home Net:802-11-wireless\n", "")})
        self.assertEqual(network.connect(self.secured(), "password1", runner,
                                         tmpdir=self.tmp.name), "")
        self.assertEqual(runner.calls[1], ["nmcli", "connection", "delete", "id", "Home Net"])
        self.assertEqual(runner.calls[2][:3], ["nmcli", "connection", "add"])

    def test_wpa3_only_uses_sae(self):
        runner = FakeRunner({"nmcli -t -f NAME,TYPE": SAVED_NONE})
        network.connect(self.secured("WPA3"), "password1", runner, tmpdir=self.tmp.name)
        self.assertEqual(runner.calls[1][-1], "sae")

    def test_short_password_and_enterprise(self):
        runner = FakeRunner({"nmcli -t -f NAME,TYPE": SAVED_NONE})
        self.assertEqual(network.connect(self.secured(), "short", runner),
                         network.SHORT_PASSWORD_TEXT)
        self.assertEqual(network.connect(self.secured("WPA2 802.1X"), "password1", runner),
                         network.ENTERPRISE_TEXT)
        self.assertEqual(network.connect(self.secured(), "pass\nword1", runner),
                         network.BAD_PASSWORD_TEXT)
        self.assertFalse(any("add" in c for c in runner.calls))

    def test_advanced_command(self):
        have = lambda *names: (lambda n: f"/usr/bin/{n}" if n in names else None)
        self.assertEqual(network.advanced_command(have("nm-connection-editor", "alacritty")),
                         ["nm-connection-editor"])
        self.assertEqual(network.advanced_command(have("alacritty", "nmtui")),
                         ["alacritty", "-e", "nmtui"])
        self.assertIsNone(network.advanced_command(have()))


class TestPure(unittest.TestCase):
    def test_no_gtk_import(self):
        source = (ROOT / "glue_welcome" / "network.py").read_text()
        self.assertNotIn("gi.repository", source)
        self.assertNotIn("import gi", source)


if __name__ == "__main__":
    unittest.main()
