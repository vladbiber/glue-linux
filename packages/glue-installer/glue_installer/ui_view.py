"""
View-layer dataclasses, events, and user-facing notice texts for the wizard.

Split out of ui_model.py. Pure data -
NO curses, NO I/O, no wizard logic. ui_model re-exports everything here, so
existing imports from ui_model keep working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from glue_installer.catalog import SchedulerOption, Session, Shell
from glue_installer.ui_forms import FormField, disk_label, masked

# -- notices -----------------------------------------------------------------

DE_SECTION = "Desktop environments (optional)"

SESSIONS_NOTICE = (
    "You can tick several at once. Everything you tick gets installed, "
    "and at the login screen (greeter) you choose which one to start "
    "every time you log in — for example gluewc for daily use and KDE "
    "as a backup."
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
    "check your cable there, then press Enter to re-check and continue. "
    "No internet?  O = install OFFLINE: clone this live system (gluewc + "
    "stock kernel, runit; gaming and other desktops need the online install)."
)

OFFLINE_SUMMARY = "Offline install (clone of the live system: stock linux kernel, runit)"

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

SWAP_NOTICE = (
    "Swap gives the system room when memory runs out. Automatic is right "
    "for almost everyone: fast compressed memory (zram) plus a disk swap "
    "sized from your RAM."
)

HIBERNATE_NOTICE = (
    "Hibernation saves your session to disk and powers off. It needs a "
    "disk swap at least as large as your RAM; the installer sizes it "
    "automatically. Leave it off if you only use sleep."
)

SWAP_SUMMARY = {"auto": "Automatic (zram + disk)", "zram": "zram only",
                "none": "none"}

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
    screenshots: Tuple[str, ...] = ()
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
        screenshot=session.screenshot,
        screenshots=tuple(session.screenshots), selected=selected,
        section=DE_SECTION if session.kind == "de" else None,
    )


def shell_item(shell: Shell, selected: bool, recommended: bool = False) -> Item:
    return Item(
        id=shell.id,
        label=shell.name + (" (recommended)" if recommended else ""),
        description=shell.description,
        ease=shell.ease, lightness=shell.lightness,
        keybinds=_format_keybinds(shell.keybindings),
        screenshot=shell.screenshot,
        screenshots=tuple(shell.screenshots), recommended=recommended, selected=selected,
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


def swap_items(swap_mode: str) -> List[Item]:
    return [
        Item(id="auto", label="Automatic (recommended)",
             description="zram compressed memory plus a disk swap sized "
                         "from your RAM",
             recommended=True, selected=swap_mode == "auto"),
        Item(id="zram", label="zram only",
             description="compressed memory swap, nothing on disk; "
                         "no hibernation",
             selected=swap_mode == "zram"),
        Item(id="none", label="No swap", description="only if you know why",
             selected=swap_mode == "none"),
    ]


def hibernate_item(selected: bool) -> Item:
    return Item(id="hibernate", label="Hibernation (suspend to disk)",
                description="Needs a disk swap of at least RAM + 1 GiB; the "
                            "installer sizes it for you.",
                selected=selected)


def kernel_items(kernels, kernel_id, cpu_v3: bool) -> List[Item]:
    # Primary kernel listed first and marked recommended
    ordered = sorted(kernels, key=lambda k: not k.primary)
    items = []
    for k in ordered:
        if k.id == "linux-cachyos" and cpu_v3:
            suffix = " (x86-64-v3)"
        else:
            suffix = " (recommended)" if k.primary else ""
        items.append(Item(id=k.id, label=k.name + suffix,
                          description=k.description, recommended=k.primary,
                          selected=kernel_id == k.id))
    return items


def init_items(inits, init_id) -> List[Item]:
    return [Item(id=i.id,
                 label=i.name + (" (recommended)" if i.recommended else ""),
                 description=i.description, recommended=i.recommended,
                 selected=init_id == i.id)
            for i in inits]


def support_items(support, selected_ids) -> List[Item]:
    return [Item(id=t.id, label=t.name, description=t.description,
                 selected=t.id in selected_ids)
            for t in support]


def disk_items(disks, device_path) -> List[Item]:
    return [Item(id=d.path, label=disk_label(d),
                 description="Removable device" if d.is_removable else "",
                 selected=device_path == d.path)
            for d in disks]


def sessions_items(sessions, selected_ids) -> List[Item]:
    wms = [s for s in sessions if s.kind == "wm"]
    des = [s for s in sessions if s.kind == "de"]
    return [session_item(s, s.id in selected_ids) for s in wms + des]


def shell_items(session, shells_by_id, chosen) -> List[Item]:
    return [shell_item(shells_by_id[sid], chosen == sid, recommended=idx == 0)
            for idx, sid in enumerate(session.shell_choices)]


def scheduler_items(options, chosen) -> List[Item]:
    return [scheduler_item(o, chosen == o.id) for o in options]


# -- summary screen ----------------------------------------------------------

def summary_items(w) -> List[Item]:
    """Items of the wizard's summary screen, built from a Wizard's state
    (kept here so ui_model.py stays small; it reads the wizard's private
    fields on purpose - it is the view of exactly that state)."""
    kernel_map = {k.id: k for k in w._catalog.kernels}
    init_map = {i.id: i for i in w._catalog.inits}
    items: List[Item] = []

    def _add(sid: str, label: str) -> None:
        items.append(Item(id=sid, label=label, description="", selected=True))

    if w._mode == "offline":
        _add("summary:mode", "Mode: " + OFFLINE_SUMMARY)
    else:
        _add("summary:mode", "Mode: " + (
            "Minimal install" if w._mode == "minimal" else "Custom install"))
        kernel, init = kernel_map.get(w._kernel_id), init_map.get(w._init_id)
        _add("summary:kernel", f"Kernel: {kernel.name if kernel else '(none)'}")
        _add("summary:init", f"Init: {init.name if init else '(none)'}")
    for sid in sorted(w._session_ids):
        session = w._sessions_by_id[sid]
        label = f"Session: {session.name}"
        shell_id = w._shell_choice.get(sid)
        if shell_id:
            label += f" (shell: {w._shells_by_id[shell_id].name})"
        _add(f"summary:session:{sid}", label)
    for tid in sorted(w._support_ids):
        toggle = next(t for t in w._catalog.support if t.id == tid)
        _add(f"summary:support:{tid}", f"Support: {toggle.name}")
    if w._mode != "offline":
        _add("summary:gaming", f"Gaming Mode: {'on' if w._gaming else 'off'}")
    if w._gaming and w._catalog.gaming.schedulers:
        sched = next(o for o in w._catalog.gaming.schedulers
                     if o.id == w._scheduler)
        _add("summary:scheduler", f"CPU scheduler: {sched.name}")
    if w._disks is not None:
        if w._disk_mode == "erase":
            _add("summary:disk",
                 f"Disk: {w._device_path or '(none)'} — WILL BE ERASED")
        else:
            _add("summary:disk",
                 f"Partition: {w._partition_path or '(none)'} — will be "
                 "formatted (rest of the disk untouched)")
        _add("summary:swap", f"Swap: {SWAP_SUMMARY[w._swap_mode]}")
        if w._hibernate:
            _add("summary:hibernate", "Hibernation: on")
    if w._forms is not None:
        for fkey, form in w._forms.items():
            if fkey == "password_confirm":
                continue  # masked password shown once is enough
            _add(f"summary:{fkey}", f"{form.label}: {masked(form)}")
    return items
