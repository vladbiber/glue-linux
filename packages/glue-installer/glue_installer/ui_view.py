"""
View-layer dataclasses, events, and user-facing notice texts for the wizard.

Split out of ui_model.py (ADR-7 keeps files under 500 lines). Pure data —
NO curses, NO I/O, no wizard logic. ui_model re-exports everything here, so
existing imports from ui_model keep working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from glue_installer.catalog import SchedulerOption, Session, Shell
from glue_installer.ui_forms import FormField

# -- notices -----------------------------------------------------------------

DE_SECTION = "Desktop environments (optional)"

SESSIONS_NOTICE = (
    "You can select MULTIPLE window managers and desktops at once — "
    "install several and pick which one to use at the login screen, "
    "every time you log in."
)

WELCOME_NOTICE = (
    "Welcome to Glue Linux! This wizard lets you compose your own "
    "system: kernel, init system, window managers, and extras. "
    "Nothing is touched until you confirm the summary at the end."
)

NETWORK_NOTICE_TEMPLATE = (
    "Internet: {status}. "
    "A working connection is REQUIRED — the installer downloads every "
    "package. You cannot continue until you are online. "
    "Press N to open the network tool (nmtui): connect to Wi-Fi or "
    "check your cable there, then press Enter to re-check and continue."
)

DISKMODE_NOTICE = (
    "How should Glue use your storage? Erasing a whole disk is the "
    "simplest; using an existing partition keeps everything else on the "
    "disk intact; manual mode opens cfdisk so you can make room yourself."
)

DISK_NOTICE = (
    "Choose the disk to install Glue Linux on. "
    "The selected disk will be COMPLETELY ERASED."
)

DISK_MANUAL_NOTICE = (
    "Choose the disk to partition. cfdisk will open on it — create or "
    "adjust partitions, write the table, and quit; you will then pick "
    "the partition to install into. Nothing else is touched."
)

SCHEDULER_NOTICE = (
    "Gaming mode runs a sched_ext CPU scheduler on top of the CachyOS kernel. "
    "scx_lavd is what CachyOS ships for gaming; scx_bpfland favours "
    "interactive tasks; None keeps the kernel's built-in EEVDF scheduler. "
    "The choice can be changed later in /etc/default/scx."
)

PARTITION_NOTICE = (
    "Choose the partition to install Glue Linux into. Only that "
    "partition is formatted — the rest of the disk is left untouched. "
    "On UEFI systems the disk's existing EFI partition is reused as-is."
)


# -- view dataclasses (immutable snapshots handed to the renderer) ----------

@dataclass(frozen=True)
class Item:
    id: str
    label: str
    description: str
    ease: Optional[int] = None
    lightness: Optional[int] = None
    keybinds: Optional[str] = None
    screenshot: Optional[str] = None
    recommended: bool = False
    selected: bool = False
    section: Optional[str] = None


@dataclass(frozen=True)
class Screen:
    key: str
    title: str
    kind: str  # 'info' | 'radio' | 'multi' | 'toggle' | 'form' | 'summary'
    items: List[Item] = field(default_factory=list)
    notice: Optional[str] = None
    field: Optional[FormField] = None  # set only on kind == 'form' screens


@dataclass(frozen=True)
class WizardResult:
    """Final wizard output. device_path/identity are None when their screens
    were skipped (no disk list injected / ask_identity=False)."""
    selection: object  # plan.Selection
    device_path: Optional[str]
    identity: Optional[object]  # identity.IdentitySpec when collected
    disk_mode: str = "erase"    # 'erase' | 'existing' | 'manual'
    partition_path: Optional[str] = None  # set for existing/manual modes


# -- events ------------------------------------------------------------------

@dataclass(frozen=True)
class Toggle:
    item_id: str


@dataclass(frozen=True)
class Choose:
    item_id: str


@dataclass(frozen=True)
class SetFlag:
    value: bool


# -- item builders -----------------------------------------------------------

def _format_keybinds(keybindings) -> Optional[str]:
    if not keybindings:
        return None
    return "\n".join(f"{kb.keys} — {kb.action}" for kb in keybindings)


def session_item(session: Session, selected: bool) -> Item:
    return Item(
        id=session.id, label=session.name, description=session.description,
        ease=session.ease, lightness=session.lightness,
        keybinds=_format_keybinds(session.keybindings),
        screenshot=session.screenshot, selected=selected,
        section=DE_SECTION if session.kind == "de" else None,
    )


def shell_item(shell: Shell, selected: bool) -> Item:
    return Item(
        id=shell.id, label=shell.name, description=shell.description,
        ease=shell.ease, lightness=shell.lightness,
        keybinds=_format_keybinds(shell.keybindings),
        screenshot=shell.screenshot, selected=selected,
    )


def partition_item(part, selected: bool) -> Item:
    """Item for an existing disks.Partition (radio list on the partition screen)."""
    gib = part.size_bytes / (1024 ** 3)
    fs = part.fstype or "unformatted"
    return Item(
        id=part.path, label=f"{part.path} — {gib:.1f} GiB ({fs})",
        description=f"On {part.parent_path}. Will be formatted as ext4.",
        selected=selected,
    )


def scheduler_item(option: SchedulerOption, selected: bool) -> Item:
    label = option.name + (" (recommended)" if option.default else "")
    return Item(id=option.id, label=label, description=option.description,
                recommended=option.default, selected=selected)


def mode_items(mode: str, minimal) -> List[Item]:
    return [
        Item(id="custom", label="Custom install",
             description="Compose your system: kernel, init, window managers, "
                         "support extras and gaming mode.",
             recommended=True, selected=mode == "custom"),
        Item(id="minimal", label=minimal.name, description=minimal.description,
             selected=mode == "minimal"),
    ]


def diskmode_items(disk_mode: str) -> List[Item]:
    return [
        Item(id="erase", label="Erase a whole disk (guided)",
             description="Simplest: wipes the chosen disk and lets the "
                         "installer lay out partitions automatically.",
             recommended=True, selected=disk_mode == "erase"),
        Item(id="existing", label="Use an existing partition",
             description="Formats ONLY the partition you pick; everything "
                         "else on the disk stays untouched.",
             selected=disk_mode == "existing"),
        Item(id="manual", label="Partition manually (cfdisk)",
             description="Opens cfdisk on a disk of your choice to make "
                         "room, then install into a partition you pick.",
             selected=disk_mode == "manual"),
    ]
