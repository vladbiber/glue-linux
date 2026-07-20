"""Headless tests for `python -m wheatley_installer` (no TTY, no root)."""

import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

_CATALOG_PATH = _PKG_ROOT / "catalog" / "catalog.json"


def _run_module(*argv, stdin=""):
    env = dict(os.environ, TERM="dumb")
    return subprocess.run(
        [sys.executable, "-m", "wheatley_installer", *argv],
        cwd=str(_PKG_ROOT), capture_output=True, text=True,
        input=stdin, env=env, timeout=60,
    )


class TestValidateCatalog(unittest.TestCase):
    def test_real_catalog_exits_zero_with_counts(self):
        result = _run_module("--validate-catalog")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 kernels", result.stdout)
        self.assertIn("2 inits", result.stdout)
        self.assertIn("7 sessions", result.stdout)
        self.assertIn("2 shells", result.stdout)

    def test_explicit_catalog_path_matches_default(self):
        result = _run_module("--validate-catalog", "--catalog", str(_CATALOG_PATH))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Catalog OK", result.stdout)

    def test_broken_catalog_exits_two_with_message_no_traceback(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False
        ) as fh:
            fh.write('{"version": 1}')
            broken = fh.name
        try:
            result = _run_module("--validate-catalog", "--catalog", broken)
        finally:
            os.unlink(broken)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Catalog error:", result.stderr)
        self.assertIn("missing required keys", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Traceback", result.stdout)

    def test_invalid_json_catalog_exits_two(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False
        ) as fh:
            fh.write("{not json")
            broken = fh.name
        try:
            result = _run_module("--validate-catalog", "--catalog", broken)
        finally:
            os.unlink(broken)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not valid JSON", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_catalog_file_exits_two(self):
        result = _run_module(
            "--validate-catalog", "--catalog", "/nonexistent/catalog.json"
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Catalog error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class TestCliBasics(unittest.TestCase):
    def test_help_exits_zero(self):
        result = _run_module("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("--catalog", "--target", "--dry-run", "--validate-catalog"):
            self.assertIn(flag, result.stdout)

    def test_unknown_flag_exits_nonzero(self):
        result = _run_module("--no-such-flag")
        self.assertNotEqual(result.returncode, 0)

    def test_importing_main_module_needs_no_tty(self):
        module = importlib.import_module("wheatley_installer.__main__")
        self.assertTrue(callable(module.main))
        self.assertEqual(module.main(["--validate-catalog"]), 0)


if __name__ == "__main__":
    unittest.main()
