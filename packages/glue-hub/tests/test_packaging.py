import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from glue_hub.app import self_test


class TestPackaging(unittest.TestCase):
    def test_required_files(self):
        for rel in ("PKGBUILD", "glue-hub", "pkg.sh", "update.sh",
                    "org.glue.hub.policy", "data/applications/org.glue.Hub.desktop",
                    "data/autostart/org.glue.Hub.desktop"):
            self.assertTrue((ROOT / rel).is_file(), rel)

    def test_pkgbuild_has_runtime_dependencies_and_no_other_store(self):
        text = (ROOT / "PKGBUILD").read_text()
        for dependency in ("python-gobject", "gtk4", "libadwaita", "polkit"):
            self.assertIn(dependency, text)
        self.assertNotIn("shelly", text.casefold())
        self.assertNotIn("packagekit", text.casefold())

    def test_desktop_entry(self):
        text = (ROOT / "data/applications/org.glue.Hub.desktop").read_text()
        self.assertIn("Exec=glue-hub", text)
        self.assertIn("Terminal=false", text)

    def test_self_test(self):
        self.assertEqual(self_test(), 0)

    def test_source_files_stay_under_500_lines(self):
        for path in (ROOT / "glue_hub").glob("*.py"):
            with self.subTest(path=path.name):
                self.assertLessEqual(len(path.read_text().splitlines()), 500)


if __name__ == "__main__":
    unittest.main()
