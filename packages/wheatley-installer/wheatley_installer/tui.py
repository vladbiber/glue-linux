"""
Thin curses driver for the Wheatley Linux installer (ADR-5).

The ONLY module that imports curses. It contains no business logic and no
knowledge of catalog contents: it maps key presses to ui_model events,
paints the styled lines produced by render.render_screen with the amber
palette, and returns the finished Selection (or None on quit).
"""

from __future__ import annotations

import curses
from typing import Optional

from wheatley_installer.render import render_screen
from wheatley_installer.ui_model import (
    Choose, SetFlag, Toggle, ValidationError, Wizard, WizardResult,
)

# Amber palette (#100A02 / #A66900 / #F1B00A), scaled to curses 0-1000.
_RGB_BG = (63, 39, 8)
_RGB_DIM = (651, 412, 0)
_RGB_BRIGHT = (945, 690, 39)

# Custom color slots (only used when the terminal can change colors).
_SLOT_BG, _SLOT_DIM, _SLOT_BRIGHT = 16, 17, 18

# Color pairs: 1 = bright-on-bg, 2 = dim-on-bg, 3 = cursor (bg-on-bright),
# 4 = error (red-on-bg).
_PAIR_FOR_STYLE = {
    "title": 1,
    "notice": 2,
    "item": 2,
    "item_selected": 1,
    "item_cursor": 3,
    "detail": 2,
    "recommended": 1,
    "section": 1,
    "error": 4,
    "footer": 2,
}

_ATTR_FOR_STYLE = {
    "title": curses.A_BOLD,
    "item_cursor": curses.A_BOLD,
    "recommended": curses.A_BOLD,
    "section": curses.A_BOLD,
}

_QUIT_PROMPT = "Quit installer? [y/N]"


def _init_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
    except curses.error:
        pass
    bg, dim, bright = -1, curses.COLOR_YELLOW, curses.COLOR_YELLOW
    cursor_fg = curses.COLOR_BLACK
    if curses.can_change_color() and curses.COLORS > _SLOT_BRIGHT:
        try:
            curses.init_color(_SLOT_BG, *_RGB_BG)
            curses.init_color(_SLOT_DIM, *_RGB_DIM)
            curses.init_color(_SLOT_BRIGHT, *_RGB_BRIGHT)
            bg, dim, bright = _SLOT_BG, _SLOT_DIM, _SLOT_BRIGHT
            cursor_fg = _SLOT_BG
        except curses.error:
            pass
    try:
        curses.init_pair(1, bright, bg)
        curses.init_pair(2, dim, bg)
        curses.init_pair(3, cursor_fg, bright)
        curses.init_pair(4, curses.COLOR_RED, bg)
    except curses.error:
        pass


def _attr_for(style: str) -> int:
    attr = _ATTR_FOR_STYLE.get(style, 0)
    if curses.has_colors():
        attr |= curses.color_pair(_PAIR_FOR_STYLE.get(style, 2))
    elif style in ("item_cursor",):
        attr |= curses.A_REVERSE
    return attr


def _paint(stdscr, screen, cursor: int, error: Optional[str]) -> None:
    height, width = stdscr.getmaxyx()
    lines = render_screen(
        screen, cursor, max(1, width - 1), max(1, height), error=error
    )
    stdscr.erase()
    for y, (text, style) in enumerate(lines):
        try:
            stdscr.addstr(y, 0, text, _attr_for(style))
        except curses.error:
            pass  # writing to the very last cell can error; ignore
    stdscr.refresh()


def _confirm_quit(stdscr) -> bool:
    height, width = stdscr.getmaxyx()
    try:
        stdscr.addstr(
            max(0, height - 1), 0,
            _QUIT_PROMPT[: max(1, width - 1)], _attr_for("error"),
        )
    except curses.error:
        pass
    stdscr.refresh()
    return stdscr.getch() in (ord("y"), ord("Y"))


def _apply_space(wizard: Wizard, screen, cursor: int) -> Optional[str]:
    """Map a Space press to the ui_model event for the current screen kind."""
    if not screen.items:
        return None
    item = screen.items[cursor]
    try:
        if screen.kind == "multi":
            wizard.apply(Toggle(item.id))
        elif screen.kind == "radio":
            wizard.apply(Choose(item.id))
        elif screen.kind == "toggle":
            wizard.apply(SetFlag(not item.selected))
    except ValidationError as exc:
        return str(exc)
    return None


def _loop(stdscr, wizard: Wizard) -> Optional[WizardResult]:
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    _init_colors()
    stdscr.keypad(True)
    cursor = 0
    error: Optional[str] = None

    while True:
        screen = wizard.current_screen()
        count = len(screen.items)
        cursor = max(0, min(cursor, count - 1)) if count else 0
        _paint(stdscr, screen, cursor, error)

        key = stdscr.getch()
        error = None  # any keypress clears the previous error line

        if key in (curses.KEY_ENTER, 10, 13):
            try:
                wizard.next()
                cursor = 0
            except ValidationError as exc:
                error = str(exc)
            if wizard.is_finished():
                try:
                    return wizard.to_result()
                except ValidationError as exc:
                    error = str(exc)
        elif key == 27:  # Esc quits everywhere ('q' is typeable on forms)
            if _confirm_quit(stdscr):
                return None
        elif screen.kind == "form":
            # Printable characters are text input here — including b/q/space.
            if key in (curses.KEY_BACKSPACE, 127, 8):
                wizard.backspace()
            elif key == curses.KEY_LEFT:
                wizard.back()
                cursor = 0
            elif 32 <= key < 256 and chr(key).isprintable():
                wizard.feed_char(chr(key))
        elif key in (curses.KEY_UP, ord("k")):
            cursor = max(0, cursor - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            cursor = min(max(0, count - 1), cursor + 1)
        elif key == ord(" "):
            error = _apply_space(wizard, screen, cursor)
        elif key in (curses.KEY_LEFT, ord("b"), ord("B")):
            wizard.back()
            cursor = 0
        elif key in (ord("q"), ord("Q")):
            if _confirm_quit(stdscr):
                return None
        # KEY_RESIZE and anything else: just repaint on the next iteration.


def run_tui(catalog, disks=None, ask_identity: bool = False,
            identity_defaults=None) -> Optional[WizardResult]:
    """Run the full wizard in curses; returns a WizardResult, or None on quit."""
    wizard = Wizard(catalog, disks=disks, ask_identity=ask_identity,
                    identity_defaults=identity_defaults)
    return curses.wrapper(_loop, wizard)
