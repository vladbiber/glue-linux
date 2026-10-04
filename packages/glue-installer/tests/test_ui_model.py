"""Unit tests for glue_installer.ui_model (pure wizard state machine)."""

import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.catalog import load_catalog
from glue_installer.plan import Selection, resolve_plan
from glue_installer.ui_model import (
    Choose, Item, Screen, SetFlag, Toggle, ValidationError, Wizard,
    DE_SECTION, SWAP_NOTICE, HIBERNATE_NOTICE,
)

_CATALOG_PATH = _PKG_ROOT / "catalog" / "catalog.json"
_CATALOG = load_catalog(_CATALOG_PATH)

# The REAL catalog only gives gluewc a shell choice (glueqs/noctalia); cover
# the generic shell machinery with a synthetic catalog too: one extra
# shell-capable session + two fake bars.
import dataclasses as _dc

from glue_installer.catalog import Session as _Session, Shell as _Shell

_MOCK_SHELLS = [
    _Shell(id="bar-a", name="Bar A", description="synthetic bar A",
           ease=4, lightness=4, keybindings=[], screenshot="screenshots/bar-a.png",
           packages=["bar-a-pkg"], exec="bar-a-cmd"),
    _Shell(id="bar-b", name="Bar B", description="synthetic bar B",
           ease=3, lightness=5, keybindings=[], screenshot=None,
           packages=["bar-b-pkg"], exec="bar-b-cmd"),
]
_MOCK_SESSION = _Session(
    id="mockwc", name="MockWC", kind="wm", description="synthetic compositor",
    ease=4, lightness=4, keybindings=[], screenshot="screenshots/mockwc.png",
    packages=["mockwc-pkg"], services=[], shell_choices=["bar-a", "bar-b"],
    session_type="wayland", exec="mockwc",
)
_SHELL_CATALOG = _dc.replace(
    _CATALOG,
    sessions=list(_CATALOG.sessions) + [_MOCK_SESSION],
    # keep the real shells too — gluewc references glueqs/noctalia
    shells=_MOCK_SHELLS + list(_CATALOG.shells),
)


def _new_wizard(catalog=_CATALOG) -> Wizard:
    w = Wizard(catalog)
    w.set_network_status("connected")  # network screen blocks Next otherwise
    return w


def _advance_to(wizard: Wizard, key: str) -> None:
    """Drive next() until current screen key == key (choices must be valid)."""
    for _ in range(30):
        if wizard.current_screen().key == key:
            return
        wizard.next()
    raise AssertionError(f"Never reached screen '{key}'")


def _drive_happy_path(sessions=("gluewc", "nvwm"), shells=None,
                      bluetooth=True, gaming=True, catalog=_CATALOG) -> Wizard:
    """Full custom-mode walk: cachyos, dinit, given sessions/shells."""
    w = _new_wizard(catalog)
    w.next()  # welcome -> network
    w.next()  # network -> mode (custom preselected)
    w.next()  # mode -> kernel
    w.apply(Choose("linux-cachyos"))
    w.next()  # kernel -> init
    w.apply(Choose("dinit"))
    w.next()  # init -> sessions
    # gluewc starts preselected: drop it unless asked for, toggle the rest
    if "gluewc" not in sessions:
        w.apply(Toggle("gluewc"))
    for sid in sessions:
        if sid != "gluewc":
            w.apply(Toggle(sid))
    w.next()  # sessions -> first shell screen (or support)
    shells = shells or {}
    while w.current_screen().key.startswith("shell:"):
        sid = w.current_screen().key.split(":", 1)[1]
        # explicit choice if given, else the screen's first shell
        choice = shells.get(sid) or w.current_screen().items[0].id
        w.apply(Choose(choice))
        w.next()
    assert w.current_screen().key == "support"
    if bluetooth:
        w.apply(Toggle("bluetooth"))
    w.next()  # support -> gaming
    w.apply(SetFlag(gaming))
    w.next()  # gaming -> scheduler (gaming on) or summary
    if w.current_screen().key == "scheduler":
        w.next()  # default scheduler preselected
    w.next()  # summary -> finished
    return w


class TestScreenSequence(unittest.TestCase):

    def test_welcome_is_first_screen(self):
        w = _new_wizard()
        screen = w.current_screen()
        self.assertEqual(screen.key, "welcome")
        self.assertEqual(screen.kind, "info")
        self.assertIn("Glue", screen.notice)

    def test_full_custom_walk_visits_expected_keys(self):
        w = _new_wizard(_SHELL_CATALOG)
        visited = [w.current_screen().key]
        w.next(); visited.append(w.current_screen().key)   # network
        w.next(); visited.append(w.current_screen().key)   # mode
        w.next(); visited.append(w.current_screen().key)   # kernel
        w.apply(Choose("linux-cachyos"))
        w.next(); visited.append(w.current_screen().key)   # init
        w.apply(Choose("dinit"))
        w.next(); visited.append(w.current_screen().key)   # sessions
        w.apply(Toggle("mockwc"))  # gluewc already preselected
        w.next(); visited.append(w.current_screen().key)   # shell:gluewc
        w.apply(Choose("noctalia"))
        w.next(); visited.append(w.current_screen().key)   # shell:mockwc
        w.apply(Choose("bar-a"))
        w.next(); visited.append(w.current_screen().key)   # support
        w.next(); visited.append(w.current_screen().key)   # gaming
        w.next(); visited.append(w.current_screen().key)   # summary
        self.assertEqual(visited, [
            "welcome", "network", "mode", "kernel", "init", "sessions",
            "shell:gluewc", "shell:mockwc", "support", "gaming", "summary",
        ])

    def test_mode_screen_preselects_custom(self):
        w = _new_wizard()
        w.next()  # -> network
        w.next()  # -> mode
        screen = w.current_screen()
        self.assertEqual(screen.key, "mode")
        selected = [i.id for i in screen.items if i.selected]
        self.assertEqual(selected, ["custom"])

    def test_shell_choice_covers_exactly_the_qualifying_sessions(self):
        w = _drive_happy_path(sessions=("mockwc", "nvwm"),
                              catalog=_SHELL_CATALOG)
        # only mockwc has shell_choices; nvwm must not appear in the mapping
        sel = w.to_selection()
        self.assertEqual(list(sel.shell_choice.keys()), ["mockwc"])

    def test_shell_screens_only_for_shell_capable_sessions(self):
        w = _new_wizard()
        w.next(); w.next(); w.next()  # welcome -> network -> mode -> kernel
        w.apply(Choose("linux-cachyos")); w.next()
        w.apply(Choose("dinit")); w.next()
        w.apply(Toggle("gluewc"))  # deselect the shell-capable default
        w.apply(Toggle("nvwm"))
        w.apply(Toggle("kde-plasma"))
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
        # 5.1b: explicit multi-select + greeter wording
        self.assertIn("several at once", screen.notice)
        self.assertIn("login screen (greeter)", screen.notice)

    def test_gluewc_preselected_by_default(self):
        _, screen = self._sessions_screen()
        selected = [i.id for i in screen.items if i.selected]
        self.assertEqual(selected, ["gluewc"])

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
        w.apply(Toggle("nvwm"))
        item = next(i for i in w.current_screen().items if i.id == "nvwm")
        self.assertTrue(item.selected)
        w.apply(Toggle("nvwm"))
        item = next(i for i in w.current_screen().items if i.id == "nvwm")
        self.assertFalse(item.selected)


class TestShellScreens(unittest.TestCase):
    """Shell-choice machinery: real catalog (gluewc -> glueqs/noctalia) plus
    the synthetic _SHELL_CATALOG for the generic multi-shell paths."""

    def _to_shell_screen(self):
        w = _new_wizard(_SHELL_CATALOG)
        _advance_to(w, "kernel")
        w.apply(Choose("linux-cachyos")); w.next()
        w.apply(Choose("dinit")); w.next()
        w.apply(Toggle("gluewc"))  # deselect the default: mockwc only
        w.apply(Toggle("mockwc"))
        w.next()
        return w

    def test_real_catalog_gluewc_offers_glueqs_then_noctalia(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        w.apply(Choose("linux-cachyos")); w.next()
        w.apply(Choose("dinit")); w.next()
        w.next()  # gluewc preselected -> its shell screen
        screen = w.current_screen()
        self.assertEqual(screen.key, "shell:gluewc")
        # exactly glueqs (recommended, preselected) + noctalia; no "none"
        self.assertEqual([i.id for i in screen.items], ["glueqs", "noctalia"])
        glueqs = screen.items[0]
        self.assertTrue(glueqs.recommended)
        self.assertTrue(glueqs.selected)
        w.next()  # the preselected default is valid without a Choose
        self.assertEqual(w.current_screen().key, "support")

    def test_shell_items_carry_catalog_data(self):
        w = self._to_shell_screen()
        screen = w.current_screen()
        self.assertEqual(screen.key, "shell:mockwc")
        shells = {s.id: s for s in _SHELL_CATALOG.shells}
        self.assertEqual([i.id for i in screen.items], ["bar-a", "bar-b"])
        for item in screen.items:
            shell = shells[item.id]
            self.assertEqual(item.ease, shell.ease)
            self.assertEqual(item.lightness, shell.lightness)
            self.assertEqual(item.screenshot, shell.screenshot)
            if shell.keybindings:
                self.assertIsNotNone(item.keybinds)

    def test_shell_screen_preselects_recommended_first_shell(self):
        # toggling a shell-capable session auto-picks its first (recommended)
        # shell, so the shell screen never blocks Next
        w = self._to_shell_screen()
        screen = w.current_screen()
        self.assertEqual([i.id for i in screen.items if i.selected], ["bar-a"])
        self.assertTrue(screen.items[0].recommended)
        w.next()
        self.assertEqual(w.current_screen().key, "support")

    def test_deselect_session_drops_shell_screen_and_choice(self):
        w = self._to_shell_screen()
        w.apply(Choose("bar-a"))
        w.back()  # back to sessions
        self.assertEqual(w.current_screen().key, "sessions")
        w.apply(Toggle("mockwc"))   # deselect
        w.apply(Toggle("nvwm"))     # keep a session so next() is valid
        w.next()
        self.assertEqual(w.current_screen().key, "support")
        w.next(); w.next(); w.next()  # support -> gaming -> summary -> done
        sel = w.to_selection()
        self.assertEqual(sel.shell_choice, {})
        self.assertEqual(sel.session_ids, ["nvwm"])
        resolve_plan(_SHELL_CATALOG, sel)  # must not raise


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
        w.apply(Toggle("gluewc"))  # deselect the preselected default
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
            w.apply(Choose("nvwm"))

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
        # gaming is on in the happy path: the scheduler screen precedes summary
        self.assertEqual(w.current_screen().key, "scheduler")


class TestSupportAndGaming(unittest.TestCase):

    def test_support_screen_lists_bluetooth_with_catalog_default(self):
        w = _new_wizard()
        _advance_to(w, "kernel")
        w.apply(Choose("linux-cachyos")); w.next()
        w.apply(Choose("dinit")); w.next()
        w.apply(Toggle("gluewc")); w.apply(Toggle("nvwm")); w.next()
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
        w.apply(Toggle("gluewc")); w.apply(Toggle("nvwm")); w.next()
        w.next()  # support -> gaming
        screen = w.current_screen()
        self.assertEqual(screen.kind, "toggle")
        self.assertFalse(screen.items[0].selected)
        w.apply(SetFlag(True))
        self.assertTrue(w.current_screen().items[0].selected)


class TestSummaryAndSelection(unittest.TestCase):

    def test_happy_path_selection_resolves_with_expected_packages(self):
        w = _drive_happy_path(
            sessions=("gluewc", "mockwc"),
            shells={"mockwc": "bar-a"},
            bluetooth=True, gaming=True,
            catalog=_SHELL_CATALOG,
        )
        sel = w.to_selection()
        self.assertEqual(sel.kernel_id, "linux-cachyos")
        self.assertEqual(sel.init_id, "dinit")
        self.assertEqual(sel.session_ids, ["gluewc", "mockwc"])
        # gluewc's screen keeps its preselected recommended shell (glueqs)
        self.assertEqual(sel.shell_choice,
                         {"gluewc": "glueqs", "mockwc": "bar-a"})
        self.assertEqual(sel.support_ids, ["app-store", "bluetooth"])
        self.assertTrue(sel.gaming)
        self.assertFalse(sel.minimal)
        plan = resolve_plan(_SHELL_CATALOG, sel)  # must not raise PlanError
        for pkg in ("mockwc-pkg", "gluewc", "bar-a-pkg", "glueqs",
                    "linux-cachyos", "dinit", "bluez",
                    "steam", "greetd", "fastfetch"):
            self.assertIn(pkg, plan.packages)

    def test_summary_items_reflect_choices(self):
        w = _drive_happy_path()
        # to_selection() is only valid while finished — capture it before back()
        selected_ids = w.to_selection().session_ids
        w.back()  # back onto summary
        w.next()  # forward: still summary until finishing next()
        summary = w.current_screen()
        self.assertEqual(summary.kind, "summary")
        labels = "\n".join(i.label for i in summary.items)
        self.assertIn("CachyOS", labels)
        self.assertIn("dinit", labels)
        # 5.1b: the summary lists EVERY selected session (by display name)
        names = {s.id: s.name for s in _CATALOG.sessions}
        for sid in selected_ids:
            self.assertIn(f"Session: {names[sid]}", labels)
        self.assertIn("Session: gluewc (shell: glueqs)", labels)
        self.assertIn("Session: nvwm", labels)
        self.assertIn("Bluetooth", labels)
        self.assertIn("Gaming Mode: on", labels)

    def test_every_completed_run_resolves(self):
        # single session, no shell needed, runit, zen, no extras
        w = _new_wizard()
        _advance_to(w, "kernel")
        w.apply(Choose("linux-zen")); w.next()
        w.apply(Choose("runit")); w.next()
        w.apply(Toggle("gluewc"))  # drop the default: no shell screen
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
        w = _new_wizard(_SHELL_CATALOG)
        _advance_to(w, "kernel")
        w.apply(Choose("linux-cachyos")); w.next()
        w.apply(Choose("dinit")); w.next()
        w.apply(Toggle("gluewc"))  # deselect default: only mockwc's screen
        w.apply(Toggle("mockwc")); w.next()
        w.apply(Choose("bar-a"))
        w.back(); w.back(); w.back(); w.back()  # back to mode
        self.assertEqual(w.current_screen().key, "mode")
        w.apply(Choose("minimal"))
        while not w.is_finished():
            w.next()
        sel = w.to_selection()
        self.assertEqual(sel.session_ids, [])
        self.assertEqual(sel.shell_choice, {})
        resolve_plan(_SHELL_CATALOG, sel)  # must not raise




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
        w.next()  # partition -> swap
        self.assertEqual(w.current_screen().key, "swap")
        w.next()  # swap -> summary
        self.assertEqual(w.current_screen().key, "summary")
        self.assertIn("/dev/vda2", w.current_screen().items[-2].label)
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


class TestTimezonePrefill(unittest.TestCase):
    """prefill_timezone: the TUI injects the GeoIP timezone once the network
    screen confirms connectivity; only the 'UTC' placeholder is replaced."""

    def _wizard_with_identity(self, tz_default="UTC"):
        w = Wizard(_CATALOG, ask_identity=True,
                   identity_defaults={"timezone": tz_default})
        w.set_network_status("connected")
        return w

    def test_replaces_utc_placeholder(self):
        w = self._wizard_with_identity()
        self.assertTrue(w.prefill_timezone("Europe/Bucharest"))
        self.assertEqual(w._forms["timezone"].value, "Europe/Bucharest")

    def test_never_clobbers_a_real_default_or_user_input(self):
        w = self._wizard_with_identity(tz_default="Europe/London")
        self.assertFalse(w.prefill_timezone("Europe/Bucharest"))
        self.assertEqual(w._forms["timezone"].value, "Europe/London")

    def test_rejects_invalid_or_empty_values(self):
        w = self._wizard_with_identity()
        self.assertFalse(w.prefill_timezone(""))
        self.assertFalse(w.prefill_timezone(None))
        self.assertFalse(w.prefill_timezone("not a tz!!"))
        self.assertEqual(w._forms["timezone"].value, "UTC")

    def test_noop_without_identity_forms(self):
        w = _new_wizard()  # ask_identity defaults to False
        self.assertFalse(w.prefill_timezone("Europe/Bucharest"))


class TestSchedulerScreen(unittest.TestCase):
    """1.4: CPU scheduler radio, only on gaming installs."""

    def _to_gaming(self) -> Wizard:
        w = _new_wizard()
        _advance_to(w, "kernel")
        w.apply(Choose("linux-cachyos")); w.next()
        w.apply(Choose("dinit")); w.next()
        w.apply(Toggle("gluewc")); w.apply(Toggle("nvwm")); w.next()
        self.assertEqual(w.current_screen().key, "support")
        w.next()
        self.assertEqual(w.current_screen().key, "gaming")
        return w

    def test_no_scheduler_screen_when_gaming_off(self):
        w = self._to_gaming()
        w.apply(SetFlag(False)); w.next()
        self.assertEqual(w.current_screen().key, "summary")

    def test_scheduler_screen_after_gaming_with_default_preselected(self):
        w = self._to_gaming()
        w.apply(SetFlag(True)); w.next()
        screen = w.current_screen()
        self.assertEqual(screen.key, "scheduler")
        self.assertEqual(screen.kind, "radio")
        self.assertEqual([i.id for i in screen.items],
                         ["scx_lavd", "scx_bpfland", "none"])
        self.assertEqual([i.id for i in screen.items if i.selected], ["scx_lavd"])
        lavd = next(i for i in screen.items if i.id == "scx_lavd")
        self.assertTrue(lavd.recommended)
        self.assertIn("recommended", lavd.label)
        w.next()  # the default is valid without any Choose
        self.assertEqual(w.current_screen().key, "summary")

    def test_choice_reaches_summary_selection_and_plan(self):
        w = self._to_gaming()
        w.apply(SetFlag(True)); w.next()
        w.apply(Choose("scx_bpfland")); w.next()
        self.assertIn("CPU scheduler: scx_bpfland",
                      [i.label for i in w.current_screen().items])
        w.next()
        sel = w.to_selection()
        self.assertEqual(sel.scheduler, "scx_bpfland")
        plan = resolve_plan(_CATALOG, sel)
        self.assertIn("scx-scheds", plan.packages)
        conf = next(f for f in plan.files if f.path == "/etc/default/scx")
        self.assertIn("SCX_SCHEDULER=scx_bpfland", conf.content)

    def test_none_keeps_kernel_scheduler(self):
        w = self._to_gaming()
        w.apply(SetFlag(True)); w.next()
        w.apply(Choose("none")); w.next(); w.next()
        plan = resolve_plan(_CATALOG, w.to_selection())
        self.assertNotIn("scx-scheds", plan.packages)
        self.assertNotIn("scx", plan.services)

    def test_unknown_scheduler_rejected(self):
        w = self._to_gaming()
        w.apply(SetFlag(True)); w.next()
        with self.assertRaises(ValidationError):
            w.apply(Choose("scx_rusty"))

    def test_gaming_off_again_drops_scheduler_from_summary(self):
        w = self._to_gaming()
        w.apply(SetFlag(True)); w.next(); w.next()
        w.back(); w.back()
        self.assertEqual(w.current_screen().key, "gaming")
        w.apply(SetFlag(False)); w.next()
        labels = [i.label for i in w.current_screen().items]
        self.assertFalse(any(lb.startswith("CPU scheduler") for lb in labels))


class _FakeDisk:
    path = "/dev/sdx"
    name = "sdx"
    size_bytes = 2 ** 40
    model = "x"
    is_removable = False
    has_mounted_partitions = False


def _swap_wizard(is_laptop=False, disks=True) -> Wizard:
    w = Wizard(_CATALOG, disks=[_FakeDisk()] if disks else None,
               is_laptop=is_laptop)
    w.set_network_status("connected")
    return w


def _to_swap(w: Wizard) -> None:
    w.next(); w.next(); w.next()  # welcome, network, mode -> kernel
    w.apply(Choose("linux-cachyos")); w.next()
    w.apply(Choose("dinit")); w.next()
    _advance_to(w, "diskmode")
    w.next()  # diskmode -> disk
    w.apply(Choose("/dev/sdx"))
    w.next()


class TestSwapScreens(unittest.TestCase):

    def test_swap_after_disk_before_summary(self):
        keys = _swap_wizard()._screen_keys()
        self.assertEqual(keys[-3:], ["disk", "swap", "summary"])
        self.assertNotIn("hibernate", keys)

    def test_no_swap_screens_without_disks(self):
        keys = _swap_wizard(is_laptop=True, disks=False)._screen_keys()
        self.assertNotIn("swap", keys)
        self.assertNotIn("hibernate", keys)

    def test_swap_auto_preselected_and_recommended(self):
        w = _swap_wizard(); _to_swap(w)
        s = w.current_screen()
        self.assertEqual((s.key, s.kind, s.notice), ("swap", "radio", SWAP_NOTICE))
        self.assertEqual([i.id for i in s.items], ["auto", "zram", "none"])
        auto = s.items[0]
        self.assertTrue(auto.selected and auto.recommended)
        self.assertFalse(any(i.selected for i in s.items[1:]))

    def test_choose_zram_and_none(self):
        for mode in ("zram", "none"):
            w = _swap_wizard(); _to_swap(w)
            w.apply(Choose(mode))
            self.assertEqual([i.id for i in w.current_screen().items
                              if i.selected], [mode])
            w.next(); w.next()
            self.assertEqual(w.to_selection().swap_mode, mode)

    def test_choose_bogus_swap_raises(self):
        w = _swap_wizard(); _to_swap(w)
        with self.assertRaises(ValidationError):
            w.apply(Choose("bogus"))

    def test_hibernate_only_on_laptop_with_auto(self):
        w = _swap_wizard(is_laptop=True)
        self.assertEqual(w._screen_keys()[-3:], ["swap", "hibernate", "summary"])
        _to_swap(w)
        w.apply(Choose("zram"))
        self.assertNotIn("hibernate", w._screen_keys())
        w.apply(Choose("auto"))
        self.assertIn("hibernate", w._screen_keys())

    def test_switching_away_from_auto_clears_hibernate(self):
        w = _swap_wizard(is_laptop=True); _to_swap(w)
        w.next()
        self.assertEqual(w.current_screen().key, "hibernate")
        w.apply(SetFlag(True))
        w.back()
        w.apply(Choose("none"))
        w.apply(Choose("auto"))
        w.next()
        self.assertFalse(w.current_screen().items[0].selected)

    def test_hibernate_flag_reaches_selection_and_summary(self):
        w = _swap_wizard(is_laptop=True); _to_swap(w)
        w.next()
        s = w.current_screen()
        self.assertEqual((s.kind, s.title, s.notice),
                         ("toggle", "Hibernation", HIBERNATE_NOTICE))
        self.assertEqual([i.id for i in s.items], ["hibernate"])
        w.apply(SetFlag(True))
        self.assertFalse(w._gaming)
        w.next()
        labels = [i.label for i in w.current_screen().items]
        self.assertIn("Swap: Automatic (zram + disk)", labels)
        self.assertIn("Hibernation: on", labels)
        w.next()
        sel = w.to_selection()
        self.assertEqual((sel.swap_mode, sel.hibernate), ("auto", True))

    def test_summary_zram_only_and_defaults(self):
        w = _swap_wizard(); _to_swap(w)
        w.apply(Choose("zram")); w.next()
        labels = [i.label for i in w.current_screen().items]
        self.assertIn("Swap: zram only", labels)
        self.assertFalse(any(l.startswith("Hibernation") for l in labels))
        w2 = _swap_wizard(); _to_swap(w2); w2.next(); w2.next()
        sel = w2.to_selection()
        self.assertEqual((sel.swap_mode, sel.hibernate), ("auto", False))

    def test_selection_defaults_stay_valid(self):
        s = Selection(kernel_id="linux-cachyos", init_id="dinit",
                      session_ids=[], shell_choice={}, support_ids=[],
                      gaming=False, minimal=True)
        self.assertEqual((s.swap_mode, s.hibernate), ("auto", False))



if __name__ == "__main__":
    unittest.main()
