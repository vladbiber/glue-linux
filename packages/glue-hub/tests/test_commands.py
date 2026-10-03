import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from glue_hub.commands import action_plan, update_plan


class TestActionPlans(unittest.TestCase):
    def test_repo_uses_polkit_helper(self):
        command = action_plan("repo", "install", "vlc")[0]
        self.assertEqual(command.argv,
                         ("pkexec", "/usr/lib/glue-hub/pkg.sh", "install", "vlc"))

    def test_flatpak_adds_flathub_before_install(self):
        plan = action_plan("flatpak", "install", "org.videolan.VLC")
        self.assertEqual(len(plan), 2)
        self.assertIn("--if-not-exists", plan[0].argv)
        self.assertIn("--user", plan[1].argv)

    def test_aur_runs_as_user_and_delegates_only_sudo(self):
        argv = action_plan("aur", "install", "vesktop-bin")[0].argv
        self.assertEqual(argv[0], "yay")
        self.assertIn("--aur", argv)
        self.assertEqual(argv[argv.index("--sudo") + 1], "pkexec")
        self.assertNotEqual(argv[0], "pkexec")

    def test_remove_aur_uses_validating_helper(self):
        argv = action_plan("aur", "remove", "vesktop-bin")[0].argv
        self.assertEqual(argv[:2], ("pkexec", "/usr/lib/glue-hub/pkg.sh"))

    def test_rejects_injection(self):
        for ref in ("", "vlc;id", "$(id)", "../vlc", "vlc name"):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                action_plan("repo", "install", ref)

    def test_update_covers_repo_flatpak_and_aur(self):
        plan = update_plan()
        self.assertEqual(len(plan), 3)
        self.assertEqual(plan[0].argv[0], "pkexec")
        self.assertEqual(plan[1].argv[0], "flatpak")
        self.assertEqual(plan[2].argv[0], "yay")


if __name__ == "__main__":
    unittest.main()
