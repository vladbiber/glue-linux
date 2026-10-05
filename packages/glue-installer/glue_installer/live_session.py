"""Live ISO first-console choice: desktop or text installer.

Pure module. The shell script `glue-live-session` (root-overlay of the ISO)
applies exactly the same rules; tests/test_live_session.py runs both on the
same inputs and compares them.

Rules, first match wins:
  1. `glue.tui` word on the kernel command line  -> tui  (the user asked for it)
  2. no /dev/dri/card* (no KMS)                  -> tui  (gluewc needs DRM)
  3. GLUE_NOAUTO set and non-empty               -> tui  (already launched once)
  4. otherwise                                   -> desktop
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping

DESKTOP = "desktop"
TUI = "tui"

CMDLINE_FLAG = "glue.tui"
NOAUTO_VAR = "GLUE_NOAUTO"

REASON_CMDLINE = "glue.tui on the kernel command line"
REASON_NO_KMS = "no KMS device (/dev/dri/card*)"
REASON_NOAUTO = "GLUE_NOAUTO is set"
REASON_OK = "KMS available"


@dataclass(frozen=True)
class LiveChoice:
    kind: str  # DESKTOP | TUI
    reason: str
    shell: bool  # start the glueqs bar (false: glueqs is not installed)


def cmdline_has_flag(cmdline: str, flag: str = CMDLINE_FLAG) -> bool:
    """True when `flag` is a whole word of the command line (not a substring)."""
    return flag in cmdline.split()


def decide(
    cmdline: str,
    drm_cards: List[str],
    env: Mapping[str, str],
    shell_installed: bool,
) -> LiveChoice:
    """Pick what tty1 starts on the live ISO."""
    if cmdline_has_flag(cmdline):
        return LiveChoice(TUI, REASON_CMDLINE, False)
    if not any(c for c in drm_cards):
        return LiveChoice(TUI, REASON_NO_KMS, False)
    if env.get(NOAUTO_VAR):
        return LiveChoice(TUI, REASON_NOAUTO, False)
    return LiveChoice(DESKTOP, REASON_OK, bool(shell_installed))
