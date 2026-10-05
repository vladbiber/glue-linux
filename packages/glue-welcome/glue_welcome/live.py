"""Live ISO helpers for Glue Welcome: pure logic, no GTK, effects injectable."""

from __future__ import annotations

import glob
import os
import shutil
from dataclasses import dataclass

INSTALL_LAUNCHER = "glue-install-gui"
DEFAULT_MARKER = "/run/artix/bootmnt"
DEFAULT_KMS_GLOB = "/dev/dri/card*"

NOT_LIVE_TEXT = "The installer is only available from the Glue Linux live USB."
NO_KMS_TEXT = ("The graphical installer needs a working graphics driver. "
               "Use the text installer: open a terminal and run glue-install.")
NO_LAUNCHER_TEXT = "The graphical installer is not installed on this system."
NO_DISPLAY_TEXT = ("The installer could not reach the screen. Sign out, sign in again "
                   "and try once more, or run glue-install in a terminal.")
NO_CONFIG_TEXT = ("The installer configuration is missing from this live USB. "
                  "Run glue-install in a terminal instead.")
FAILED_TEXT = "The installer could not start. Run glue-install in a terminal instead."

EXIT_MESSAGES = {2: NOT_LIVE_TEXT, 3: NO_KMS_TEXT, 4: NO_DISPLAY_TEXT, 5: NO_CONFIG_TEXT}


@dataclass(frozen=True)
class LiveAction:
    id: str
    title: str
    subtitle: str
    argv: tuple[str, ...] | None


def is_live(env=os.environ, exists=os.path.exists) -> bool:
    """True when the live ISO boot mount exists (same marker as glue-install-gui)."""
    return bool(exists(env.get("GLUE_LIVE_MARKER") or DEFAULT_MARKER))


def has_kms(env=os.environ, glob_fn=glob.glob) -> bool:
    """True when at least one KMS device node matches the glob."""
    return bool(glob_fn(env.get("GLUE_KMS_GLOB") or DEFAULT_KMS_GLOB))


def install_command(which=shutil.which, env=os.environ, exists=os.path.exists,
                    glob_fn=glob.glob) -> list[str] | None:
    """Command to start the graphical installer, or None when it cannot run."""
    if not is_live(env, exists) or not has_kms(env, glob_fn):
        return None
    return [INSTALL_LAUNCHER] if which(INSTALL_LAUNCHER) else None


def explain_unavailable(which=shutil.which, env=os.environ, exists=os.path.exists,
                        glob_fn=glob.glob) -> str:
    """Plain-language reason install_command() is None; empty when it is available."""
    if not is_live(env, exists):
        return NOT_LIVE_TEXT
    if not has_kms(env, glob_fn):
        return NO_KMS_TEXT
    if not which(INSTALL_LAUNCHER):
        return NO_LAUNCHER_TEXT
    return ""


def exit_code_message(code: int) -> str:
    """Message for a failed glue-install-gui exit code."""
    return EXIT_MESSAGES.get(code, FAILED_TEXT)


_ACTIONS = (
    ("terminal", "Terminal", "Type commands.", "alacritty"),
    ("files", "Files", "Browse your files and drives.", "nautilus"),
    ("browser", "Web browser", "Go online.", "firefox"),
    ("network", "Network", "Connect to Wi-Fi.", "glue-network"),
)


def quick_actions(which=shutil.which) -> list[LiveAction]:
    """Quick actions for the live desktop; entries with a missing binary are left out."""
    actions = [LiveAction(aid, title, sub, (binary,))
               for aid, title, sub, binary in _ACTIONS if which(binary)]
    actions.append(LiveAction("system", "Hardware info", "See what this computer has.", None))
    return actions
