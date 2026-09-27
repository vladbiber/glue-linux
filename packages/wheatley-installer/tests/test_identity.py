"""Tests for wheatley_installer.identity — IdentitySpec validation and identity_steps()."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from wheatley_installer.executor import RunCommand, WriteTargetFile
from wheatley_installer.identity import IdentityError, IdentitySpec, identity_steps


class TestIdentitySpecValid(unittest.TestCase):
    """Valid IdentitySpec construction and default fields."""

    def _spec(self, **kwargs):
        defaults = dict(hostname="wheatley", username="alice", password="hunter2")
        defaults.update(kwargs)
        return IdentitySpec(**defaults)

    def test_minimal_spec_round_trip(self):
        s = self._spec()
        self.assertEqual(s.hostname, "wheatley")
        self.assertEqual(s.username, "alice")
        self.assertEqual(s.password, "hunter2")
        self.assertEqual(s.locale, "en_US.UTF-8")
        self.assertEqual(s.timezone, "UTC")

    def test_custom_locale(self):
        s = self._spec(locale="de_DE.UTF-8")
        self.assertEqual(s.locale, "de_DE.UTF-8")

    def test_custom_timezone_two_part(self):
        s = self._spec(timezone="Europe/London")
        self.assertEqual(s.timezone, "Europe/London")

    def test_custom_timezone_three_part(self):
        s = self._spec(timezone="America/Indiana/Indianapolis")
        self.assertEqual(s.timezone, "America/Indiana/Indianapolis")

    def test_spec_is_frozen(self):
        s = self._spec()
        with self.assertRaises((AttributeError, TypeError)):
            s.hostname = "other"  # type: ignore[misc]

    def test_hostname_single_char(self):
        s = self._spec(hostname="x")
        self.assertEqual(s.hostname, "x")

    def test_hostname_with_hyphens(self):
        s = self._spec(hostname="my-arch-box")
        self.assertEqual(s.hostname, "my-arch-box")

    def test_hostname_alphanumeric_mix(self):
        s = self._spec(hostname="box42")
        self.assertEqual(s.hostname, "box42")

    def test_username_with_underscore(self):
        s = self._spec(username="_sysuser")
        self.assertEqual(s.username, "_sysuser")

    def test_username_with_hyphen(self):
        s = self._spec(username="john-doe")
        self.assertEqual(s.username, "john-doe")


# ---------------------------------------------------------------------------
# Negative validation cases (≥8 required)
# ---------------------------------------------------------------------------

class TestIdentitySpecInvalid(unittest.TestCase):
    """Negative validation tests — IdentityError must be raised."""

    def _bad(self, **kwargs):
        defaults = dict(hostname="wheatley", username="alice", password="secret")
        defaults.update(kwargs)
        with self.assertRaises(IdentityError):
            IdentitySpec(**defaults)

    def test_hostname_empty(self):
        self._bad(hostname="")

    def test_hostname_leading_hyphen(self):
        self._bad(hostname="-badhost")

    def test_hostname_trailing_hyphen(self):
        self._bad(hostname="badhost-")

    def test_hostname_uppercase(self):
        self._bad(hostname="BadHost")

    def test_hostname_too_long(self):
        self._bad(hostname="a" * 64)

    def test_hostname_dot_not_allowed(self):
        self._bad(hostname="my.host")

    def test_username_empty(self):
        self._bad(username="")

    def test_username_starts_with_digit(self):
        self._bad(username="1user")

    def test_username_uppercase(self):
        self._bad(username="Alice")

    def test_password_empty(self):
        self._bad(password="")

    def test_locale_missing_charset(self):
        self._bad(locale="en_US")

    def test_locale_wrong_case(self):
        # ll_CC.utf-8 is rejected; we only accept .UTF-8
        self._bad(locale="en_US.utf-8")

    def test_locale_bad_country_lowercase(self):
        self._bad(locale="en_us.UTF-8")

    def test_timezone_empty(self):
        self._bad(timezone="")

    def test_timezone_space_not_allowed(self):
        self._bad(timezone="America/New York")


# ---------------------------------------------------------------------------
# Step ordering and content tests
# ---------------------------------------------------------------------------

class TestIdentitySteps(unittest.TestCase):
    """Verify the exact ordering and content of steps returned by identity_steps()."""

    def setUp(self):
        self.spec = IdentitySpec(
            hostname="testbox",
            username="carol",
            password="S3cr3t!",
            locale="fr_FR.UTF-8",
            timezone="Europe/Paris",
        )
        self.steps = identity_steps(self.spec, target="/mnt")

    def test_step_count(self):
        self.assertEqual(len(self.steps), 11)

    def test_step_1_hostname_write(self):
        step = self.steps[0]
        self.assertIsInstance(step, WriteTargetFile)
        self.assertEqual(step.path, "/mnt/etc/hostname")
        self.assertEqual(step.content, "testbox\n")

    def test_step_2_hosts_write(self):
        step = self.steps[1]
        self.assertIsInstance(step, WriteTargetFile)
        self.assertEqual(step.path, "/mnt/etc/hosts")
        self.assertIn("127.0.1.1", step.content)
        self.assertIn("testbox", step.content)
        self.assertIn("127.0.0.1", step.content)
        self.assertIn("::1", step.content)

    def test_step_3_timezone_symlink(self):
        step = self.steps[2]
        self.assertIsInstance(step, RunCommand)
        self.assertIn("ln", step.argv)
        self.assertIn("/usr/share/zoneinfo/Europe/Paris", step.argv)
        self.assertIn("/etc/localtime", step.argv)

    def test_step_4_hwclock(self):
        step = self.steps[3]
        self.assertIsInstance(step, RunCommand)
        self.assertIn("hwclock", step.argv)
        self.assertIn("--systohc", step.argv)

    def test_step_5_locale_gen_write(self):
        step = self.steps[4]
        self.assertIsInstance(step, WriteTargetFile)
        self.assertEqual(step.path, "/mnt/etc/locale.gen")
        self.assertIn("fr_FR.UTF-8", step.content)

    def test_step_6_locale_gen_command(self):
        step = self.steps[5]
        self.assertIsInstance(step, RunCommand)
        self.assertIn("locale-gen", step.argv)

    def test_step_7_locale_conf_write(self):
        step = self.steps[6]
        self.assertIsInstance(step, WriteTargetFile)
        self.assertEqual(step.path, "/mnt/etc/locale.conf")
        self.assertEqual(step.content, "LANG=fr_FR.UTF-8\n")

    def test_step_8_useradd(self):
        step = self.steps[7]
        self.assertIsInstance(step, RunCommand)
        self.assertIn("useradd", step.argv)
        self.assertIn("-m", step.argv)
        self.assertIn("-G", step.argv)
        self.assertIn("wheel,audio,video,input,storage", step.argv)
        self.assertIn("-s", step.argv)
        self.assertIn("/bin/bash", step.argv)
        self.assertIn("carol", step.argv)

    def test_step_9_user_chpasswd(self):
        step = self.steps[8]
        self.assertIsInstance(step, RunCommand)
        self.assertIn("chpasswd", step.argv)
        self.assertIsNotNone(step.stdin)
        self.assertIn("carol:", step.stdin)

    def test_step_10_root_chpasswd(self):
        step = self.steps[9]
        self.assertIsInstance(step, RunCommand)
        self.assertIn("chpasswd", step.argv)
        self.assertIsNotNone(step.stdin)
        self.assertIn("root:", step.stdin)

    def test_step_11_sudoers(self):
        step = self.steps[10]
        self.assertIsInstance(step, WriteTargetFile)
        self.assertEqual(step.path, "/mnt/etc/sudoers.d/10-wheel")
        self.assertIn("%wheel", step.content)
        self.assertEqual(step.mode, 0o440)

    def test_password_not_in_any_argv(self):
        """The literal password must NEVER appear in any RunCommand.argv."""
        password = self.spec.password
        for step in self.steps:
            if isinstance(step, RunCommand):
                joined = " ".join(step.argv)
                self.assertNotIn(password, joined,
                    f"Password found in argv of: {step.description}")

    def test_password_only_in_chpasswd_stdin(self):
        """Password appears only in the two chpasswd RunCommand.stdin fields."""
        password = self.spec.password
        chpasswd_steps = [
            s for s in self.steps
            if isinstance(s, RunCommand) and "chpasswd" in s.argv
        ]
        self.assertEqual(len(chpasswd_steps), 2)
        for s in chpasswd_steps:
            self.assertIn(password, s.stdin)

    def test_non_chpasswd_steps_have_no_stdin(self):
        """Only the two chpasswd steps carry a stdin field."""
        for step in self.steps:
            if isinstance(step, RunCommand) and "chpasswd" not in step.argv:
                self.assertIsNone(step.stdin,
                    f"Unexpected stdin on non-chpasswd step: {step.description}")

    def test_target_path_honored(self):
        """All WriteTargetFile paths and artix-chroot paths use the given target."""
        steps = identity_steps(self.spec, target="/target/mnt")
        for step in steps:
            if isinstance(step, WriteTargetFile):
                self.assertTrue(step.path.startswith("/target/mnt"),
                    f"Path {step.path!r} does not start with /target/mnt")
            if isinstance(step, RunCommand) and "artix-chroot" in step.argv:
                self.assertIn("/target/mnt", step.argv)

    def test_hosts_127_0_1_1_contains_hostname(self):
        hosts = self.steps[1]
        lines = hosts.content.splitlines()
        found = any("127.0.1.1" in line and "testbox" in line for line in lines)
        self.assertTrue(found, "127.0.1.1 <hostname> line missing from /etc/hosts")

    def test_default_target(self):
        """identity_steps defaults to target=/mnt."""
        steps = identity_steps(self.spec)
        hostname_step = steps[0]
        self.assertIsInstance(hostname_step, WriteTargetFile)
        self.assertTrue(hostname_step.path.startswith("/mnt"))


if __name__ == "__main__":
    unittest.main()
