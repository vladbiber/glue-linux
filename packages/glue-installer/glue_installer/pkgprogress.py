"""Progress of one basestrap/pacman run, read from its output.

Pure: feed() takes one output line, returns (fraction 0..1, status text) when
the line moves the bar, else None. The format is pacman's output without a
terminal (no progress bars), checked against a real `pacman -r ROOT -Sy`
on the build image:

    Package (10)              New Version   Net Change  Download Size
    system/glibc              2.44-1         50.82 MiB
    system/bash               5.3.20-1        9.60 MiB       1.91 MiB
    :: Retrieving packages...
     bash-5.3.20-1-x86_64 downloading...
    checking keyring...
    :: Processing package changes...
    installing bash...
    :: Running post-transaction hooks...
    (1/3) Generating the configured locale...

Rows without a Download Size are already in the cache. Unknown lines are
ignored, so a format change only makes the bar coarser, never wrong.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# share of the run for each phase (downloads dominate on a fresh install)
_DOWNLOAD = (0.0, 0.45)
_CHECKS = (0.45, 0.50)
_INSTALL = (0.50, 0.95)
_HOOKS = (0.95, 1.0)

_TABLE_HEAD = re.compile(r"^Packages? \((\d+)\)\s")
_SIZE = re.compile(r"\d+(?:\.\d+)?\s+(?:B|KiB|MiB|GiB)\b")
_DOWNLOADING = re.compile(r"^\s*(\S+?) downloading\.\.\.\s*$")
_INSTALLING = re.compile(r"^(?:installing|upgrading|reinstalling|downgrading) (\S+?)\.\.\.\s*$")
_HOOK = re.compile(r"^\((\d+)/(\d+)\) (.+?)\.*\s*$")
_CHECK_WORDS = ("checking keyring", "checking package integrity",
                "loading package files", "checking for file conflicts",
                "checking available disk space")
_DB_SUFFIXES = ("system", "world", "galaxy", "lib32", "cachyos", "cachyos-v3",
                "cachyos-core-v3", "cachyos-extra-v3", "glue", "extra", "core",
                "multilib")


def _span(phase: Tuple[float, float], done: int, total: int) -> float:
    lo, hi = phase
    if total <= 0:
        return lo
    return lo + (hi - lo) * min(done, total) / total


class PacmanProgress:
    def __init__(self) -> None:
        self.total = 0          # packages in the transaction
        self.to_download = 0    # rows with a Download Size
        self.downloaded = 0
        self.installed = 0
        self._in_table = False
        self._rows = 0
        self._last = 0.0

    def _emit(self, value: float, text: str) -> Tuple[float, str]:
        # never move the bar backwards
        self._last = max(self._last, min(1.0, value))
        return self._last, text

    def feed(self, line: str) -> Optional[Tuple[float, str]]:
        line = line.rstrip("\n")
        stripped = line.strip()
        head = _TABLE_HEAD.match(stripped)
        if head:
            self.total = int(head.group(1))
            self._in_table = True
            self._rows = 0
            return None
        if self._in_table:
            if not stripped:
                if self._rows:              # blank line after the rows
                    self._in_table = False
                return None
            if stripped.startswith("Total "):
                self._in_table = False
                return None
            self._rows += 1
            if len(_SIZE.findall(stripped)) >= 2:
                self.to_download += 1
            return None

        m = _DOWNLOADING.match(line)
        if m:
            name = m.group(1)
            if name in _DB_SUFFIXES:          # database refresh, not a package
                return self._emit(self._last, "Refreshing package databases")
            self.downloaded += 1
            total = self.to_download or self.total
            return self._emit(_span(_DOWNLOAD, self.downloaded, total),
                              f"Downloading {_pkgname(name)} "
                              f"({self.downloaded}/{total or '?'})")
        low = stripped.lower()
        for word in _CHECK_WORDS:
            if low.startswith(word):
                idx = _CHECK_WORDS.index(word) + 1
                return self._emit(_span(_CHECKS, idx, len(_CHECK_WORDS)),
                                  word[0].upper() + word[1:])
        m = _INSTALLING.match(stripped)
        if m:
            self.installed += 1
            total = self.total or self.installed
            return self._emit(_span(_INSTALL, self.installed, total),
                              f"Installing {m.group(1)} ({self.installed}/{total})")
        if self.installed:
            m = _HOOK.match(stripped)
            if m:
                i, n = int(m.group(1)), int(m.group(2))
                return self._emit(_span(_HOOKS, i, n), m.group(3).strip())
        return None


def _pkgname(filename: str) -> str:
    """bash-5.3.20-1-x86_64 -> bash (drops version, release and arch)."""
    parts = filename.split("-")
    if len(parts) >= 4:
        return "-".join(parts[:-3])
    return filename
