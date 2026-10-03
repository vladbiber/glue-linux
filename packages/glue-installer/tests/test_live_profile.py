"""Live ISO profile: shells and bar runtime present, one kernel (Faza 5.7)."""

import re
import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.catalog import load_catalog

_REPO_ROOT = _PKG_ROOT.parent.parent
_PROFILE = _REPO_ROOT / "iso-profile" / "glue" / "profile.yaml"
_CATALOG = _PKG_ROOT / "catalog" / "catalog.json"

_REQUIRED = (
    "gluewc", "glueqs", "noctalia-shell", "alacritty", "polkit-gnome",
    "brightnessctl", "playerctl", "wl-clipboard", "upower",
    "power-profiles-daemon", "xdg-desktop-portal", "xdg-desktop-portal-wlr",
    "xdg-desktop-portal-gtk", "grim", "glue-installer", "glue-apps",
    "glue-welcome", "fastfetch",
)
_FORBIDDEN = (
    "linux-cachyos", "linux-cachyos-bore", "calamares", "gnome-software",
    "packagekit", "discover",
)
_OK_LINUX_PREFIXED = {"linux-firmware", "linux-api-headers"}


def _rootfs_names():
    """Package names between `rootfs:` and `livefs:` (packages + packages-init)."""
    names = []
    inside = False
    for line in _PROFILE.read_text().splitlines():
        if re.match(r"^rootfs:", line):
            inside = True
        elif re.match(r"^livefs:", line):
            break
        elif inside:
            m = re.match(r"^\s+- (\S+)", line)
            if m:
                names.append(m.group(1))
    return names


class TestLiveProfile(unittest.TestCase):
    def setUp(self):
        self.names = _rootfs_names()

    def test_parsed_something(self):
        self.assertGreater(len(self.names), 30)

    def test_required_packages_present(self):
        for pkg in _REQUIRED:
            self.assertIn(pkg, self.names)

    def test_forbidden_packages_absent(self):
        for pkg in _FORBIDDEN:
            self.assertNotIn(pkg, self.names)

    def test_no_second_kernel(self):
        extra = [n for n in self.names
                 if n.startswith("linux-") and n not in _OK_LINUX_PREFIXED]
        self.assertEqual(extra, [])

    def test_no_duplicate_package_lines_in_main_list(self):
        main = []
        rootfs = _PROFILE.read_text().split("\nrootfs:")[1].split("packages-init:")[0]
        for line in rootfs.splitlines():
            m = re.match(r"^    - (\S+)", line)
            if m:
                main.append(m.group(1))
        self.assertEqual(sorted(n for n in set(main) if main.count(n) > 1), [])

    def test_gluewc_shell_packages_on_live(self):
        cat = load_catalog(_CATALOG)
        session = next(s for s in cat.sessions if s.id == "gluewc")
        shells = {s.id: s for s in cat.shells}
        self.assertTrue(session.shell_choices)
        for sid in session.shell_choices:
            for pkg in shells[sid].packages:
                self.assertIn(pkg, self.names)


if __name__ == "__main__":
    unittest.main()
