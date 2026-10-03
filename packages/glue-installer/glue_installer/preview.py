"""Session/shell screenshot preview for the TUI (P key).

Pure helpers plus the system-facing hook: tui.py stays curses-only, so the
chafa/subprocess work lives here with injectable `which`, `run`, `input_fn`
and `get_size` for headless tests.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

from glue_installer.ui_view import Screen


def screenshot_under_cursor(screen: Screen, cursor: int) -> Optional[str]:
    """Catalog-relative screenshot path of the item under `cursor`, or None."""
    if screen.kind == "form" or not screen.items:
        return None
    if not isinstance(cursor, int) or isinstance(cursor, bool):
        return None
    cursor = max(0, min(cursor, len(screen.items) - 1))
    return screen.items[cursor].screenshot or None


def make_show_screenshot(
    catalog_dir: Path,
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
    run: Callable = subprocess.run,
    input_fn: Callable[[str], str] = input,
    get_size: Callable = shutil.get_terminal_size,
    out: Callable[[str], None] = print,
) -> Callable[[str], None]:
    """Build the `show_screenshot(rel_path)` hook for run_tui.

    Runs with curses suspended: draws the PNG with chafa when installed,
    otherwise prints where the file is. Never raises on a missing file or a
    failing viewer; always waits for Enter so the message can be read.
    """
    base = Path(catalog_dir).resolve()

    def show(rel_path: str) -> None:
        path = (base / rel_path).resolve()
        if not path.is_file():
            out(f"Screenshot not found: {path}")
        else:
            chafa = which("chafa")
            if chafa is None:
                kib = path.stat().st_size // 1024
                out(f"Image viewer (chafa) not installed — the screenshot is at: "
                    f"{path} ({kib} KiB)")
            else:
                size = get_size((80, 24))
                try:
                    result = run(["chafa", "--clear", "--size",
                                  f"{size.columns}x{max(1, size.lines - 2)}",
                                  str(path)])
                    if getattr(result, "returncode", 0) != 0:
                        out(f"chafa could not draw the screenshot; it is at: {path}")
                except OSError as exc:
                    out(f"chafa failed ({exc}); the screenshot is at: {path}")
        try:
            input_fn("Press Enter to return")
        except EOFError:
            pass

    return show
