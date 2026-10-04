import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from glue_welcome import live


def have(*names):
    return lambda name: f"/usr/bin/{name}" if name in names else None


class TestDetection(unittest.TestCase):
    def test_is_live_default_and_override(self):
        seen = []
        self.assertTrue(live.is_live({}, lambda p: seen.append(p) or True))
        self.assertEqual(seen, ["/run/artix/bootmnt"])
        self.assertFalse(live.is_live({}, lambda p: False))
        env = {"GLUE_LIVE_MARKER": "/tmp/m"}
        self.assertTrue(live.is_live(env, lambda p: p == "/tmp/m"))
        self.assertFalse(live.is_live(env, lambda p: p == "/run/artix/bootmnt"))

    def test_has_kms(self):
        seen = []
        self.assertTrue(live.has_kms({}, lambda g: seen.append(g) or ["/dev/dri/card0"]))
        self.assertEqual(seen, ["/dev/dri/card*"])
        self.assertFalse(live.has_kms({}, lambda g: []))
        self.assertTrue(live.has_kms({"GLUE_KMS_GLOB": "/x/c*"},
                                     lambda g: ["/x/c0"] if g == "/x/c*" else []))


class TestInstall(unittest.TestCase):
    ok = dict(env={}, exists=lambda p: True, glob_fn=lambda g: ["/dev/dri/card0"])

    def test_command_available(self):
        self.assertEqual(live.install_command(have("glue-install-gui"), **self.ok),
                         ["glue-install-gui"])
        self.assertEqual(live.explain_unavailable(have("glue-install-gui"), **self.ok), "")

    def test_launcher_absent(self):
        self.assertIsNone(live.install_command(have(), **self.ok))
        self.assertTrue(live.explain_unavailable(have(), **self.ok))

    def test_no_kms(self):
        kw = dict(self.ok, glob_fn=lambda g: [])
        self.assertIsNone(live.install_command(have("glue-install-gui"), **kw))
        self.assertEqual(live.explain_unavailable(have("glue-install-gui"), **kw),
                         live.NO_KMS_TEXT)
        self.assertIn("glue-install", live.NO_KMS_TEXT)

    def test_not_live(self):
        kw = dict(self.ok, exists=lambda p: False)
        self.assertIsNone(live.install_command(have("glue-install-gui"), **kw))
        self.assertEqual(live.explain_unavailable(have("glue-install-gui"), **kw),
                         "The installer is only available from the Glue Linux live USB.")

    def test_exit_codes(self):
        self.assertEqual(live.exit_code_message(2), live.NOT_LIVE_TEXT)
        self.assertEqual(live.exit_code_message(3), live.NO_KMS_TEXT)
        self.assertIn("screen", live.exit_code_message(4))
        self.assertIn("configuration", live.exit_code_message(5))
        self.assertEqual(len({live.exit_code_message(c) for c in (2, 3, 4, 5)}), 4)
        self.assertEqual(live.exit_code_message(99), live.FAILED_TEXT)
        self.assertEqual(live.exit_code_message(1), live.FAILED_TEXT)


class TestQuickActions(unittest.TestCase):
    def test_order_and_argv(self):
        every = have("alacritty", "nautilus", "firefox", "nm-connection-editor")
        actions = live.quick_actions(every)
        self.assertEqual([a.id for a in actions],
                         ["terminal", "files", "browser", "network", "system"])
        self.assertEqual(actions[0].argv, ("alacritty",))
        self.assertIsNone(actions[-1].argv)

    def test_missing_binaries_omitted(self):
        actions = live.quick_actions(have("nautilus", "nm-connection-editor"))
        self.assertEqual([a.id for a in actions], ["files", "network", "system"])
        self.assertEqual([a.id for a in live.quick_actions(have())], ["system"])


class TestSources(unittest.TestCase):
    def read(self, name):
        return (ROOT / "glue_welcome" / name).read_text(encoding="utf-8")

    def test_live_is_pure(self):
        src = self.read("live.py")
        self.assertNotRegex(src, r"(?m)^\s*(import|from)\s+(gi|glue_installer)\b")
        self.assertNotIn("subprocess", src)

    def test_window_never_elevates_or_imports_installer(self):
        src = self.read("window.py")
        for word in ("pkexec", "sudo", "glue_installer"):
            self.assertNotIn(word, src)

    def test_installed_system_page_has_no_new_elements(self):
        src = self.read("window.py")
        body = src[src.index("def _welcome_page"):src.index("def _hero")]
        self.assertRegex(body, r"if on_live:\s+page\.append\(self\._install_card\(\)\)\s+"
                               r"page\.append\(self\._live_actions\(\)\)\s+else:\s+"
                               r"page\.append\(self\._update_banner\(\)\)")

    def test_no_artix_in_texts(self):
        texts = [v for k, v in vars(live).items() if k.endswith("_TEXT")]
        texts += [t for a in live.quick_actions(lambda n: n) for t in (a.title, a.subtitle)]
        texts += list(live.EXIT_MESSAGES.values())
        from glue_welcome.strings import RO
        texts += list(RO) + list(RO.values())
        for text in texts:
            self.assertIsNone(re.search("artix", text, re.I), text)
        self.assertNotRegex(self.read("window.py").replace("/run/artix/bootmnt", ""), "(?i)artix")

    def test_ro_covers_live_texts(self):
        from glue_welcome.strings import RO
        for text in [*live.EXIT_MESSAGES.values(), live.NO_LAUNCHER_TEXT, live.FAILED_TEXT]:
            self.assertIn(text, RO)
        for a in live.quick_actions(lambda n: n):
            self.assertIn(a.title, RO)
            self.assertIn(a.subtitle, RO)


if __name__ == "__main__":
    unittest.main()
