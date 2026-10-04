"""Tests for PKGBUILD contents, launcher script, and find_catalog() resolution."""

import importlib
import os
import shutil
import re
import sys
import tempfile
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

_PKGBUILD = _PKG_ROOT / "PKGBUILD"
_LAUNCHER = _PKG_ROOT / "glue-install"


def _pkgbuild_text():
    return _PKGBUILD.read_text()


def _launcher_lines():
    return _LAUNCHER.read_text().splitlines()


# ---------------------------------------------------------------------------
# PKGBUILD contents
# ---------------------------------------------------------------------------

class TestPkgbuildContents(unittest.TestCase):

    def test_no_libnewt_in_depends(self):
        text = _pkgbuild_text()
        self.assertNotIn("libnewt", text)

    def test_python_in_depends(self):
        text = _pkgbuild_text()
        # depends=('python' ...) — python must appear in the depends array
        self.assertIn("'python'", text)

    def test_installs_to_usr_lib_glue_installer(self):
        text = _pkgbuild_text()
        self.assertIn("/usr/lib/glue-installer", text)

    def test_installs_catalog_json(self):
        text = _pkgbuild_text()
        self.assertIn("/usr/share/glue-installer/catalog/catalog.json", text)

    def test_installs_pacman_conf(self):
        text = _pkgbuild_text()
        self.assertIn("/usr/share/glue/pacman.conf", text)

    def test_installs_usr_bin_launcher(self):
        text = _pkgbuild_text()
        self.assertIn("/usr/bin/glue-install", text)

    def test_pkgrel_bumped_above_five(self):
        text = _pkgbuild_text()
        for line in text.splitlines():
            if line.startswith("pkgrel="):
                rel = int(line.split("=", 1)[1].strip())
                self.assertGreater(rel, 5, "pkgrel must be bumped beyond old value 5")
                return
        self.fail("pkgrel= not found in PKGBUILD")

    def test_no_bash_dep_required_by_installer(self):
        # The new installer is pure Python — bash is not needed as an explicit dep
        text = _pkgbuild_text()
        # 'bash' may appear in comments; must not appear in depends array line
        depends_line = next(
            (ln for ln in text.splitlines() if ln.strip().startswith("depends=")), ""
        )
        self.assertNotIn("'bash'", depends_line)


# ---------------------------------------------------------------------------
# Launcher contents
# ---------------------------------------------------------------------------

class TestLauncherContents(unittest.TestCase):

    def test_launcher_under_20_lines(self):
        lines = _launcher_lines()
        self.assertLess(len(lines), 20, f"Launcher has {len(lines)} lines (must be < 20)")

    def test_launcher_contains_python3_module(self):
        text = _LAUNCHER.read_text()
        self.assertIn("python3 -m glue_installer", text)

    def test_launcher_contains_at_args(self):
        text = _LAUNCHER.read_text()
        self.assertIn('"$@"', text)

    def test_launcher_no_whiptail(self):
        text = _LAUNCHER.read_text()
        self.assertNotIn("whiptail", text)

    def test_launcher_sets_pythonpath(self):
        text = _LAUNCHER.read_text()
        self.assertIn("PYTHONPATH", text)
        self.assertIn("/usr/lib/glue-installer", text)


# ---------------------------------------------------------------------------
# find_catalog() search order
# ---------------------------------------------------------------------------

class TestFindCatalog(unittest.TestCase):

    def setUp(self):
        # Force re-import so we get the real module under test
        if "glue_installer.__main__" in sys.modules:
            importlib.reload(sys.modules["glue_installer.__main__"])
        from glue_installer.__main__ import find_catalog
        self._find_catalog = find_catalog

    def _make_tmp_catalog(self, tmp_dir: Path) -> Path:
        """Create a minimal catalog.json under tmp_dir and return its path."""
        catalog_path = tmp_dir / "catalog.json"
        catalog_path.write_text('{"version":1}')
        return catalog_path

    def test_env_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_catalog = Path(tmp) / "custom_catalog.json"
            env_catalog.write_text("{}")
            old = os.environ.pop("GLUE_CATALOG", None)
            try:
                os.environ["GLUE_CATALOG"] = str(env_catalog)
                result = self._find_catalog()
                self.assertEqual(result, env_catalog)
            finally:
                os.environ.pop("GLUE_CATALOG", None)
                if old is not None:
                    os.environ["GLUE_CATALOG"] = old

    def test_packaged_path_wins_when_env_absent_and_file_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            packaged_dir = tmp_path / "usr/share/glue-installer/catalog"
            packaged_dir.mkdir(parents=True)
            packaged_catalog = packaged_dir / "catalog.json"
            packaged_catalog.write_text("{}")

            old = os.environ.pop("GLUE_CATALOG", None)
            try:
                result = self._find_catalog(base_prefix=tmp)
                self.assertEqual(result, packaged_catalog)
            finally:
                if old is not None:
                    os.environ["GLUE_CATALOG"] = old

    def test_repo_fallback_when_packaged_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            # No packaged catalog in this tmp prefix
            old = os.environ.pop("GLUE_CATALOG", None)
            try:
                result = self._find_catalog(base_prefix=tmp)
                # Should fall back to the repo-relative catalog
                self.assertTrue(result.name == "catalog.json")
                self.assertIn("catalog", str(result))
                # Repo fallback path should be resolvable from the package root
                self.assertTrue(
                    str(result).startswith(str(_PKG_ROOT)) or result.exists(),
                    f"Fallback {result} is neither under PKG_ROOT nor exists"
                )
            finally:
                if old is not None:
                    os.environ["GLUE_CATALOG"] = old

    def test_root_prefix_yields_absolute_path_regardless_of_cwd(self):
        """Regression: base_prefix='/' must produce /usr/share/..., never a
        CWD-relative 'usr/share/...'. Path('/'.rstrip('/')) == Path('') made the
        packaged candidate relative, so the live ISO never found its catalog."""
        with tempfile.TemporaryDirectory() as tmp:
            # Decoy: a RELATIVE usr/share/... tree inside the CWD. With the bug,
            # find_catalog(base_prefix='/') resolves against this and returns a
            # relative path; fixed code must ignore it.
            decoy_dir = Path(tmp) / "usr/share/glue-installer/catalog"
            decoy_dir.mkdir(parents=True)
            (decoy_dir / "catalog.json").write_text("{}")

            old_env = os.environ.pop("GLUE_CATALOG", None)
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                result = self._find_catalog(base_prefix="/")
                self.assertTrue(
                    result.is_absolute(),
                    f"find_catalog(base_prefix='/') returned relative path {result}"
                )
                self.assertNotEqual(result, Path("usr/share/glue-installer/catalog/catalog.json"))
            finally:
                os.chdir(old_cwd)
                if old_env is not None:
                    os.environ["GLUE_CATALOG"] = old_env

    def test_env_overrides_even_when_packaged_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Create both a packaged and an env-specified catalog
            packaged_dir = tmp_path / "usr/share/glue-installer/catalog"
            packaged_dir.mkdir(parents=True)
            (packaged_dir / "catalog.json").write_text("{}")
            env_catalog = tmp_path / "env_override.json"
            env_catalog.write_text("{}")

            old = os.environ.pop("GLUE_CATALOG", None)
            try:
                os.environ["GLUE_CATALOG"] = str(env_catalog)
                result = self._find_catalog(base_prefix=tmp)
                self.assertEqual(result, env_catalog)
            finally:
                os.environ.pop("GLUE_CATALOG", None)
                if old is not None:
                    os.environ["GLUE_CATALOG"] = old


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# gluewc PKGBUILD guards (Faza 5.5)
# ---------------------------------------------------------------------------

class TestGluewcPkgbuildGuards(unittest.TestCase):
    def setUp(self):
        self.text = (_PKG_ROOT.parent / "gluewc" / "PKGBUILD").read_text()

    def test_adaptive_sync_guard(self):
        self.assertIn("wlr_output_state_set_adaptive_sync_enabled", self.text)
        self.assertIn("lost per-output adaptive_sync", self.text)

    def test_alacritty_default_guard(self):
        self.assertIn("bind_insert = mod+Return = spawn:alacritty", self.text)
        self.assertIn("no longer the default terminal", self.text)

    def test_source_unchanged(self):
        self.assertIn('source=("git+https://github.com/vladbiber/gluewc.git")', self.text)

    # 5.9: the bar's hardware keys are guarded the same way
    def test_xf86_keybinding_guard(self):
        self.assertIn("XF86MonBrightnessUp = spawn:gluewc-backlight up", self.text)
        self.assertIn("XF86AudioRaiseVolume = spawn:.*wpctl set-volume", self.text)
        self.assertIn("XF86AudioPlay = spawn:playerctl play-pause", self.text)
        self.assertIn("lost the XF86 brightness/volume/media keybindings", self.text)

    def test_glueqs_depends_on_bar_runtime(self):
        text = (_PKG_ROOT.parent / "glueqs" / "PKGBUILD").read_text()
        depends = re.search(r"^depends=\((.*?)\)", text, re.S | re.M).group(1)
        for pkg in ("brightnessctl", "playerctl", "upower",
                    "power-profiles-daemon", "wl-clipboard", "quickshell"):
            self.assertIn(f"'{pkg}'", depends)
        self.assertNotIn("noctalia-qs", depends)


# ---------------------------------------------------------------------------
# nvwm PKGBUILD keeps alacritty as the default terminal (Faza 8.2)
# ---------------------------------------------------------------------------

class TestNvwmPkgbuildTerminal(unittest.TestCase):
    def setUp(self):
        self.text = (_PKG_ROOT.parent / "nvwm" / "PKGBUILD").read_text()

    def test_no_st_rewrite(self):
        self.assertNotIn("spawn:st", self.text)
        self.assertNotIn("terminal       = st", self.text)

    def test_alacritty_default_guard(self):
        self.assertIn("grep -q '^terminal       = alacritty' config.conf", self.text)
        self.assertIn("no longer defaults to alacritty", self.text)

    def test_alacritty_is_a_dependency(self):
        m = re.search(r"^depends=\((.*?)\)", self.text, re.S | re.M)
        self.assertIn("'alacritty'", m.group(1))
