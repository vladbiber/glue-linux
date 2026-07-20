"""
Pure TUI wizard state machine for the Wheatley Linux installer.

This module contains NO curses imports and performs NO I/O: it receives an
already-loaded Catalog and exposes a deterministic screen-by-screen wizard
that produces a plan.Selection. A thin curses renderer (built later) only
draws the Screen snapshots returned here and forwards key events.

Screen sequence:
  welcome -> mode -> kernel -> init -> sessions -> shell:<sid>... ->
  support -> gaming -> summary

In minimal mode the 'sessions', 'shell:*' and 'gaming' screens are skipped
entirely ('gaming' because resolve_plan rejects gaming on a minimal install;
support toggles like bluetooth remain available on a bare system).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from wheatley_installer.catalog import Catalog, Session, Shell
from wheatley_installer.plan import Selection


class ValidationError(Exception):
    """Raised when a wizard action is invalid for the current state."""


# ---------------------------------------------------------------------------
# View dataclasses (immutable snapshots handed to the renderer)
# ---------------------------------------------------------------------------

DE_SECTION = "Desktop environments (optional)"

SESSIONS_NOTICE = (
    "You can select MULTIPLE window managers and desktops at once — "
    "install several and pick which one to use at the login screen, "
    "every time you log in."
)

WELCOME_NOTICE = (
    "Welcome to Wheatley Linux! This wizard lets you compose your own "
    "system: kernel, init system, window managers, and extras. "
    "Nothing is touched until you confirm the summary at the end."
)


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
    kind: str  # 'info' | 'radio' | 'multi' | 'toggle' | 'summary'
    items: List[Item] = field(default_factory=list)
    notice: Optional[str] = None


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Toggle:
    item_id: str


@dataclass(frozen=True)
class Choose:
    item_id: str


@dataclass(frozen=True)
class SetFlag:
    value: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_keybinds(keybindings) -> Optional[str]:
    if not keybindings:
        return None
    return "\n".join(f"{kb.keys} — {kb.action}" for kb in keybindings)


def _session_item(session: Session, selected: bool) -> Item:
    return Item(
        id=session.id,
        label=session.name,
        description=session.description,
        ease=session.ease,
        lightness=session.lightness,
        keybinds=_format_keybinds(session.keybindings),
        screenshot=session.screenshot,
        selected=selected,
        section=DE_SECTION if session.kind == "de" else None,
    )


def _shell_item(shell: Shell, selected: bool) -> Item:
    return Item(
        id=shell.id,
        label=shell.name,
        description=shell.description,
        ease=shell.ease,
        lightness=shell.lightness,
        keybinds=_format_keybinds(shell.keybindings),
        screenshot=shell.screenshot,
        selected=selected,
    )


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

class Wizard:
    """Deterministic wizard state machine over a loaded Catalog."""

    def __init__(self, catalog: Catalog):
        if not isinstance(catalog, Catalog):
            raise ValidationError(
                f"Wizard requires a Catalog, got {type(catalog).__name__}"
            )
        self._catalog = catalog
        self._sessions_by_id = {s.id: s for s in catalog.sessions}
        self._shells_by_id = {s.id: s for s in catalog.shells}

        # User state
        self._mode: str = "custom"  # 'custom' | 'minimal'; custom preselected
        self._kernel_id: Optional[str] = None
        self._init_id: Optional[str] = None
        self._session_ids: set = set()
        self._shell_choice: dict = {}  # session_id -> shell_id
        self._support_ids: set = {t.id for t in catalog.support if t.default}
        self._gaming: bool = False

        # Navigation state
        self._current_key: str = "welcome"
        self._finished: bool = False

    # -- navigation --------------------------------------------------------

    def _screen_keys(self) -> List[str]:
        keys = ["welcome", "mode", "kernel", "init"]
        if self._mode == "custom":
            keys.append("sessions")
            keys.extend(
                f"shell:{sid}"
                for sid in sorted(self._session_ids)
                if self._sessions_by_id[sid].shell_choices
            )
        keys.append("support")
        if self._mode == "custom":
            keys.append("gaming")
        keys.append("summary")
        return keys

    def _validate_current(self) -> None:
        key = self._current_key
        if key == "kernel" and self._kernel_id is None:
            raise ValidationError("Select a kernel to continue.")
        if key == "init" and self._init_id is None:
            raise ValidationError("Select an init system to continue.")
        if key == "sessions" and not self._session_ids:
            raise ValidationError(
                "Select at least one window manager or desktop to continue — "
                "or go back and pick the minimal install instead."
            )
        if key.startswith("shell:"):
            sid = key.split(":", 1)[1]
            if sid not in self._shell_choice:
                name = self._sessions_by_id[sid].name
                raise ValidationError(
                    f"Choose a shell/bar for {name} to continue."
                )

    def next(self) -> None:
        """Advance to the next screen; raises ValidationError if invalid."""
        self._validate_current()
        keys = self._screen_keys()
        idx = keys.index(self._current_key)
        if idx == len(keys) - 1:  # summary is always last
            self._finished = True
            return
        self._current_key = keys[idx + 1]

    def back(self) -> None:
        """Return to the previous screen; no-op on the first screen."""
        self._finished = False
        keys = self._screen_keys()
        idx = keys.index(self._current_key)
        if idx > 0:
            self._current_key = keys[idx - 1]

    def is_finished(self) -> bool:
        return self._finished

    # -- events ------------------------------------------------------------

    def apply(self, event) -> None:
        """Apply a Toggle/Choose/SetFlag event to the current screen."""
        screen = self.current_screen()
        if isinstance(event, Choose):
            if screen.kind != "radio":
                raise ValidationError(
                    f"Choose is only valid on radio screens, not '{screen.key}'"
                )
            self._apply_choose(screen, event.item_id)
        elif isinstance(event, Toggle):
            if screen.kind != "multi":
                raise ValidationError(
                    f"Toggle is only valid on multi screens, not '{screen.key}'"
                )
            self._apply_toggle(screen, event.item_id)
        elif isinstance(event, SetFlag):
            if screen.kind != "toggle":
                raise ValidationError(
                    f"SetFlag is only valid on toggle screens, not '{screen.key}'"
                )
            if not isinstance(event.value, bool):
                raise ValidationError("SetFlag value must be a boolean")
            self._gaming = event.value
        else:
            raise ValidationError(f"Unknown event type: {type(event).__name__}")

    def _check_item(self, screen: Screen, item_id: str) -> None:
        if item_id not in {i.id for i in screen.items}:
            raise ValidationError(
                f"Unknown item '{item_id}' on screen '{screen.key}'"
            )

    def _apply_choose(self, screen: Screen, item_id: str) -> None:
        self._check_item(screen, item_id)
        key = screen.key
        if key == "mode":
            self._mode = item_id
            if item_id == "minimal":
                # resolve_plan rejects sessions/shells/gaming on minimal
                self._session_ids.clear()
                self._shell_choice.clear()
                self._gaming = False
        elif key == "kernel":
            self._kernel_id = item_id
        elif key == "init":
            self._init_id = item_id
        elif key.startswith("shell:"):
            self._shell_choice[key.split(":", 1)[1]] = item_id

    def _apply_toggle(self, screen: Screen, item_id: str) -> None:
        self._check_item(screen, item_id)
        if screen.key == "sessions":
            if item_id in self._session_ids:
                self._session_ids.discard(item_id)
                self._shell_choice.pop(item_id, None)  # drop stale choice
            else:
                self._session_ids.add(item_id)
        elif screen.key == "support":
            self._support_ids.symmetric_difference_update({item_id})

    # -- screen construction -------------------------------------------------

    def current_screen(self) -> Screen:
        key = self._current_key
        if key == "welcome":
            return Screen(
                key="welcome", title="Welcome to Wheatley Linux",
                kind="info", items=[], notice=WELCOME_NOTICE,
            )
        if key == "mode":
            return self._mode_screen()
        if key == "kernel":
            return self._kernel_screen()
        if key == "init":
            return self._init_screen()
        if key == "sessions":
            return self._sessions_screen()
        if key.startswith("shell:"):
            return self._shell_screen(key.split(":", 1)[1])
        if key == "support":
            return self._support_screen()
        if key == "gaming":
            return self._gaming_screen()
        return self._summary_screen()

    def _mode_screen(self) -> Screen:
        minimal = self._catalog.minimal
        items = [
            Item(
                id="custom", label="Custom install",
                description=(
                    "Compose your system: kernel, init, window managers, "
                    "support extras and gaming mode."
                ),
                recommended=True, selected=self._mode == "custom",
            ),
            Item(
                id="minimal", label=minimal.name,
                description=minimal.description,
                selected=self._mode == "minimal",
            ),
        ]
        return Screen(key="mode", title="Installation mode",
                      kind="radio", items=items)

    def _kernel_screen(self) -> Screen:
        # Primary kernel listed first and marked recommended
        ordered = sorted(self._catalog.kernels, key=lambda k: not k.primary)
        items = [
            Item(
                id=k.id,
                label=k.name + (" (recommended)" if k.primary else ""),
                description=k.description,
                recommended=k.primary,
                selected=self._kernel_id == k.id,
            )
            for k in ordered
        ]
        return Screen(key="kernel", title="Kernel", kind="radio", items=items)

    def _init_screen(self) -> Screen:
        items = [
            Item(
                id=i.id,
                label=i.name + (" (recommended)" if i.recommended else ""),
                description=i.description,
                recommended=i.recommended,
                selected=self._init_id == i.id,
            )
            for i in self._catalog.inits
        ]
        return Screen(key="init", title="Init system", kind="radio", items=items)

    def _sessions_screen(self) -> Screen:
        wms = [s for s in self._catalog.sessions if s.kind == "wm"]
        des = [s for s in self._catalog.sessions if s.kind == "de"]
        items = [
            _session_item(s, s.id in self._session_ids) for s in wms + des
        ]
        return Screen(
            key="sessions", title="Window managers & desktops",
            kind="multi", items=items, notice=SESSIONS_NOTICE,
        )

    def _shell_screen(self, session_id: str) -> Screen:
        session = self._sessions_by_id[session_id]
        chosen = self._shell_choice.get(session_id)
        items = [
            _shell_item(self._shells_by_id[shell_id], chosen == shell_id)
            for shell_id in session.shell_choices
        ]
        return Screen(
            key=f"shell:{session_id}",
            title=f"Shell / bar for {session.name}",
            kind="radio", items=items,
        )

    def _support_screen(self) -> Screen:
        items = [
            Item(
                id=t.id, label=t.name, description=t.description,
                selected=t.id in self._support_ids,
            )
            for t in self._catalog.support
        ]
        return Screen(key="support", title="Support options",
                      kind="multi", items=items)

    def _gaming_screen(self) -> Screen:
        gaming = self._catalog.gaming
        item = Item(
            id="gaming", label=gaming.name,
            description=gaming.description, selected=self._gaming,
        )
        return Screen(key="gaming", title=gaming.name,
                      kind="toggle", items=[item])

    def _summary_screen(self) -> Screen:
        kernel_map = {k.id: k for k in self._catalog.kernels}
        init_map = {i.id: i for i in self._catalog.inits}
        items: List[Item] = []

        def _add(sid: str, label: str) -> None:
            items.append(Item(id=sid, label=label, description="", selected=True))

        mode_label = "Minimal install" if self._mode == "minimal" else "Custom install"
        _add("summary:mode", f"Mode: {mode_label}")
        kernel = kernel_map.get(self._kernel_id)
        _add("summary:kernel", f"Kernel: {kernel.name if kernel else '(none)'}")
        init = init_map.get(self._init_id)
        _add("summary:init", f"Init: {init.name if init else '(none)'}")
        for sid in sorted(self._session_ids):
            session = self._sessions_by_id[sid]
            label = f"Session: {session.name}"
            shell_id = self._shell_choice.get(sid)
            if shell_id:
                label += f" (shell: {self._shells_by_id[shell_id].name})"
            _add(f"summary:session:{sid}", label)
        for tid in sorted(self._support_ids):
            toggle = next(t for t in self._catalog.support if t.id == tid)
            _add(f"summary:support:{tid}", f"Support: {toggle.name}")
        _add("summary:gaming",
             f"Gaming Mode: {'on' if self._gaming else 'off'}")
        return Screen(key="summary", title="Summary",
                      kind="summary", items=items)

    # -- output --------------------------------------------------------------

    def to_selection(self) -> Selection:
        """Build the final Selection; only valid once the wizard is finished."""
        if not self._finished:
            raise ValidationError(
                "to_selection() is only valid after the wizard is finished"
            )
        return Selection(
            kernel_id=self._kernel_id,
            init_id=self._init_id,
            session_ids=sorted(self._session_ids),
            shell_choice=dict(sorted(self._shell_choice.items())),
            support_ids=sorted(self._support_ids),
            gaming=self._gaming,
            minimal=self._mode == "minimal",
        )
