"""Headless tests for `python -m glue_installer` (no TTY, no root)."""

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
        [sys.executable, "-m", "glue_installer", *argv],
        cwd=str(_PKG_ROOT), capture_output=True, text=True,
        input=stdin, env=env, timeout=60,
    )


class TestValidateCatalog(unittest.TestCase):
    def test_real_catalog_exits_zero_with_counts(self):
        result = _run_module("--validate-catalog")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 kernels", result.stdout)
        self.assertIn("3 inits", result.stdout)
        self.assertIn("6 sessions", result.stdout)
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
        module = importlib.import_module("glue_installer.__main__")
        self.assertTrue(callable(module.main))
        self.assertEqual(module.main(["--validate-catalog"]), 0)


class TestKeyringPrepInDryRun(unittest.TestCase):
    """The host pacman keyring must be initialized+populated BEFORE basestrap
    (regression: online install died with 'cachyos: key ... is unknown')."""

    def test_keyring_steps_precede_basestrap(self):
        result = _run_module("--headless", "--dry-run", "--disk", "/dev/fake")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        init_at = next(
            i for i, l in enumerate(lines) if "pacman keyring" in l and "Initialize" in l
        )
        populate_at = next(
            i for i, l in enumerate(lines) if "cachyos" in l and "Populate" in l
        )
        basestrap_at = next(i for i, l in enumerate(lines) if "basestrap" in l)
        self.assertLess(init_at, populate_at)
        self.assertLess(populate_at, basestrap_at)


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
    """Verify GLUE_CATALOG env var and repo-relative fallback."""

    def _run_with_env(self, extra_env, *argv):
        env = dict(os.environ, TERM="dumb", **extra_env)
        return subprocess.run(
            [sys.executable, "-m", "glue_installer", *argv],
            cwd=str(_PKG_ROOT), capture_output=True, text=True,
            input="", env=env, timeout=60,
        )

    def test_glue_catalog_env_used_for_validate(self):
        """GLUE_CATALOG pointing at a copy of the real catalog loads correctly."""
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            tmp_catalog = os.path.join(tmp, "catalog.json")
            shutil.copy(str(_CATALOG_PATH), tmp_catalog)
            result = self._run_with_env(
                {"GLUE_CATALOG": tmp_catalog},
                "--validate-catalog",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Catalog OK", result.stdout)

    def test_glue_catalog_env_bad_path_exits_two(self):
        """A GLUE_CATALOG that doesn't exist causes a catalog error."""
        result = self._run_with_env(
            {"GLUE_CATALOG": "/nonexistent/catalog.json"},
            "--validate-catalog",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Catalog error", result.stderr)

    def test_repo_relative_fallback_works(self):
        """Without GLUE_CATALOG, the repo-relative catalog is found."""
        env = {k: v for k, v in os.environ.items() if k != "GLUE_CATALOG"}
        env["TERM"] = "dumb"
        result = subprocess.run(
            [sys.executable, "-m", "glue_installer", "--validate-catalog"],
            cwd=str(_PKG_ROOT), capture_output=True, text=True,
            input="", env=env, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Catalog OK", result.stdout)

    def test_dry_run_with_glue_catalog_override(self):
        """End-to-end --dry-run honours GLUE_CATALOG."""
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            tmp_catalog = os.path.join(tmp, "catalog.json")
            shutil.copy(str(_CATALOG_PATH), tmp_catalog)
            result = self._run_with_env(
                {"GLUE_CATALOG": tmp_catalog},
                "--headless", "--dry-run", "--disk", "/dev/fake",
            )
        self.assertEqual(result.returncode, 0, result.stderr)


class TestExecutorStdin(unittest.TestCase):
    """Verify that execute() passes stdin to subprocess for RunCommand with stdin set."""

    def test_stdin_field_default_is_none(self):
        from glue_installer.executor import RunCommand
        step = RunCommand(argv=["echo", "hi"], description="test")
        self.assertIsNone(step.stdin)

    def test_stdin_field_can_be_set(self):
        from glue_installer.executor import RunCommand
        step = RunCommand(argv=["cat"], description="test", stdin="hello\n")
        self.assertEqual(step.stdin, "hello\n")

    def test_execute_dry_run_does_not_print_stdin(self):
        """Dry-run output never exposes stdin contents."""
        import io
        from glue_installer.executor import RunCommand, execute
        secret = "hunter2_secret_value"
        step = RunCommand(argv=["chpasswd"], description="Set password", stdin=f"root:{secret}\n")
        log_lines = []
        execute([step], dry_run=True, log=log_lines.append)
        combined = "\n".join(log_lines)
        self.assertNotIn(secret, combined)

    def test_execute_dry_run_still_logs_description(self):
        """Dry-run logs the description even for stdin-carrying steps."""
        from glue_installer.executor import RunCommand, execute
        step = RunCommand(argv=["chpasswd"], description="Set password for alice", stdin="alice:pw\n")
        log_lines = []
        execute([step], dry_run=True, log=log_lines.append)
        self.assertTrue(any("Set password for alice" in line for line in log_lines))


if __name__ == "__main__":
    unittest.main()


class TestRootElevation(unittest.TestCase):
    """build_elevation_argv: sudo re-exec argv for the root requirement."""

    def test_prefers_packaged_launcher(self):
        from glue_installer.__main__ import build_elevation_argv
        argv = build_elevation_argv(
            ["--target", "/mnt"],
            which=lambda name: "/usr/bin/glue-install",
            executable="/usr/bin/python3",
        )
        self.assertEqual(
            argv, ["sudo", "/usr/bin/glue-install", "--target", "/mnt"])

    def test_falls_back_to_python_module(self):
        from glue_installer.__main__ import build_elevation_argv
        argv = build_elevation_argv(
            [], which=lambda name: None, executable="/usr/bin/python3")
        self.assertEqual(
            argv, ["sudo", "/usr/bin/python3", "-m", "glue_installer"])


class TestSyncClock(unittest.TestCase):
    """HTTP-Date live-clock sync (injectable; never raises)."""

    def test_large_drift_sets_clock(self):
        from glue_installer.run_ui import sync_clock
        applied = []
        drift = sync_clock(
            fetch_date=lambda url: "Tue, 21 Jul 2026 12:00:00 GMT",
            set_time=applied.append,
            now=lambda: 1000.0,
        )
        self.assertEqual(len(applied), 1)
        self.assertGreater(applied[0], 1.7e9)
        self.assertIsInstance(drift, int)
        self.assertGreater(drift, 0)

    def test_small_drift_leaves_clock_alone(self):
        import email.utils
        from glue_installer.run_ui import sync_clock
        header = "Tue, 21 Jul 2026 12:00:00 GMT"
        epoch = email.utils.parsedate_to_datetime(header).timestamp()
        applied = []
        self.assertIsNone(sync_clock(
            fetch_date=lambda url: header,
            set_time=applied.append,
            now=lambda: epoch + 30,
        ))
        self.assertEqual(applied, [])

    def test_offline_returns_none(self):
        from glue_installer.run_ui import sync_clock
        def fetch(url):
            raise OSError("no net")
        self.assertIsNone(sync_clock(fetch_date=fetch,
                                     set_time=lambda e: None,
                                     now=lambda: 0.0))

    def test_garbage_date_header_returns_none(self):
        from glue_installer.run_ui import sync_clock
        for bad in (None, "", "<html>", "not a date"):
            self.assertIsNone(sync_clock(fetch_date=lambda url, b=bad: b,
                                         set_time=lambda e: None,
                                         now=lambda: 0.0), bad)

    def test_set_time_failure_is_swallowed(self):
        from glue_installer.run_ui import sync_clock
        def boom(epoch):
            raise RuntimeError("date failed")
        self.assertIsNone(sync_clock(
            fetch_date=lambda url: "Tue, 21 Jul 2026 12:00:00 GMT",
            set_time=boom,
            now=lambda: 1000.0,
        ))


class TestDetectTimezone(unittest.TestCase):
    """GeoIP timezone default (injectable fetch, never raises)."""

    def test_valid_first_endpoint(self):
        from glue_installer.__main__ import detect_timezone
        self.assertEqual(
            detect_timezone(fetch=lambda url: "Europe/Bucharest\n"),
            "Europe/Bucharest",
        )

    def test_falls_back_to_second_endpoint(self):
        from glue_installer.__main__ import detect_timezone, _TZ_ENDPOINTS
        calls = []

        def fetch(url):
            calls.append(url)
            if url == _TZ_ENDPOINTS[0]:
                raise OSError("offline")
            return "America/New_York"

        self.assertEqual(detect_timezone(fetch=fetch), "America/New_York")
        self.assertEqual(calls, list(_TZ_ENDPOINTS))

    def test_offline_returns_none(self):
        from glue_installer.__main__ import detect_timezone

        def fetch(url):
            raise OSError("no network")

        self.assertIsNone(detect_timezone(fetch=fetch))

    def test_garbage_rejected(self):
        from glue_installer.__main__ import detect_timezone
        for bad in ("", "<html>error</html>", "not a timezone at all!", "UTC;rm -rf"):
            self.assertIsNone(detect_timezone(fetch=lambda url, b=bad: b), bad)


class TestAutodetectSpecTimezone(unittest.TestCase):
    """Second-chance timezone detection after the wizard (Wi-Fi came up in
    the wizard's network screen, so the pre-wizard lookup often ran offline)."""

    def _spec(self, tz):
        from glue_installer.identity import IdentitySpec
        return IdentitySpec(hostname="h", username="u", password="p", timezone=tz)

    def test_utc_placeholder_gets_detected_timezone(self):
        from glue_installer.run_ui import autodetect_spec_timezone
        spec = self._spec("UTC")
        out = autodetect_spec_timezone(spec, detect=lambda: "Europe/Bucharest")
        self.assertEqual(out.timezone, "Europe/Bucharest")
        self.assertEqual(out.username, "u")  # everything else untouched
        self.assertEqual(spec.timezone, "UTC")  # original spec not mutated

    def test_real_timezone_never_overridden(self):
        from glue_installer.run_ui import autodetect_spec_timezone
        spec = self._spec("Europe/London")
        out = autodetect_spec_timezone(spec, detect=lambda: "Europe/Bucharest")
        self.assertIs(out, spec)

    def test_detection_failure_keeps_spec(self):
        from glue_installer.run_ui import autodetect_spec_timezone
        spec = self._spec("UTC")
        self.assertIs(autodetect_spec_timezone(spec, detect=lambda: None), spec)
        self.assertIs(autodetect_spec_timezone(spec, detect=lambda: "UTC"), spec)

    def test_none_spec_passes_through(self):
        from glue_installer.run_ui import autodetect_spec_timezone
        self.assertIsNone(autodetect_spec_timezone(None, detect=lambda: "Europe/Paris"))
