"""Tests for glue_installer.preview (P key) and the preview footer."""

import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.preview import (
    make_show_screenshot, screenshot_under_cursor, screenshots_under_cursor)
from glue_installer.render import (
    FOOTER_TEXT, PREVIEW_FOOTER_TEXT, render_screen,
)
from glue_installer.ui_view import Item, Screen

_Size = namedtuple("Size", "columns lines")


def _screen(kind="radio"):
    return Screen(key="sessions", title="Sessions", kind=kind, items=[
        Item(id="a", label="A", description="with picture", screenshot="screenshots/a.png"),
        Item(id="b", label="B", description="no picture"),
    ])


def _footer(screen, cursor):
    return render_screen(screen, cursor, 100, 30)[-1][0].strip()


class ScreenshotUnderCursorTest(unittest.TestCase):
    def test_item_with_screenshot(self):
        self.assertEqual(screenshot_under_cursor(_screen(), 0), "screenshots/a.png")

    def test_item_without_screenshot(self):
        self.assertIsNone(screenshot_under_cursor(_screen(), 1))

    def test_out_of_range_cursor_is_clamped(self):
        self.assertEqual(screenshot_under_cursor(_screen(), -4), "screenshots/a.png")
        self.assertIsNone(screenshot_under_cursor(_screen(), 99))

    def test_empty_and_form_screens(self):
        self.assertIsNone(screenshot_under_cursor(Screen("x", "X", "info"), 0))
        self.assertIsNone(screenshot_under_cursor(_screen("form"), 0))

    def test_bad_cursor_type(self):
        self.assertIsNone(screenshot_under_cursor(_screen(), "0"))


class PreviewFooterTest(unittest.TestCase):
    def test_footer_advertises_p_only_on_items_with_screenshot(self):
        self.assertEqual(PREVIEW_FOOTER_TEXT, FOOTER_TEXT + "   P preview")
        self.assertIn("P preview", _footer(_screen(), 0))
        self.assertEqual(_footer(_screen(), 1), FOOTER_TEXT)


class ShowScreenshotHookTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        (self.base / "screenshots").mkdir()
        self.png = self.base / "screenshots" / "a.png"
        self.png.write_bytes(b"\x89PNG" + b"0" * 2048)
        self.msgs, self.calls, self.prompts = [], [], []

    def _hook(self, which):
        return make_show_screenshot(
            self.base, which=which, out=self.msgs.append,
            run=lambda argv, **kw: self.calls.append(argv),
            input_fn=lambda p: self.prompts.append(p) or "",
            get_size=lambda fallback=None: _Size(100, 40),
        )

    def test_no_chafa_prints_path_and_runs_nothing(self):
        self._hook(lambda name: None)("screenshots/a.png")
        self.assertEqual(self.calls, [])
        self.assertTrue(any(str(self.png) in m and "chafa" in m for m in self.msgs))
        self.assertEqual(self.prompts, ["Press Enter to return"])

    def test_chafa_gets_absolute_path_and_terminal_size(self):
        self._hook(lambda name: "/usr/bin/chafa")("screenshots/a.png")
        self.assertEqual(len(self.calls), 1)
        argv = self.calls[0]
        self.assertEqual(argv[0], "chafa")
        self.assertIn(str(self.png), argv)
        self.assertIn("100x38", argv)
        self.assertEqual(self.prompts, ["Press Enter to return"])

    def test_missing_file_does_not_raise(self):
        self._hook(lambda name: "/usr/bin/chafa")("screenshots/nope.png")
        self.assertEqual(self.calls, [])
        self.assertTrue(any("not found" in m for m in self.msgs))
        self.assertEqual(self.prompts, ["Press Enter to return"])

    def test_chafa_failure_falls_back_to_message(self):
        def boom(argv, **kw):
            raise OSError("exec failed")
        hook = make_show_screenshot(
            self.base, which=lambda n: "/usr/bin/chafa", run=boom,
            out=self.msgs.append, input_fn=lambda p: "",
            get_size=lambda fb=None: _Size(80, 24))
        hook("screenshots/a.png")
        self.assertTrue(any(str(self.png) in m for m in self.msgs))

    def test_eof_on_input_is_tolerated(self):
        def eof(prompt):
            raise EOFError
        hook = make_show_screenshot(self.base, which=lambda n: None,
                                    out=self.msgs.append, input_fn=eof)
        hook("screenshots/a.png")


class GalleryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        (self.base / "screenshots").mkdir()
        self.names = ["a", "b", "c"]
        for n in self.names:
            (self.base / "screenshots" / f"{n}.png").write_bytes(b"\x89PNG" + b"0" * 64)
        self.msgs, self.calls, self.prompts = [], [], []

    def _hook(self, answers, chafa=True, which=None):
        feed = iter(answers)

        def input_fn(prompt):
            self.prompts.append(prompt)
            try:
                return next(feed)
            except StopIteration:
                raise EOFError

        return make_show_screenshot(
            self.base, which=which or (lambda n: "/usr/bin/chafa" if chafa else None),
            out=self.msgs.append, run=lambda argv, **kw: self.calls.append(argv),
            input_fn=input_fn, get_size=lambda fb=None: _Size(100, 40))

    def _paths(self, n):
        return [f"screenshots/{x}.png" for x in self.names[:n]]

    def test_navigation_n_n_p_q(self):
        self._hook(["n", "n", "p", "q"])(self._paths(3))
        self.assertEqual([p.split("  ")[0] for p in self.prompts],
                         ["Image 1/3", "Image 2/3", "Image 3/3", "Image 2/3"])
        self.assertIn("[n] next  [p] previous  [q] back", self.prompts[0])
        drawn = [Path(c[-1]).stem for c in self.calls]
        self.assertEqual(drawn, ["a", "b", "c", "b"])

    def test_enter_advances_and_exits_on_last(self):
        self._hook(["", ""])(self._paths(2))
        self.assertEqual([p.split("  ")[0] for p in self.prompts], ["Image 1/2", "Image 2/2"])

    def test_p_on_first_and_n_on_last_stay(self):
        self._hook(["p", "n", "n", "x", "q"])(self._paths(2))
        self.assertEqual([p.split("  ")[0] for p in self.prompts],
                         ["Image 1/2", "Image 1/2", "Image 2/2", "Image 2/2", "Image 2/2"])

    def test_single_image_is_compatible(self):
        self._hook([""])(self._paths(1))
        self.assertEqual(self.prompts, ["Press Enter to return"])
        self.assertIn("Image 1/1", self.msgs)
        self._hook([""])(self._paths(1)[0])  # plain str still accepted
        self.assertEqual(len(self.calls), 2)

    def test_missing_file_keeps_indicator(self):
        paths = ["screenshots/a.png", "screenshots/nope.png"]
        self._hook(["n", "q"])(paths)
        self.assertEqual([p.split("  ")[0] for p in self.prompts], ["Image 1/2", "Image 2/2"])
        self.assertTrue(any("not found" in m for m in self.msgs))
        self.assertEqual(len(self.calls), 1)

    def test_no_chafa_keeps_indicator(self):
        self._hook(["n", "n", "q"], chafa=False)(self._paths(3))
        self.assertEqual([p.split("  ")[0] for p in self.prompts],
                         ["Image 1/3", "Image 2/3", "Image 3/3"])
        self.assertEqual(self.calls, [])
        self.assertEqual(sum("not installed" in m for m in self.msgs), 3)

    def test_eof_exits_quietly(self):
        self._hook([])(self._paths(3))
        self.assertEqual(len(self.prompts), 1)

    def test_empty_list_is_a_noop(self):
        self._hook([])([])
        self.assertEqual(self.prompts, [])


class ScreenshotsUnderCursorTest(unittest.TestCase):
    def test_list_and_fallback(self):
        screen = Screen(key="s", title="S", kind="radio", items=[
            Item(id="a", label="A", description="", screenshot="x.png",
                 screenshots=("x.png", "y.png")),
            Item(id="b", label="B", description="", screenshot="z.png"),
            Item(id="c", label="C", description=""),
        ])
        self.assertEqual(screenshots_under_cursor(screen, 0), ("x.png", "y.png"))
        self.assertEqual(screenshots_under_cursor(screen, 1), ("z.png",))
        self.assertEqual(screenshots_under_cursor(screen, 2), ())
        self.assertEqual(screenshots_under_cursor(screen, "0"), ())


class GalleryRenderTest(unittest.TestCase):
    def test_preview_line_counts_images(self):
        screen = Screen(key="s", title="S", kind="radio", items=[
            Item(id="a", label="A", description="d", screenshot="x.png",
                 screenshots=("x.png", "y.png", "z.png"))])
        text = "\n".join(ln for ln, _ in render_screen(screen, 0, 100, 30))
        self.assertIn("Preview: 3 images (P to view)", text)
        self.assertNotIn("x.png", text)


if __name__ == "__main__":
    unittest.main()
