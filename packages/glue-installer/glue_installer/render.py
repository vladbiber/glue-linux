"""
Pure line renderer for the Glue Linux installer TUI (ADR-5).

Turns a ui_model.Screen snapshot + cursor position + terminal size into a
flat list of (text, style_tag) lines. The thin curses driver (tui.py) only
paints these lines; this module MUST NOT import curses, perform I/O, or
depend on terminal state. Same inputs always produce the same output.

Style tags:
  'title' | 'notice' | 'item' | 'item_selected' | 'item_cursor' |
  'detail' | 'recommended' | 'section' | 'error' | 'footer'
"""

from __future__ import annotations

import textwrap
from typing import List, Optional, Tuple

from glue_installer.ui_forms import masked
from glue_installer.ui_model import Item, Screen

StyledLine = Tuple[str, str]

STYLES = frozenset({
    "title", "notice", "item", "item_selected", "item_cursor",
    "detail", "recommended", "section", "error", "footer",
})

FOOTER_TEXT = "↑/↓ move   Space toggle/choose   Enter next   B back   Q quit"

FORM_FOOTER_TEXT = "Type to edit   Backspace erase   Enter continue   ← back   Esc quit"

_RECOMMENDED_SUFFIX = " (recommended)"


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _fit(text: str, width: int) -> str:
    """Truncate text to width, ending with '…' when anything was cut."""
    if len(text) <= width:
        return text
    if width <= 1:
        return "…"
    return text[: width - 1] + "…"


def _wrap(text: str, width: int) -> List[str]:
    """Word-wrap text to width; long unbroken words are split to fit."""
    if not text:
        return []
    return textwrap.wrap(text, width=max(1, width))


def _glyph(kind: str, selected: bool) -> str:
    if kind == "multi":
        return "[x] " if selected else "[ ] "
    if kind in ("radio", "toggle"):
        return "(o) " if selected else "( ) "
    return ""


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def _item_row(screen: Screen, item: Item, at_cursor: bool, width: int) -> StyledLine:
    marker = "> " if at_cursor else "  "
    label = item.label
    if item.recommended and _RECOMMENDED_SUFFIX not in label:
        label += _RECOMMENDED_SUFFIX
    text = _fit(marker + _glyph(screen.kind, item.selected) + label, width)
    if at_cursor:
        style = "item_cursor"
    elif item.recommended:
        style = "recommended"
    elif item.selected:
        style = "item_selected"
    else:
        style = "item"
    return (text, style)


def _item_rows(screen: Screen, cursor: int, width: int) -> Tuple[List[StyledLine], int]:
    """Build interleaved section-header + item rows.

    Returns (rows, cursor_row_index) where cursor_row_index points at the
    row for the item under the cursor within `rows`.
    """
    rows: List[StyledLine] = []
    cursor_row = 0
    prev_section: Optional[str] = None
    for idx, item in enumerate(screen.items):
        if item.section != prev_section and item.section is not None:
            rows.append((_fit(f"— {item.section} —", width), "section"))
        prev_section = item.section
        if idx == cursor:
            cursor_row = len(rows)
        rows.append(_item_row(screen, item, idx == cursor, width))
    return rows, cursor_row


def _form_rows(form, width: int) -> List[StyledLine]:
    """Input row for a form screen. Secret values are ALWAYS masked with '*'
    per character — the plaintext never appears in any rendered line."""
    return [(_fit(f"> {form.label}: {masked(form)}_", width), "item_cursor")]


def _detail_lines(item: Item, width: int) -> List[StyledLine]:
    """Detail block for the item under the cursor; only non-None fields."""
    lines: List[StyledLine] = []
    for ln in _wrap(item.description, width):
        lines.append((ln, "detail"))
    if item.ease is not None:
        lines.append((_fit(f"Ease: {item.ease}/5", width), "detail"))
    if item.lightness is not None:
        lines.append((_fit(f"Lightness: {item.lightness}/5", width), "detail"))
    if item.keybinds:
        keys = "; ".join(item.keybinds.splitlines())
        lines.append((_fit(f"Keys: {keys}", width), "detail"))
    if item.screenshot:
        lines.append((_fit(f"Preview: {item.screenshot}", width), "detail"))
    if lines:
        lines.insert(0, ("", "detail"))
    return lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_screen(
    screen: Screen,
    cursor: int,
    width: int,
    height: int,
    error: Optional[str] = None,
) -> List[StyledLine]:
    """Render a Screen into at most `height` styled lines of at most `width` chars.

    The item list scrolls so the cursor row stays visible; when height is
    tight the detail block, then the notice, then the footer are dropped
    (title, error, and the cursor row survive longest). Pure function.
    """
    if not isinstance(width, int) or isinstance(width, bool) or width < 1:
        raise ValueError(f"width must be a positive integer, got {width!r}")
    if not isinstance(height, int) or isinstance(height, bool) or height < 1:
        raise ValueError(f"height must be a positive integer, got {height!r}")
    if not isinstance(screen, Screen):
        raise ValueError(f"screen must be a ui_model.Screen, got {type(screen).__name__}")
    if not isinstance(cursor, int) or isinstance(cursor, bool):
        raise ValueError(f"cursor must be an integer, got {cursor!r}")

    cursor = max(0, min(cursor, len(screen.items) - 1)) if screen.items else 0

    is_form = screen.kind == "form" and screen.field is not None
    if is_form and error is None:
        error = screen.field.error

    title_lines: List[StyledLine] = [(_fit(screen.title, width), "title")]
    notice_lines: List[StyledLine] = (
        [(ln, "notice") for ln in _wrap(screen.notice, width)] if screen.notice else []
    )
    error_lines: List[StyledLine] = (
        [(ln, "error") for ln in _wrap(str(error), width)] if error else []
    )
    detail = _detail_lines(screen.items[cursor], width) if screen.items else []
    foot_text = FORM_FOOTER_TEXT if is_form else FOOTER_TEXT
    foot: List[StyledLine] = [(_fit(foot_text, width), "footer")]

    if is_form:
        rows, cursor_row = _form_rows(screen.field, width), 0
    else:
        rows, cursor_row = _item_rows(screen, cursor, width)

    # Fit everything into `height`: shed lowest-priority blocks first.
    fixed = (len(title_lines) + len(notice_lines) + len(error_lines)
             + len(detail) + len(foot))
    avail = height - fixed
    min_rows = 1 if rows else 0
    while avail < min_rows and detail:
        detail.pop()
        avail += 1
    while avail < min_rows and notice_lines:
        notice_lines.pop()
        avail += 1
    while avail < min_rows and foot:
        foot.pop()
        avail += 1
    while avail < min_rows and len(error_lines) > 1:
        error_lines.pop()
        avail += 1
    avail = max(avail, min_rows)

    # Scroll window over the rows so the cursor row is always visible.
    if rows:
        start = 0 if cursor_row < avail else cursor_row - avail + 1
        window = rows[start:start + avail]
    else:
        window = []

    lines = title_lines + notice_lines + error_lines + window + detail + foot
    return lines[:height]
