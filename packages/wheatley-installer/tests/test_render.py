"""Unit tests for wheatley_installer.render (pure line renderer, no curses)."""

import subprocess
import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from wheatley_installer.catalog import load_catalog
from wheatley_installer.render import FOOTER_TEXT, STYLES, render_screen
from wheatley_installer.ui_model import (
    Choose, SetFlag, Toggle, Wizard, DE_SECTION,
)

_CATALOG_PATH = _PKG_ROOT / "catalog" / "catalog.json"
_CATALOG = load_catalog(_CATALOG_PATH)

W, H = 80, 24


def _wizard_at(key: str) -> Wizard:
    """Drive a fresh wizard along the happy path until screen `key`."""
    w = Wizard(_CATALOG)
    w.set_network_status("connected")  # network screen blocks Next otherwise
    for _ in range(30):
        screen = w.current_screen()
        if screen.key == key:
            return w
        if screen.key == "kernel":
            w.apply(Choose("linux-cachyos"))
        elif screen.key == "init":
            w.apply(Choose("dinit"))
        elif screen.key == "sessions":
            w.apply(Toggle("niri"))
            w.apply(Toggle("sway"))
        elif screen.key.startswith("shell:"):
            w.apply(Choose("noctalia"))
        elif screen.key == "gaming":
            w.apply(SetFlag(True))
        w.next()
    raise AssertionError(f"Never reached screen '{key}'")


def _texts(lines):
    return [text for text, _ in lines]


def _joined(lines):
    return " ".join(_texts(lines))


class TestRenderBasics(unittest.TestCase):
    def test_title_is_first_line_with_title_style(self):
        screen = Wizard(_CATALOG).current_screen()
        lines = render_screen(screen, 0, W, H)
        self.assertEqual(lines[0], (screen.title, "title"))

    def test_all_styles_are_known_tags(self):
        for key in ("welcome", "kernel", "sessions", "gaming", "summary"):
            screen = _wizard_at(key).current_screen()
            for text, style in render_screen(screen, 0, W, H, error="boom"):
                self.assertIn(style, STYLES)

    def test_footer_present_with_key_hints(self):
        screen = _wizard_at("kernel").current_screen()
        lines = render_screen(screen, 0, W, H)
        self.assertEqual(lines[-1], (FOOTER_TEXT, "footer"))
        for hint in ("move", "Space", "Enter", "back", "Q quit"):
            self.assertIn(hint, lines[-1][0])

    def test_determinism_same_inputs_same_output(self):
        screen = _wizard_at("sessions").current_screen()
        first = render_screen(screen, 2, W, H, error="err")
        second = render_screen(screen, 2, W, H, error="err")
        self.assertEqual(first, second)

    def test_error_line_rendered_with_error_style(self):
        screen = _wizard_at("kernel").current_screen()
        lines = render_screen(screen, 0, W, H, error="Select a kernel to continue.")
        error_lines = [t for t, s in lines if s == "error"]
        self.assertEqual(error_lines, ["Select a kernel to continue."])

    def test_info_screen_has_notice_but_no_item_rows(self):
        screen = Wizard(_CATALOG).current_screen()  # welcome
        lines = render_screen(screen, 0, W, H)
        self.assertTrue(any(s == "notice" for _, s in lines))
        self.assertFalse(any(s.startswith("item") for _, s in lines))


class TestCursorAndGlyphs(unittest.TestCase):
    def test_cursor_marker_on_cursor_row_only(self):
        screen = _wizard_at("kernel").current_screen()
        lines = render_screen(screen, 1, W, H)
        cursor_rows = [t for t, s in lines if s == "item_cursor"]
        self.assertEqual(len(cursor_rows), 1)
        self.assertTrue(cursor_rows[0].startswith("> "))
        for text, style in lines:
            if style in ("item", "item_selected", "recommended"):
                self.assertTrue(text.startswith("  "))

    def test_radio_glyphs_reflect_selection(self):
        w = _wizard_at("kernel")
        w.apply(Choose("linux-zen"))
        rendered = _texts(render_screen(w.current_screen(), 0, W, H))
        self.assertTrue(any("(o) Zen Kernel" in t for t in rendered))
        self.assertTrue(any("( ) CachyOS Kernel" in t for t in rendered))

    def test_multi_checkbox_glyphs_reflect_selection(self):
        screen = _wizard_at("support").current_screen()
        self.assertEqual(screen.kind, "multi")
        rendered = _texts(render_screen(screen, 0, W, H))
        self.assertTrue(any("[ ] Bluetooth Support" in t for t in rendered))
        w = _wizard_at("support")
        w.apply(Toggle("bluetooth"))
        rendered = _texts(render_screen(w.current_screen(), 0, W, H))
        self.assertTrue(any("[x] Bluetooth Support" in t for t in rendered))

    def test_toggle_screen_uses_radio_glyphs(self):
        w = _wizard_at("gaming")
        rendered = _texts(render_screen(w.current_screen(), 0, W, H))
        self.assertTrue(any("( ) Gaming Mode" in t for t in rendered))
        w.apply(SetFlag(True))
        rendered = _texts(render_screen(w.current_screen(), 0, W, H))
        self.assertTrue(any("(o) Gaming Mode" in t for t in rendered))

    def test_cursor_out_of_range_is_clamped(self):
        screen = _wizard_at("kernel").current_screen()
        lines = render_screen(screen, 99, W, H)
        self.assertEqual(len([t for t, s in lines if s == "item_cursor"]), 1)
        lines = render_screen(screen, -5, W, H)
        self.assertEqual(len([t for t, s in lines if s == "item_cursor"]), 1)


class TestRecommendedAndSections(unittest.TestCase):
    def test_dinit_row_carries_recommended_marker(self):
        screen = _wizard_at("init").current_screen()
        lines = render_screen(screen, 1, W, H)  # cursor away from dinit row
        marked = [t for t, s in lines if s == "recommended"]
        self.assertEqual(len(marked), 1)
        self.assertIn("dinit", marked[0])
        self.assertIn("(recommended)", marked[0])

    def test_recommended_suffix_appended_when_missing_from_label(self):
        screen = _wizard_at("mode").current_screen()
        lines = render_screen(screen, 1, W, H)
        marked = [t for t, s in lines if s == "recommended"]
        self.assertEqual(len(marked), 1)
        self.assertIn("Custom install (recommended)", marked[0])

    def test_de_section_header_separates_wm_and_de_items(self):
        screen = _wizard_at("sessions").current_screen()
        lines = render_screen(screen, 0, W, 60)
        styles = [s for _, s in lines]
        self.assertIn("section", styles)
        section_idx = styles.index("section")
        self.assertIn(DE_SECTION, lines[section_idx][0])
        before = _joined(lines[:section_idx])
        after = _joined(lines[section_idx:])
        self.assertIn("Sway", before)
        self.assertIn("KDE Plasma", after)
        self.assertIn("XFCE", after)


class TestNoticeAndDetail(unittest.TestCase):
    def test_sessions_notice_contains_multi_select_login_text(self):
        screen = _wizard_at("sessions").current_screen()
        lines = render_screen(screen, 0, W, 60)
        notice = " ".join(t for t, s in lines if s == "notice")
        self.assertIn("MULTIPLE window managers", notice)
        self.assertIn("pick which one to use at the login screen", notice)

    def test_notice_is_word_wrapped_to_width(self):
        screen = _wizard_at("sessions").current_screen()
        lines = render_screen(screen, 0, 40, 60)
        notice_lines = [t for t, s in lines if s == "notice"]
        self.assertGreater(len(notice_lines), 1)
        for text in notice_lines:
            self.assertLessEqual(len(text), 40)

    def test_detail_block_shows_ease_lightness_keys_preview(self):
        screen = _wizard_at("sessions").current_screen()
        idx = [i.id for i in screen.items].index("niri")
        detail = " ".join(t for t, s in render_screen(screen, idx, 200, 60)
                          if s == "detail")
        self.assertIn("Ease: 4/5", detail)
        self.assertIn("Lightness: 4/5", detail)
        self.assertIn("Keys: Super+Return — Open terminal", detail)
        self.assertIn("Preview: screenshots/niri.png", detail)
        self.assertIn("scrollable-tiling Wayland compositor", detail)

    def test_detail_follows_the_cursor(self):
        screen = _wizard_at("sessions").current_screen()
        ids = [i.id for i in screen.items]
        sway = " ".join(t for t, s in render_screen(screen, ids.index("sway"), W, 60)
                        if s == "detail")
        self.assertIn("i3-compatible", sway)
        self.assertNotIn("niri.png", sway)


class TestGeometry(unittest.TestCase):
    def test_scrolling_keeps_cursor_visible_at_small_height(self):
        screen = _wizard_at("sessions").current_screen()
        last = len(screen.items) - 1
        for height in range(5, 20):
            lines = render_screen(screen, last, W, height)
            self.assertLessEqual(len(lines), height)
            cursor_rows = [t for t, s in lines if s == "item_cursor"]
            self.assertEqual(len(cursor_rows), 1,
                             f"cursor row lost at height={height}")
            self.assertIn(screen.items[last].label, cursor_rows[0])

    def test_all_lines_truncated_to_narrow_width(self):
        screen = _wizard_at("sessions").current_screen()
        lines = render_screen(screen, 2, 24, 60)
        self.assertTrue(all(len(t) <= 24 for t in _texts(lines)))
        self.assertTrue(any(t.endswith("…") for t in _texts(lines)))

    def test_never_exceeds_height(self):
        for key in ("welcome", "sessions", "summary"):
            screen = _wizard_at(key).current_screen()
            for height in (1, 2, 3, 8, 24):
                lines = render_screen(screen, 0, W, height)
                self.assertLessEqual(len(lines), height)

    def test_invalid_geometry_raises_value_error(self):
        screen = _wizard_at("kernel").current_screen()
        for width, height in ((0, 24), (-1, 24), (80, 0), (80, -3)):
            with self.assertRaises(ValueError):
                render_screen(screen, 0, width, height)
        with self.assertRaises(ValueError):
            render_screen("not a screen", 0, W, H)
        with self.assertRaises(ValueError):
            render_screen(screen, "zero", W, H)


class TestSummaryScreen(unittest.TestCase):
    def test_summary_lists_all_choices(self):
        w = _wizard_at("summary")
        text = _joined(render_screen(w.current_screen(), 0, 120, 60))
        self.assertIn("Kernel: CachyOS Kernel", text)
        self.assertIn("Init: dinit", text)
        self.assertIn("Session: Niri", text)
        self.assertIn("Gaming Mode: on", text)


class TestPurity(unittest.TestCase):
    def test_importing_render_does_not_load_curses(self):
        code = (
            "import sys; sys.path.insert(0, {root!r})\n"
            "import wheatley_installer.render\n"
            "assert 'curses' not in sys.modules, 'render pulled in curses'\n"
            "print('clean')\n"
        ).format(root=str(_PKG_ROOT))
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clean", result.stdout)


class TestFormSecrecy(unittest.TestCase):
    """With 'hunter2!' typed, no rendered line ever contains the plaintext."""

    _PASSWORD = "hunter2!"

    def _wizard_with_password(self):
        w = Wizard(_CATALOG, ask_identity=True)
        w.set_network_status("connected")
        w.next()                              # welcome -> network
        w.next()                              # network -> mode
        w.apply(Choose("minimal"))
        w.next()                              # -> kernel
        w.apply(Choose("linux-cachyos"))
        w.next()                              # -> init
        w.apply(Choose("dinit"))
        w.next()                              # -> support
        w.next()                              # -> form:hostname (prefilled)
        w.next()                              # -> form:username
        for ch in "alice":
            w.feed_char(ch)
        w.next()                              # -> form:password
        for ch in self._PASSWORD:
            w.feed_char(ch)
        return w

    def _rendered(self, w):
        return _joined(render_screen(w.current_screen(), 0, 120, 40))

    def test_password_screen_masks_plaintext(self):
        text = self._rendered(self._wizard_with_password())
        self.assertNotIn(self._PASSWORD, text)
        self.assertIn("********", text)

    def test_confirm_screen_masks_plaintext(self):
        w = self._wizard_with_password()
        w.next()                              # -> form:password_confirm
        for ch in self._PASSWORD:
            w.feed_char(ch)
        text = self._rendered(w)
        self.assertNotIn(self._PASSWORD, text)
        self.assertIn("********", text)

    def test_summary_masks_plaintext(self):
        w = self._wizard_with_password()
        w.next()                              # -> confirm
        for ch in self._PASSWORD:
            w.feed_char(ch)
        w.next()                              # -> locale (default valid)
        w.next()                              # -> timezone (default valid)
        w.next()                              # -> summary
        self.assertEqual(w.current_screen().key, "summary")
        text = self._rendered(w)
        self.assertNotIn(self._PASSWORD, text)
        self.assertIn("Password: ********", text)

    def test_form_screen_shows_form_footer_and_error(self):
        from wheatley_installer.render import FORM_FOOTER_TEXT
        w = self._wizard_with_password()
        screen = w.current_screen()
        self.assertEqual(screen.kind, "form")
        text = self._rendered(w)
        self.assertIn(FORM_FOOTER_TEXT, text)
        lines = render_screen(screen, 0, 120, 40, error="Nope.")
        self.assertIn(("Nope.", "error"), lines)


if __name__ == "__main__":
    unittest.main()
