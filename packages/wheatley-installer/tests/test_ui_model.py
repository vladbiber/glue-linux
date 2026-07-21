"""Unit tests for wheatley_installer.ui_model (pure wizard state machine)."""

import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from wheatley_installer.catalog import load_catalog
from wheatley_installer.plan import Selection, resolve_plan
from wheatley_installer.ui_model import (
    Choose, Item, Screen, SetFlag, Toggle, ValidationError, Wizard,
    DE_SECTION,
)

_CATALOG_PATH = _PKG_ROOT / "catalog" / "catalog.json"
_CATALOG = load_catalog(_CATALOG_PATH)


def _new_wizard() -> Wizard:
    w = Wizard(_CATALOG)
    w.set_network_status("connected")  # network screen blocks Next otherwise
    return w


def _advance_to(wizard: Wizard, key: str) -> None:
    """Drive next() until current screen key == key (choices must be valid)."""
    for _ in range(30):
        if wizard.current_screen().key == key:
            return
        wizard.next()
    raise AssertionError(f"Never reached screen '{key}'")


def _drive_happy_path(sessions=("mangowc", "niri"), shells=None,
                      bluetooth=True, gaming=True) -> Wizard:
    """Full custom-mode walk: cachyos, dinit, given sessions/shells."""
    w = _new_wizard()
    w.next()  # welcome -> network
    w.next()  # network -> mode (custom preselected)
    w.next()  # mode -> kernel
    w.apply(Choose("linux-cachyos"))
    w.next()  # kernel -> init
    w.apply(Choose("dinit"))
    w.next()  # init -> sessions
    for sid in sessions:
        w.apply(Toggle(sid))
    w.next()  # sessions -> first shell screen (or support)
    shells = shells or {}
    while w.current_screen().key.startswith("shell:"):
        sid = w.current_screen().key.split(":", 1)[1]
        w.apply(Choose(shells.get(sid, "noctalia")))
        w.next()
    assert w.current_screen().key == "support"
    if bluetooth:
        w.apply(Toggle("bluetooth"))
    w.next()  # support -> gaming
    w.apply(SetFlag(gaming))
    w.next()  # gaming -> summary
    w.next()  # summary -> finished
    return w


class TestScreenSequence(unittest.TestCase):

    def test_welcome_is_first_screen(self):
        w = _new_wizard()
        screen = w.current_screen()
        self.assertEqual(screen.key, "welcome")
        self.assertEqual(screen.kind, "info")
        self.assertIn("Wheatley", screen.notice)

    def test_full_custom_walk_visits_expected_keys(self):
        w = _new_wizard()
        visited = [w.current_screen().key]
        w.next(); visited.append(w.current_screen().key)   # network
        w.next(); visited.append(w.current_screen().key)   # mode
        w.next(); visited.append(w.current_screen().key)   # kernel
        w.apply(Choose("linux-cachyos"))
        w.next(); visited.append(w.current_screen().key)   # init
        w.apply(Choose("dinit"))
        w.next(); visited.append(w.current_screen().key)   # sessions
        w.apply(Toggle("mangowc"))
        w.apply(Toggle("niri"))
        w.next(); visited.append(w.current_screen().key)   # shell:mangowc
        w.apply(Choose("noctalia"))
        w.next(); visited.append(w.current_screen().key)   # shell:niri
        w.apply(Choose("imperative-dots"))
        w.next(); visited.append(w.current_screen().key)   # support
        w.next(); visited.append(w.current_screen().key)   # gaming
        w.next(); visited.append(w.current_screen().key)   # summary
        self.assertEqual(visited, [
            "welcome", "network", "mode", "kernel", "init", "sessions",
            "shell:mangowc", "shell:niri", "support", "gaming", "summary",
        ])

    def test_mode_screen_preselects_custom(self):
        w = _new_wizard()
        w.next()  # -> network
        w.next()  # -> mode
        screen = w.current_screen()
        self.assertEqual(screen.key, "mode")
        selected = [i.id for i in screen.items if i.selected]
        self.assertEqual(selected, ["custom"])

    def test_shell_screens_sorted_session_order(self):
        w = _drive_happy_path()
        # mangowc < niri alphabetically; verified via full walk above, and
        # the shell_choice keys must cover exactly the qualifying sessions.
        sel = w.to_selection()
        self.assertEqual(list(sel.shell_choice.keys()), ["mangowc", "niri"])

    def test_shell_screens_only_for_shell_capable_sessions(self):
        w = _new_wizard()
        w.next(); w.next(); w.next()  # welcome -> network -> mode -> kernel
        w.apply(Choose("linux-cachyos")); w.next()
        w.apply(Choose("dinit")); w.next()
        w.apply(Toggle("apeturewm"))
        w.apply(Toggle("sway"))
        w.next()
        self.assertEqual(w.current_screen().key, "support")

    def test_next_on_summary_finishes(self):
        w = _drive_happy_path()
        self.assertTrue(w.is_finished())

    def test_not_finished_before_summary_next(self):
        w = _new_wizard()
        self.assertFalse(w.is_finished())
        _advance_to(w, "mode")
        self.assertFalse(w.is_finished())


class TestKernelInitScreens(unittest.TestCase):

    def _kernel_screen(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        return w.current_screen()

    def test_cachyos_first_and_recommended(self):
        screen = self._kernel_screen()
        self.assertEqual(screen.items[0].id, "linux-cachyos")
        self.assertTrue(screen.items[0].recommended)

    def test_zen_offered_not_recommended(self):
        screen = self._kernel_screen()
        zen = next(i for i in screen.items if i.id == "linux-zen")
        self.assertFalse(zen.recommended)

    def test_dinit_label_contains_recommended(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        w.apply(Choose("linux-cachyos"))
        w.next()
        screen = w.current_screen()
        self.assertEqual(screen.key, "init")
        dinit = next(i for i in screen.items if i.id == "dinit")
        self.assertIn("(recommended)", dinit.label)
        self.assertTrue(dinit.recommended)

    def test_radio_choose_deselects_others(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        w.apply(Choose("linux-zen"))
        w.apply(Choose("linux-cachyos"))
        screen = w.current_screen()
        selected = [i.id for i in screen.items if i.selected]
        self.assertEqual(selected, ["linux-cachyos"])


class TestSessionsScreen(unittest.TestCase):

    def _sessions_screen(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        w.apply(Choose("linux-cachyos")); w.next()
        w.apply(Choose("dinit")); w.next()
        return w, w.current_screen()

    def test_notice_mentions_multiple_and_login_screen(self):
        _, screen = self._sessions_screen()
        self.assertIsNotNone(screen.notice)
        self.assertIn("MULTIPLE", screen.notice)
        self.assertIn("login screen", screen.notice)

    def test_de_items_carry_section(self):
        _, screen = self._sessions_screen()
        de_ids = {s.id for s in _CATALOG.sessions if s.kind == "de"}
        self.assertIn("kde-plasma", de_ids)
        self.assertIn("xfce", de_ids)
        for item in screen.items:
            if item.id in de_ids:
                self.assertEqual(item.section, DE_SECTION)
            else:
                self.assertIsNone(item.section)

    def test_all_sessions_present_with_ratings_and_keybinds(self):
        _, screen = self._sessions_screen()
        by_id = {i.id: i for i in screen.items}
        self.assertEqual(set(by_id), {s.id for s in _CATALOG.sessions})
        for session in _CATALOG.sessions:
            item = by_id[session.id]
            self.assertEqual(item.ease, session.ease)
            self.assertEqual(item.lightness, session.lightness)
            self.assertIsNotNone(item.keybinds)
            for kb in session.keybindings:
                self.assertIn(kb.keys, item.keybinds)
                self.assertIn(kb.action, item.keybinds)

    def test_session_screenshots_populated(self):
        _, screen = self._sessions_screen()
        for session in _CATALOG.sessions:
            item = next(i for i in screen.items if i.id == session.id)
            self.assertEqual(item.screenshot, session.screenshot)

    def test_toggle_flips_item(self):
        w, _ = self._sessions_screen()
        w.apply(Toggle("mangowc"))
        item = next(i for i in w.current_screen().items if i.id == "mangowc")
        self.assertTrue(item.selected)
        w.apply(Toggle("mangowc"))
        item = next(i for i in w.current_screen().items if i.id == "mangowc")
        self.assertFalse(item.selected)


class TestShellScreens(unittest.TestCase):

    def _to_shell_screen(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        w.apply(Choose("linux-cachyos")); w.next()
        w.apply(Choose("dinit")); w.next()
        w.apply(Toggle("mangowc"))
        w.next()
        return w

    def test_shell_items_carry_catalog_data(self):
        w = self._to_shell_screen()
        screen = w.current_screen()
        self.assertEqual(screen.key, "shell:mangowc")
        shells = {s.id: s for s in _CATALOG.shells}
        self.assertEqual([i.id for i in screen.items],
                         ["noctalia", "imperative-dots"])
        for item in screen.items:
            shell = shells[item.id]
            self.assertEqual(item.ease, shell.ease)
            self.assertEqual(item.lightness, shell.lightness)
            self.assertEqual(item.screenshot, shell.screenshot)
            self.assertIsNotNone(item.keybinds)

    def test_next_without_shell_choice_raises(self):
        w = self._to_shell_screen()
        with self.assertRaises(ValidationError):
            w.next()

    def test_deselect_session_drops_shell_screen_and_choice(self):
        w = self._to_shell_screen()
        w.apply(Choose("noctalia"))
        w.back()  # back to sessions
        self.assertEqual(w.current_screen().key, "sessions")
        w.apply(Toggle("mangowc"))  # deselect
        w.apply(Toggle("sway"))     # keep a session so next() is valid
        w.next()
        self.assertEqual(w.current_screen().key, "support")
        w.next(); w.next(); w.next()  # support -> gaming -> summary -> done
        sel = w.to_selection()
        self.assertEqual(sel.shell_choice, {})
        self.assertEqual(sel.session_ids, ["sway"])
        resolve_plan(_CATALOG, sel)  # must not raise


class TestValidation(unittest.TestCase):

    def test_next_without_kernel_raises(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        with self.assertRaises(ValidationError):
            w.next()

    def test_next_without_init_raises(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        w.apply(Choose("linux-zen"))
        w.next()
        with self.assertRaises(ValidationError):
            w.next()

    def test_zero_sessions_in_custom_mode_raises_with_hint(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        w.apply(Choose("linux-cachyos")); w.next()
        w.apply(Choose("dinit")); w.next()
        with self.assertRaises(ValidationError) as ctx:
            w.next()
        msg = str(ctx.exception)
        self.assertIn("at least one", msg)
        self.assertIn("minimal", msg)

    def test_to_selection_before_finish_raises(self):
        w = _new_wizard()
        with self.assertRaises(ValidationError):
            w.to_selection()

    def test_choose_on_multi_screen_raises(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        w.apply(Choose("linux-cachyos")); w.next()
        w.apply(Choose("dinit")); w.next()
        with self.assertRaises(ValidationError):
            w.apply(Choose("mangowc"))

    def test_toggle_on_radio_screen_raises(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        with self.assertRaises(ValidationError):
            w.apply(Toggle("linux-cachyos"))

    def test_setflag_on_radio_screen_raises(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        with self.assertRaises(ValidationError):
            w.apply(SetFlag(True))

    def test_unknown_item_id_raises(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        with self.assertRaises(ValidationError):
            w.apply(Choose("not-a-kernel"))

    def test_unknown_event_type_raises(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        with self.assertRaises(ValidationError):
            w.apply("linux-cachyos")


class TestNavigation(unittest.TestCase):

    def test_back_on_first_screen_is_noop(self):
        w = _new_wizard()
        w.back()
        self.assertEqual(w.current_screen().key, "welcome")

    def test_back_then_forward_preserves_choices(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        w.apply(Choose("linux-zen"))
        w.next()
        w.apply(Choose("runit"))
        w.back()
        kernel_screen = w.current_screen()
        self.assertEqual(
            [i.id for i in kernel_screen.items if i.selected], ["linux-zen"])
        w.next()
        init_screen = w.current_screen()
        self.assertEqual(
            [i.id for i in init_screen.items if i.selected], ["runit"])

    def test_back_from_summary_unfinishes(self):
        w = _drive_happy_path()
        self.assertTrue(w.is_finished())
        w.back()
        self.assertFalse(w.is_finished())
        self.assertEqual(w.current_screen().key, "gaming")


class TestSupportAndGaming(unittest.TestCase):

    def test_support_screen_lists_bluetooth_with_catalog_default(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        w.apply(Choose("linux-cachyos")); w.next()
        w.apply(Choose("dinit")); w.next()
        w.apply(Toggle("sway")); w.next()
        screen = w.current_screen()
        self.assertEqual(screen.key, "support")
        self.assertEqual(screen.kind, "multi")
        bt = next(i for i in screen.items if i.id == "bluetooth")
        bt_default = next(t for t in _CATALOG.support if t.id == "bluetooth")
        self.assertEqual(bt.selected, bt_default.default)

    def test_gaming_setflag_reflected_in_screen(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        w.apply(Choose("linux-cachyos")); w.next()
        w.apply(Choose("dinit")); w.next()
        w.apply(Toggle("sway")); w.next()
        w.next()  # support -> gaming
        screen = w.current_screen()
        self.assertEqual(screen.kind, "toggle")
        self.assertFalse(screen.items[0].selected)
        w.apply(SetFlag(True))
        self.assertTrue(w.current_screen().items[0].selected)


class TestSummaryAndSelection(unittest.TestCase):

    def test_happy_path_selection_resolves_with_expected_packages(self):
        w = _drive_happy_path(
            sessions=("mangowc", "niri"),
            shells={"mangowc": "noctalia", "niri": "imperative-dots"},
            bluetooth=True, gaming=True,
        )
        sel = w.to_selection()
        self.assertEqual(sel.kernel_id, "linux-cachyos")
        self.assertEqual(sel.init_id, "dinit")
        self.assertEqual(sel.session_ids, ["mangowc", "niri"])
        self.assertEqual(sel.shell_choice,
                         {"mangowc": "noctalia", "niri": "imperative-dots"})
        self.assertEqual(sel.support_ids, ["bluetooth"])
        self.assertTrue(sel.gaming)
        self.assertFalse(sel.minimal)
        plan = resolve_plan(_CATALOG, sel)  # must not raise PlanError
        for pkg in ("mangowc", "niri", "noctalia-shell", "quickshell",
                    "imperative-dots", "linux-cachyos", "dinit", "bluez",
                    "steam", "greetd", "fastfetch"):
            self.assertIn(pkg, plan.packages)

    def test_summary_items_reflect_choices(self):
        w = _drive_happy_path()
        w.back()  # back onto summary
        w.next()  # forward: still summary until finishing next()
        summary = w.current_screen()
        self.assertEqual(summary.kind, "summary")
        labels = "\n".join(i.label for i in summary.items)
        self.assertIn("CachyOS", labels)
        self.assertIn("dinit", labels)
        self.assertIn("MangoWC", labels)
        self.assertIn("Niri", labels)
        self.assertIn("Bluetooth", labels)
        self.assertIn("Gaming Mode: on", labels)

    def test_every_completed_run_resolves(self):
        # single session, no shell needed, runit, zen, no extras
        w = _new_wizard()
        _advance_to(w, "kernel")
        w.apply(Choose("linux-zen")); w.next()
        w.apply(Choose("runit")); w.next()
        w.apply(Toggle("kde-plasma")); w.next()
        w.next(); w.next(); w.next()
        resolve_plan(_CATALOG, w.to_selection())  # must not raise


class TestMinimalMode(unittest.TestCase):

    def _drive_minimal(self) -> Wizard:
        w = _new_wizard()
        w.next()  # welcome -> network
        w.next()  # network -> mode
        w.apply(Choose("minimal"))
        w.next()  # mode -> kernel
        w.apply(Choose("linux-cachyos")); w.next()
        w.apply(Choose("dinit"))
        return w

    def test_minimal_walk_never_shows_sessions(self):
        w = self._drive_minimal()
        visited = []
        while not w.is_finished():
            visited.append(w.current_screen().key)
            w.next()
        self.assertNotIn("sessions", visited)
        self.assertNotIn("gaming", visited)
        self.assertEqual(visited[-2:], ["support", "summary"])

    def test_minimal_selection_accepted_by_resolve_plan(self):
        w = self._drive_minimal()
        while not w.is_finished():
            w.next()
        sel = w.to_selection()
        self.assertTrue(sel.minimal)
        self.assertEqual(sel.session_ids, [])
        self.assertEqual(sel.shell_choice, {})
        self.assertFalse(sel.gaming)
        resolve_plan(_CATALOG, sel)  # must not raise

    def test_switching_to_minimal_clears_sessions_and_shells(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        w.apply(Choose("linux-cachyos")); w.next()
        w.apply(Choose("dinit")); w.next()
        w.apply(Toggle("mangowc")); w.next()
        w.apply(Choose("noctalia"))
        w.back(); w.back(); w.back(); w.back()  # back to mode
        self.assertEqual(w.current_screen().key, "mode")
        w.apply(Choose("minimal"))
        while not w.is_finished():
            w.next()
        sel = w.to_selection()
        self.assertEqual(sel.session_ids, [])
        self.assertEqual(sel.shell_choice, {})
        resolve_plan(_CATALOG, sel)  # must not raise


if __name__ == "__main__":
    unittest.main()


class TestDiskModeAndPartitionFlow(unittest.TestCase):
    """diskmode screen: erase/existing/manual; partition screen for existing."""

    class _Disk:
        path = "/dev/vda"; name = "vda"; size_bytes = 64 * 1024 ** 3
        model = ""; is_removable = False; has_mounted_partitions = False

    class _Part:
        def __init__(self, path, mounted=False, esp=False):
            self.name = path.rsplit("/", 1)[-1]; self.path = path
            self.parent_path = "/dev/vda"; self.size_bytes = 32 * 1024 ** 3
            self.fstype = "ext4"; self.is_esp = esp; self.is_mounted = mounted

    def _wizard_at_diskmode(self):
        w = Wizard(_CATALOG, disks=[self._Disk()])
        w.set_network_status("connected")
        w.set_partitions([self._Part("/dev/vda2"),
                          self._Part("/dev/vda1", esp=True),
                          self._Part("/dev/vda3", mounted=True)])
        _advance_to(w, "network")
        w.next()
        w.apply(Choose("minimal"))
        w.next()
        w.apply(Choose("linux-cachyos")); w.next()
        w.apply(Choose("dinit")); w.next()
        w.next()  # support -> diskmode
        self.assertEqual(w.current_screen().key, "diskmode")
        return w

    def test_erase_default_goes_to_disk_screen(self):
        w = self._wizard_at_diskmode()
        w.next()
        self.assertEqual(w.current_screen().key, "disk")

    def test_existing_goes_to_partition_screen_filtering_esp_and_mounted(self):
        w = self._wizard_at_diskmode()
        w.apply(Choose("existing"))
        w.next()
        screen = w.current_screen()
        self.assertEqual(screen.key, "partition")
        self.assertEqual([i.id for i in screen.items], ["/dev/vda2"])

    def test_existing_partition_lands_in_result(self):
        w = self._wizard_at_diskmode()
        w.apply(Choose("existing"))
        w.next()
        w.apply(Choose("/dev/vda2"))
        w.next()  # partition -> summary
        self.assertEqual(w.current_screen().key, "summary")
        self.assertIn("/dev/vda2", w.current_screen().items[-1].label)
        w.next()
        result = w.to_result()
        self.assertEqual(result.disk_mode, "existing")
        self.assertEqual(result.partition_path, "/dev/vda2")

    def test_manual_visits_disk_then_partition(self):
        w = self._wizard_at_diskmode()
        w.apply(Choose("manual"))
        w.next()
        self.assertEqual(w.current_screen().key, "disk")
        w.apply(Choose("/dev/vda"))
        w.next()
        self.assertEqual(w.current_screen().key, "partition")

    def test_network_screen_shows_injected_status(self):
        w = Wizard(_CATALOG)
        w.set_network_status("OFFLINE")
        w.next()
        screen = w.current_screen()
        self.assertEqual(screen.key, "network")
        self.assertIn("OFFLINE", screen.notice)
        self.assertIn("nmtui", screen.notice)


class TestMandatoryNetwork(unittest.TestCase):
    """The network screen HARD-BLOCKS Next until status is 'connected'."""

    def _at_network(self, status):
        w = Wizard(_CATALOG)
        w.set_network_status(status)
        w.next()  # welcome -> network
        self.assertEqual(w.current_screen().key, "network")
        return w

    def test_offline_blocks_next(self):
        w = self._at_network("OFFLINE")
        with self.assertRaises(ValidationError) as ctx:
            w.next()
        self.assertIn("internet", str(ctx.exception).lower())
        self.assertEqual(w.current_screen().key, "network")  # still here

    def test_initial_checking_status_also_blocks(self):
        w = Wizard(_CATALOG)
        w.next()
        with self.assertRaises(ValidationError):
            w.next()

    def test_connected_allows_next(self):
        w = self._at_network("connected")
        w.next()
        self.assertEqual(w.current_screen().key, "mode")

    def test_reconnect_after_block_unblocks(self):
        w = self._at_network("OFFLINE")
        with self.assertRaises(ValidationError):
            w.next()
        w.set_network_status("connected")  # user fixed it via nmtui
        w.next()
        self.assertEqual(w.current_screen().key, "mode")
