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
        self.assertIn("3 inits", result.stdout)
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


class TestIdentityFlags(unittest.TestCase):
    """Verify --username/--password/--hostname/--locale/--timezone in headless dry-run."""

    def _run_headless_dryrun(self, *extra):
        return _run_module(
            "--headless", "--dry-run",
            "--disk", "/dev/fake",
            *extra,
        )

    def test_headless_dry_run_exits_zero(self):
        result = self._run_headless_dryrun(
            "--username", "testuser", "--password", "mypassword"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_identity_steps_appear_in_dry_run_output(self):
        result = self._run_headless_dryrun(
            "--username", "testuser", "--password", "mypassword"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("hostname", output.lower())
        self.assertIn("testuser", output)

    def test_password_never_in_stdout(self):
        result = self._run_headless_dryrun(
            "--username", "testuser", "--password", "s3cr3tP@ss"
        )
        self.assertNotIn("s3cr3tP@ss", result.stdout)

    def test_password_never_in_stderr(self):
        result = self._run_headless_dryrun(
            "--username", "testuser", "--password", "s3cr3tP@ss"
        )
        self.assertNotIn("s3cr3tP@ss", result.stderr)

    def test_custom_hostname_appears_in_output(self):
        result = self._run_headless_dryrun(
            "--username", "testuser", "--password", "pw",
            "--hostname", "mycustom"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mycustom", result.stdout)

    def test_custom_timezone_appears_in_output(self):
        result = self._run_headless_dryrun(
            "--username", "testuser", "--password", "pw",
            "--timezone", "Europe/Berlin"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Europe/Berlin", result.stdout)

    def test_custom_locale_appears_in_output(self):
        result = self._run_headless_dryrun(
            "--username", "testuser", "--password", "pw",
            "--locale", "de_DE.UTF-8"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("de_DE.UTF-8", result.stdout)

    def test_invalid_hostname_exits_nonzero(self):
        result = self._run_headless_dryrun(
            "--username", "testuser", "--password", "pw",
            "--hostname", "BAD-HOST"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Identity error", result.stderr)

    def test_invalid_username_exits_nonzero(self):
        result = self._run_headless_dryrun(
            "--username", "1baduser", "--password", "pw"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Identity error", result.stderr)

    def test_empty_password_exits_nonzero(self):
        result = self._run_headless_dryrun(
            "--username", "testuser", "--password", ""
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Identity error", result.stderr)

    def test_no_username_no_identity_steps(self):
        # Without --username, no identity steps should appear
        result = self._run_headless_dryrun()
        self.assertEqual(result.returncode, 0, result.stderr)
        # No useradd or chpasswd in output
        self.assertNotIn("useradd", result.stdout)
        self.assertNotIn("chpasswd", result.stdout.lower())

    def test_headless_flag_in_help(self):
        result = _run_module("--help")
        self.assertIn("--headless", result.stdout)
        self.assertIn("--username", result.stdout)
        self.assertIn("--password", result.stdout)
        self.assertIn("--hostname", result.stdout)


class TestCatalogResolution(unittest.TestCase):
    """Verify WHEATLEY_CATALOG env var and repo-relative fallback."""

    def _run_with_env(self, extra_env, *argv):
        env = dict(os.environ, TERM="dumb", **extra_env)
        return subprocess.run(
            [sys.executable, "-m", "wheatley_installer", *argv],
            cwd=str(_PKG_ROOT), capture_output=True, text=True,
            input="", env=env, timeout=60,
        )

    def test_wheatley_catalog_env_used_for_validate(self):
        """WHEATLEY_CATALOG pointing at a copy of the real catalog loads correctly."""
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            tmp_catalog = os.path.join(tmp, "catalog.json")
            shutil.copy(str(_CATALOG_PATH), tmp_catalog)
            result = self._run_with_env(
                {"WHEATLEY_CATALOG": tmp_catalog},
                "--validate-catalog",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Catalog OK", result.stdout)

    def test_wheatley_catalog_env_bad_path_exits_two(self):
        """A WHEATLEY_CATALOG that doesn't exist causes a catalog error."""
        result = self._run_with_env(
            {"WHEATLEY_CATALOG": "/nonexistent/catalog.json"},
            "--validate-catalog",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Catalog error", result.stderr)

    def test_repo_relative_fallback_works(self):
        """Without WHEATLEY_CATALOG, the repo-relative catalog is found."""
        env = {k: v for k, v in os.environ.items() if k != "WHEATLEY_CATALOG"}
        env["TERM"] = "dumb"
        result = subprocess.run(
            [sys.executable, "-m", "wheatley_installer", "--validate-catalog"],
            cwd=str(_PKG_ROOT), capture_output=True, text=True,
            input="", env=env, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Catalog OK", result.stdout)

    def test_dry_run_with_wheatley_catalog_override(self):
        """End-to-end --dry-run honours WHEATLEY_CATALOG."""
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            tmp_catalog = os.path.join(tmp, "catalog.json")
            shutil.copy(str(_CATALOG_PATH), tmp_catalog)
            result = self._run_with_env(
                {"WHEATLEY_CATALOG": tmp_catalog},
                "--headless", "--dry-run", "--disk", "/dev/fake",
            )
        self.assertEqual(result.returncode, 0, result.stderr)


class TestExecutorStdin(unittest.TestCase):
    """Verify that execute() passes stdin to subprocess for RunCommand with stdin set."""

    def test_stdin_field_default_is_none(self):
        from wheatley_installer.executor import RunCommand
        step = RunCommand(argv=["echo", "hi"], description="test")
        self.assertIsNone(step.stdin)

    def test_stdin_field_can_be_set(self):
        from wheatley_installer.executor import RunCommand
        step = RunCommand(argv=["cat"], description="test", stdin="hello\n")
        self.assertEqual(step.stdin, "hello\n")

    def test_execute_dry_run_does_not_print_stdin(self):
        """Dry-run output never exposes stdin contents."""
        import io
        from wheatley_installer.executor import RunCommand, execute
        secret = "hunter2_secret_value"
        step = RunCommand(argv=["chpasswd"], description="Set password", stdin=f"root:{secret}\n")
        log_lines = []
        execute([step], dry_run=True, log=log_lines.append)
        combined = "\n".join(log_lines)
        self.assertNotIn(secret, combined)

    def test_execute_dry_run_still_logs_description(self):
        """Dry-run logs the description even for stdin-carrying steps."""
        from wheatley_installer.executor import RunCommand, execute
        step = RunCommand(argv=["chpasswd"], description="Set password for alice", stdin="alice:pw\n")
        log_lines = []
        execute([step], dry_run=True, log=log_lines.append)
        self.assertTrue(any("Set password for alice" in line for line in log_lines))


if __name__ == "__main__":
    unittest.main()
