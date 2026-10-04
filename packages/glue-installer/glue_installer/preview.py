"""Session/shell screenshot preview for the TUI (P key).

Pure helpers plus the system-facing hook: tui.py stays curses-only, so the
chafa/subprocess work lives here with injectable `which`, `run`, `input_fn`
and `get_size` for headless tests.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple, Union

from glue_installer.ui_view import Screen


def screenshot_under_cursor(screen: Screen, cursor: int) -> Optional[str]:
    """Catalog-relative screenshot path of the item under `cursor`, or None."""
    if screen.kind == "form" or not screen.items:
        return None
    if not isinstance(cursor, int) or isinstance(cursor, bool):
        return None
    cursor = max(0, min(cursor, len(screen.items) - 1))
    return screen.items[cursor].screenshot or None


def screenshots_under_cursor(screen: Screen, cursor: int) -> Tuple[str, ...]:
    """All catalog-relative screenshot paths of the item under `cursor`."""
    first = screenshot_under_cursor(screen, cursor)
    if first is None:
        return ()
    item = screen.items[max(0, min(cursor, len(screen.items) - 1))]
    return tuple(item.screenshots) or (first,)


def make_show_screenshot(
    catalog_dir: Path,
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
    run: Callable = subprocess.run,
    input_fn: Callable[[str], str] = input,
    get_size: Callable = shutil.get_terminal_size,
    out: Callable[[str], None] = print,
) -> Callable[[Union[str, Sequence[str]]], None]:
    """Build the `show_screenshot(rel_path | rel_paths)` hook for run_tui.

    Runs with curses suspended: draws each PNG with chafa when installed,
    otherwise prints where the file is. Several images form a gallery
    (n next, p previous, q or Enter on the last one back). Never raises on a
    missing file or a failing viewer; always waits for input so the message
    can be read.
    """
    base = Path(catalog_dir).resolve()

    def draw(rel_path: str) -> None:
        path = (base / rel_path).resolve()
        if not path.is_file():
            out(f"Screenshot not found: {path}")
            return
        chafa = which("chafa")
        if chafa is None:
            kib = path.stat().st_size // 1024
            out(f"Image viewer (chafa) not installed — the screenshot is at: "
                f"{path} ({kib} KiB)")
            return
        size = get_size((80, 24))
        try:
            result = run(["chafa", "--clear", "--size",
                          f"{size.columns}x{max(1, size.lines - 2)}", str(path)])
            if getattr(result, "returncode", 0) != 0:
                out(f"chafa could not draw the screenshot; it is at: {path}")
        except OSError as exc:
            out(f"chafa failed ({exc}); the screenshot is at: {path}")

    def show(rel_paths: Union[str, Sequence[str]]) -> None:
        paths = [rel_paths] if isinstance(rel_paths, str) else list(rel_paths)
        if not paths:
            return
        last = len(paths) - 1
        i = 0
        while True:
            draw(paths[i])
            if last == 0:
                out("Image 1/1")
                prompt = "Press Enter to return"
            else:
                prompt = (f"Image {i + 1}/{len(paths)}  "
                          "[n] next  [p] previous  [q] back")
            try:
                answer = input_fn(prompt).strip().lower()
            except EOFError:
                return
            if last == 0 or answer == "q" or (answer == "" and i == last):
                return
            if answer in ("", "n") and i < last:
                i += 1
            elif answer == "p" and i > 0:
                i -= 1

    return show
