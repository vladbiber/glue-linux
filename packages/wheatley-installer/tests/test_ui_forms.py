"""Unit tests for wheatley_installer.ui_forms and the wizard disk/identity flow."""

import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from wheatley_installer.catalog import load_catalog
from wheatley_installer.disks import BlockDevice
from wheatley_installer.ui_forms import (
    MAX_VALUE_LEN, PASSWORD_MISMATCH, FormField, build_identity_fields,
    disk_label, masked, submit_form, validate_hostname, validate_locale,
    validate_password, validate_timezone, validate_username,
)
from wheatley_installer.ui_model import (
    Choose, ValidationError, Wizard, WizardResult,
)

_CATALOG = load_catalog(_PKG_ROOT / "catalog" / "catalog.json")

_DISK = BlockDevice(
    name="vda", path="/dev/vda", size_bytes=32 * 1024 ** 3,
    model="Test Disk", is_removable=False, has_mounted_partitions=False,
)
_DISK2 = BlockDevice(
    name="sdb", path="/dev/sdb", size_bytes=64 * 1024 ** 3,
    model="USB Stick", is_removable=True, has_mounted_partitions=False,
)


def _type(wizard: Wizard, text: str) -> None:
    for ch in text:
        wizard.feed_char(ch)


def _retype(wizard: Wizard, text: str) -> None:
    """Clear the current form field, then type `text`."""
    for _ in range(len(wizard.current_screen().field.value)):
        wizard.backspace()
    _type(wizard, text)


def _wizard_at_forms(disks=None) -> Wizard:
    """Minimal-mode wizard advanced to the disk screen (or first form)."""
    w = Wizard(_CATALOG, disks=disks, ask_identity=True)
    w.next()                            # welcome -> network
    w.next()                            # network -> mode
    w.apply(Choose("minimal"))
    w.next()                            # mode -> kernel
    w.apply(Choose("linux-cachyos"))
    w.next()                            # kernel -> init
    w.apply(Choose("dinit"))
    w.next()                            # init -> support
    w.next()                            # support -> diskmode (or form:hostname)
    if disks is not None:
        w.next()                        # diskmode (erase preselected) -> disk
    return w


def _walkthrough(password="hunter2!", confirm=None) -> Wizard:
    """Drive disk pick + all identity forms; leaves the wizard finished."""
    w = _wizard_at_forms(disks=[_DISK, _DISK2])
    w.apply(Choose("/dev/vda"))
    w.next()                            # disk -> form:hostname
    _retype(w, "testhost")
    w.next()                            # -> form:username
    _retype(w, "alice")
    w.next()                            # -> form:password
    _type(w, password)
    w.next()                            # -> form:password_confirm
    _type(w, confirm if confirm is not None else password)
    w.next()                            # -> form:locale
    _retype(w, "de_DE.UTF-8")
    w.next()                            # -> form:timezone
    _retype(w, "Europe/Berlin")
    w.next()                            # -> summary
    w.next()                            # finished
    return w


class TestValidatorsNegative(unittest.TestCase):
    def test_hostname_empty(self):
        self.assertIsNotNone(validate_hostname(""))

    def test_hostname_uppercase(self):
        self.assertIsNotNone(validate_hostname("BadHost"))

    def test_hostname_trailing_hyphen(self):
        self.assertIsNotNone(validate_hostname("host-"))

    def test_hostname_too_long(self):
        self.assertIsNotNone(validate_hostname("a" * 64))

    def test_username_empty(self):
        self.assertIsNotNone(validate_username(""))

    def test_username_leading_digit(self):
        self.assertIsNotNone(validate_username("1alice"))

    def test_username_uppercase(self):
        self.assertIsNotNone(validate_username("Alice"))

    def test_password_empty(self):
        self.assertIsNotNone(validate_password(""))

    def test_locale_missing_utf8(self):
        self.assertIsNotNone(validate_locale("en_US"))

    def test_locale_garbage(self):
        self.assertIsNotNone(validate_locale("english"))

    def test_timezone_empty(self):
        self.assertIsNotNone(validate_timezone(""))

    def test_timezone_with_space(self):
        self.assertIsNotNone(validate_timezone("Bad Zone"))


class TestValidatorsPositive(unittest.TestCase):
    def test_hostname_ok(self):
        self.assertIsNone(validate_hostname("wheatley-01"))

    def test_username_ok(self):
        self.assertIsNone(validate_username("_alice-2"))

    def test_locale_ok(self):
        self.assertIsNone(validate_locale("en_US.UTF-8"))

    def test_timezone_ok(self):
        self.assertIsNone(validate_timezone("UTC"))
        self.assertIsNone(validate_timezone("Europe/London"))
        self.assertIsNone(validate_timezone("America/Argentina/Ushuaia"))


class TestFormField(unittest.TestCase):
    def test_feed_char_appends_printable(self):
        f = FormField(key="h", label="H")
        self.assertTrue(f.feed_char("a"))
        self.assertEqual(f.value, "a")

    def test_feed_char_rejects_nonprintable(self):
        f = FormField(key="h", label="H")
        self.assertFalse(f.feed_char("\n"))
        self.assertEqual(f.value, "")

    def test_feed_char_rejects_multichar_and_nonstring(self):
        f = FormField(key="h", label="H")
        self.assertFalse(f.feed_char("ab"))
        self.assertFalse(f.feed_char(7))
        self.assertEqual(f.value, "")

    def test_feed_char_respects_max_len(self):
        f = FormField(key="h", label="H", value="x" * MAX_VALUE_LEN)
        self.assertFalse(f.feed_char("y"))
        self.assertEqual(len(f.value), MAX_VALUE_LEN)

    def test_feed_char_clears_stale_error(self):
        f = FormField(key="h", label="H", validator=validate_hostname)
        f.submit()
        self.assertIsNotNone(f.error)
        f.feed_char("a")
        self.assertIsNone(f.error)

    def test_backspace_removes_last_and_noops_when_empty(self):
        f = FormField(key="h", label="H", value="ab")
        f.backspace()
        self.assertEqual(f.value, "a")
        f.backspace()
        f.backspace()
        self.assertEqual(f.value, "")

    def test_submit_valid_and_invalid(self):
        f = FormField(key="h", label="H", validator=validate_hostname)
        ok, err = f.submit()
        self.assertFalse(ok)
        self.assertEqual(err, f.error)
        f.value = "goodhost"
        ok, err = f.submit()
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_clear_resets_value_and_error(self):
        f = FormField(key="p", label="P", value="x", validator=validate_hostname)
        f.error = "boom"
        f.clear()
        self.assertEqual((f.value, f.error), ("", None))


class TestMaskedAndDiskLabel(unittest.TestCase):
    def test_masked_secret_field(self):
        f = FormField(key="p", label="P", value="hunter2!", secret=True)
        self.assertEqual(masked(f), "********")

    def test_masked_plain_field(self):
        f = FormField(key="h", label="H", value="wheatley")
        self.assertEqual(masked(f), "wheatley")

    def test_disk_label_format(self):
        self.assertEqual(disk_label(_DISK), "/dev/vda  32.0 GiB  Test Disk")


class TestBuildIdentityFields(unittest.TestCase):
    def test_defaults_prefilled(self):
        forms = build_identity_fields()
        self.assertEqual(forms["hostname"].value, "wheatley")
        self.assertEqual(forms["username"].value, "")
        self.assertEqual(forms["locale"].value, "en_US.UTF-8")
        self.assertEqual(forms["timezone"].value, "UTC")

    def test_overrides_applied(self):
        forms = build_identity_fields({"hostname": "box", "username": "bob"})
        self.assertEqual(forms["hostname"].value, "box")
        self.assertEqual(forms["username"].value, "bob")

    def test_unknown_default_key_raises(self):
        with self.assertRaises(ValueError):
            build_identity_fields({"passwd": "x"})

    def test_password_fields_are_secret(self):
        forms = build_identity_fields()
        self.assertTrue(forms["password"].secret)
        self.assertTrue(forms["password_confirm"].secret)
        self.assertFalse(forms["hostname"].secret)


class TestSubmitForm(unittest.TestCase):
    def test_unknown_field_key(self):
        err, reset = submit_form(build_identity_fields(), "nope")
        self.assertIn("nope", err)
        self.assertFalse(reset)

    def test_invalid_value_no_reset(self):
        forms = build_identity_fields()
        err, reset = submit_form(forms, "username")
        self.assertIsNotNone(err)
        self.assertFalse(reset)

    def test_password_mismatch_clears_both_and_resets(self):
        forms = build_identity_fields()
        forms["password"].value = "hunter2!"
        forms["password_confirm"].value = "different"
        err, reset = submit_form(forms, "password_confirm")
        self.assertEqual(err, PASSWORD_MISMATCH)
        self.assertTrue(reset)
        self.assertEqual(forms["password"].value, "")
        self.assertEqual(forms["password_confirm"].value, "")

    def test_password_match_passes(self):
        forms = build_identity_fields()
        forms["password"].value = "hunter2!"
        forms["password_confirm"].value = "hunter2!"
        self.assertEqual(submit_form(forms, "password_confirm"), (None, False))


class TestWizardDiskScreen(unittest.TestCase):
    def test_disk_screen_reached_after_support_in_minimal(self):
        w = _wizard_at_forms(disks=[_DISK])
        self.assertEqual(w.current_screen().key, "disk")

    def test_disk_screen_is_radio_with_labels_and_notice(self):
        w = _wizard_at_forms(disks=[_DISK, _DISK2])
        screen = w.current_screen()
        self.assertEqual(screen.kind, "radio")
        self.assertEqual([i.id for i in screen.items], ["/dev/vda", "/dev/sdb"])
        self.assertIn("ERASED", screen.notice)
        self.assertIn("32.0 GiB", screen.items[0].label)
        self.assertEqual(screen.items[1].description, "Removable device")

    def test_next_without_selection_raises(self):
        w = _wizard_at_forms(disks=[_DISK])
        with self.assertRaises(ValidationError):
            w.next()

    def test_choose_marks_selected_and_lands_in_result(self):
        w = _walkthrough()
        result = w.to_result()
        self.assertIsInstance(result, WizardResult)
        self.assertEqual(result.device_path, "/dev/vda")

    def test_empty_disk_list_raises(self):
        with self.assertRaises(ValidationError):
            Wizard(_CATALOG, disks=[])

    def test_disk_without_path_raises(self):
        class NoPath:
            path = ""
        with self.assertRaises(ValidationError):
            Wizard(_CATALOG, disks=[NoPath()])

    def test_no_disks_injected_skips_disk_screen(self):
        w = _wizard_at_forms(disks=None)
        self.assertEqual(w.current_screen().key, "form:hostname")


class TestWizardIdentityFlow(unittest.TestCase):
    def test_form_screen_order(self):
        w = _wizard_at_forms(disks=None)
        keys = []
        for value in ("myhost", "alice", "pw1", "pw1", "en_US.UTF-8", "UTC"):
            keys.append(w.current_screen().key)
            _retype(w, value)
            w.next()
        self.assertEqual(keys, [
            "form:hostname", "form:username", "form:password",
            "form:password_confirm", "form:locale", "form:timezone",
        ])
        self.assertEqual(w.current_screen().key, "summary")

    def test_full_walkthrough_produces_correct_identity_spec(self):
        w = _walkthrough(password="hunter2!")
        result = w.to_result()
        spec = result.identity
        self.assertEqual(spec.hostname, "testhost")
        self.assertEqual(spec.username, "alice")
        self.assertEqual(spec.password, "hunter2!")
        self.assertEqual(spec.locale, "de_DE.UTF-8")
        self.assertEqual(spec.timezone, "Europe/Berlin")
        self.assertFalse(result.selection.gaming)
        self.assertTrue(result.selection.minimal)

    def test_password_mismatch_returns_to_password_screen(self):
        w = _wizard_at_forms(disks=None)
        _retype(w, "myhost"); w.next()
        _retype(w, "alice"); w.next()
        _type(w, "hunter2!"); w.next()
        _type(w, "different")
        with self.assertRaises(ValidationError) as ctx:
            w.next()
        self.assertEqual(str(ctx.exception), PASSWORD_MISMATCH)
        screen = w.current_screen()
        self.assertEqual(screen.key, "form:password")
        self.assertEqual(screen.field.value, "")

    def test_mismatch_then_reentry_succeeds(self):
        w = _wizard_at_forms(disks=None)
        _retype(w, "myhost"); w.next()
        _retype(w, "alice"); w.next()
        _type(w, "first"); w.next()
        _type(w, "second")
        with self.assertRaises(ValidationError):
            w.next()
        _type(w, "hunter2!"); w.next()          # re-enter password
        _type(w, "hunter2!"); w.next()          # confirm matches now
        self.assertEqual(w.current_screen().key, "form:locale")
        w.next()                                # locale default valid
        w.next()                                # timezone default valid
        w.next()                                # summary -> finished
        self.assertEqual(w.to_result().identity.password, "hunter2!")

    def test_feed_char_outside_form_raises(self):
        w = Wizard(_CATALOG, ask_identity=True)
        with self.assertRaises(ValidationError):
            w.feed_char("a")

    def test_invalid_hostname_blocks_advance(self):
        w = _wizard_at_forms(disks=None)
        _retype(w, "BAD-HOST")
        with self.assertRaises(ValidationError):
            w.next()
        self.assertEqual(w.current_screen().key, "form:hostname")


class TestEndToEndPipeline(unittest.TestCase):
    def test_wizard_result_feeds_full_dry_run_pipeline(self):
        from wheatley_installer.disks import plan_disk
        from wheatley_installer.executor import compile_steps, execute
        from wheatley_installer.identity import identity_steps
        from wheatley_installer.plan import resolve_plan

        result = _walkthrough(password="hunter2!").to_result()
        plan = resolve_plan(_CATALOG, result.selection)
        disk_plan = plan_disk(_DISK, "uefi")
        self.assertEqual(disk_plan.device_path, result.device_path)
        steps = compile_steps(
            plan, target="/mnt", init_id=result.selection.init_id,
            disk_plan=disk_plan,
        ) + identity_steps(result.identity, target="/mnt")
        log = []
        execute(steps, dry_run=True, log=log.append)
        combined = "\n".join(log)
        self.assertIn("testhost", combined)
        self.assertNotIn("hunter2!", combined)


class TestPurityAndSize(unittest.TestCase):
    _SRC_DIR = _PKG_ROOT / "wheatley_installer"

    def _assert_pure(self, filename):
        source = (self._SRC_DIR / filename).read_text()
        for line in source.splitlines():
            stripped = line.strip()
            for banned in ("curses", "subprocess", "os"):
                self.assertFalse(
                    stripped.startswith(f"import {banned}")
                    or stripped.startswith(f"from {banned} import")
                    or stripped.startswith(f"from {banned}."),
                    f"{filename} imports {banned}: {line!r}",
                )

    def test_ui_forms_has_no_curses_subprocess_os(self):
        self._assert_pure("ui_forms.py")

    def test_ui_model_has_no_curses_subprocess_os(self):
        self._assert_pure("ui_model.py")

    def test_touched_files_within_500_lines(self):
        for filename in ("ui_forms.py", "ui_model.py", "render.py",
                         "tui.py", "__main__.py"):
            count = len((self._SRC_DIR / filename).read_text().splitlines())
            self.assertLessEqual(count, 500, f"{filename} is {count} lines")


if __name__ == "__main__":
    unittest.main()
