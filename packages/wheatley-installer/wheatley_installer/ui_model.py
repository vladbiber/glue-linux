"""
Pure TUI wizard state machine for the Wheatley Linux installer.

NO curses imports, NO I/O: takes a loaded Catalog and exposes a deterministic
screen-by-screen wizard producing a plan.Selection. Screen sequence:
  welcome -> mode -> kernel -> init -> sessions -> shell:<sid>... ->
  support -> gaming -> [disk] -> [form:hostname .. form:timezone] -> summary
Minimal mode skips 'sessions', 'shell:*' and 'gaming'. The 'disk' screen
appears only when block devices are injected via Wizard(disks=...); the
'form:*' identity screens (ADR-8) appear when ask_identity=True, backed by
ui_forms.FormField (a password-confirm mismatch clears both fields and
returns to form:password). A finished wizard yields a WizardResult via
to_result(): Selection, chosen device path (or None), IdentitySpec (or None).
"""

from __future__ import annotations

from typing import List, Optional

from wheatley_installer.catalog import Catalog
from wheatley_installer.plan import Selection
from wheatley_installer.ui_forms import (
    FormField, build_identity_fields, disk_label, masked, submit_form,
)
# Re-exported for compat: these lived here before the ui_view split.
from wheatley_installer.ui_view import (  # noqa: F401
    DE_SECTION, DISK_MANUAL_NOTICE, DISK_NOTICE, DISKMODE_NOTICE,
    NETWORK_NOTICE_TEMPLATE, PARTITION_NOTICE, SESSIONS_NOTICE, WELCOME_NOTICE,
    Choose, Item, Screen, SetFlag, Toggle, WizardResult,
    partition_item, session_item, shell_item,
)


class ValidationError(Exception):
    """Raised when a wizard action is invalid for the current state."""


# -- wizard --------------------------------------------------------------------

class Wizard:
    """Deterministic wizard state machine over a loaded Catalog."""

    def __init__(self, catalog: Catalog, disks=None, ask_identity: bool = False,
                 identity_defaults=None):
        if not isinstance(catalog, Catalog):
            raise ValidationError(
                f"Wizard requires a Catalog, got {type(catalog).__name__}"
            )
        if disks is not None:
            disks = list(disks)
            if not disks:
                raise ValidationError("No installation disks were provided")
            for d in disks:
                if not getattr(d, "path", None):
                    raise ValidationError("Disk entries must have a device path")
        self._disks = disks
        self._device_path: Optional[str] = None
        self._partitions: Optional[list] = None  # disks.Partition list
        self._partition_path: Optional[str] = None
        self._disk_mode: str = "erase"  # 'erase' | 'existing' | 'manual'
        self._network_status: str = "checking..."
        try:
            self._forms = (
                build_identity_fields(identity_defaults) if ask_identity else None
            )
        except ValueError as exc:
            raise ValidationError(str(exc))
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

    # -- driver-injected state (I/O stays in the tui/__main__ layer) --------

    def set_network_status(self, status: str) -> None:
        """Driver injects the current connectivity status ('online'/'offline')."""
        self._network_status = str(status)

    def set_partitions(self, partitions) -> None:
        """Driver injects (or refreshes, after cfdisk) the partition list."""
        self._partitions = list(partitions)
        if self._partition_path not in {p.path for p in self._partitions}:
            self._partition_path = None  # dropped by a manual repartition

    @property
    def disk_mode(self) -> str:
        return self._disk_mode

    @property
    def selected_disk_path(self) -> Optional[str]:
        return self._device_path

    # -- navigation --------------------------------------------------------

    def _screen_keys(self) -> List[str]:
        keys = ["welcome", "network", "mode", "kernel", "init"]
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
        if self._disks is not None:
            keys.append("diskmode")
            if self._disk_mode in ("erase", "manual"):
                keys.append("disk")
            if self._disk_mode in ("existing", "manual"):
                keys.append("partition")
        if self._forms is not None:
            keys.extend(f"form:{k}" for k in self._forms)
        keys.append("summary")
        return keys

    def _validate_current(self) -> None:
        key = self._current_key
        if key == "network" and self._network_status != "connected":
            raise ValidationError("No internet connection — press N to "
                                  "connect (nmtui), then Enter to re-check.")
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
                raise ValidationError(f"Choose a shell/bar for {name} to continue.")
        if key == "disk" and self._device_path is None:
            raise ValidationError("Select a disk to continue.")
        if key == "partition":
            if not self._candidate_partitions():
                raise ValidationError(
                    "No suitable partitions found — go back and use "
                    "'Partition manually (cfdisk)' to create one."
                )
            if self._partition_path is None:
                raise ValidationError("Select a partition to continue.")
        if key.startswith("form:"):
            error, reset = submit_form(self._forms, key.split(":", 1)[1])
            if reset:
                self._current_key = "form:password"
            if error:
                raise ValidationError(error)

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
                    f"Choose is only valid on radio screens, not '{screen.key}'")
            self._apply_choose(screen, event.item_id)
        elif isinstance(event, Toggle):
            if screen.kind != "multi":
                raise ValidationError(
                    f"Toggle is only valid on multi screens, not '{screen.key}'")
            self._apply_toggle(screen, event.item_id)
        elif isinstance(event, SetFlag):
            if screen.kind != "toggle":
                raise ValidationError(
                    f"SetFlag is only valid on toggle screens, not '{screen.key}'")
            if not isinstance(event.value, bool):
                raise ValidationError("SetFlag value must be a boolean")
            self._gaming = event.value
        else:
            raise ValidationError(f"Unknown event type: {type(event).__name__}")

    def _check_item(self, screen: Screen, item_id: str) -> None:
        if item_id not in {i.id for i in screen.items}:
            raise ValidationError(
                f"Unknown item '{item_id}' on screen '{screen.key}'")

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
        elif key == "disk":
            self._device_path = item_id
        elif key == "diskmode":
            self._disk_mode = item_id
        elif key == "partition":
            self._partition_path = item_id
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

    # -- form text input (ADR-8) ---------------------------------------------

    def _current_form(self) -> FormField:
        key = self._current_key
        if not key.startswith("form:") or self._forms is None:
            raise ValidationError(
                f"Text input is only valid on form screens, not '{key}'")
        return self._forms[key.split(":", 1)[1]]

    def feed_char(self, ch: str) -> None:
        """Append one printable character to the current form field."""
        self._current_form().feed_char(ch)

    def backspace(self) -> None:
        """Delete the last character of the current form field."""
        self._current_form().backspace()

    # -- screen construction -------------------------------------------------

    def current_screen(self) -> Screen:
        key = self._current_key
        if key == "welcome":
            return Screen(key="welcome", title="Welcome to Wheatley Linux",
                          kind="info", items=[], notice=WELCOME_NOTICE)
        if key == "network":
            return Screen(
                key="network", title="Network", kind="info", items=[],
                notice=NETWORK_NOTICE_TEMPLATE.format(status=self._network_status),
            )
        if key == "diskmode":
            return self._diskmode_screen()
        if key == "partition":
            return self._partition_screen()
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
        if key == "disk":
            items = [
                Item(id=d.path, label=disk_label(d),
                     description="Removable device" if d.is_removable else "",
                     selected=self._device_path == d.path)
                for d in self._disks
            ]
            manual = self._disk_mode == "manual"
            return Screen(key="disk",
                          title="Disk to partition" if manual
                          else "Installation disk",
                          kind="radio", items=items,
                          notice=DISK_MANUAL_NOTICE if manual else DISK_NOTICE)
        if key.startswith("form:"):
            form = self._forms[key.split(":", 1)[1]]
            return Screen(key=key, title=form.label, kind="form", field=form)
        return self._summary_screen()

    def _diskmode_screen(self) -> Screen:
        items = [
            Item(id="erase", label="Erase a whole disk (guided)",
                 description="Simplest: wipes the chosen disk and lets the "
                             "installer lay out partitions automatically.",
                 recommended=True, selected=self._disk_mode == "erase"),
            Item(id="existing", label="Use an existing partition",
                 description="Formats ONLY the partition you pick; everything "
                             "else on the disk stays untouched.",
                 selected=self._disk_mode == "existing"),
            Item(id="manual", label="Partition manually (cfdisk)",
                 description="Opens cfdisk on a disk of your choice to make "
                             "room, then install into a partition you pick.",
                 selected=self._disk_mode == "manual"),
        ]
        return Screen(key="diskmode", title="Storage", kind="radio",
                      items=items, notice=DISKMODE_NOTICE)

    def _candidate_partitions(self) -> list:
        """Partitions eligible as an install target: not mounted, not an ESP."""
        return [p for p in (self._partitions or [])
                if not p.is_mounted and not p.is_esp]

    def _partition_screen(self) -> Screen:
        items = [partition_item(p, self._partition_path == p.path)
                 for p in self._candidate_partitions()]
        return Screen(key="partition", title="Installation partition",
                      kind="radio", items=items, notice=PARTITION_NOTICE)

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
            Item(id="minimal", label=minimal.name,
                 description=minimal.description,
                 selected=self._mode == "minimal"),
        ]
        return Screen(key="mode", title="Installation mode",
                      kind="radio", items=items)

    def _kernel_screen(self) -> Screen:
        # Primary kernel listed first and marked recommended
        ordered = sorted(self._catalog.kernels, key=lambda k: not k.primary)
        items = [
            Item(id=k.id,
                 label=k.name + (" (recommended)" if k.primary else ""),
                 description=k.description, recommended=k.primary,
                 selected=self._kernel_id == k.id)
            for k in ordered
        ]
        return Screen(key="kernel", title="Kernel", kind="radio", items=items)

    def _init_screen(self) -> Screen:
        items = [
            Item(id=i.id,
                 label=i.name + (" (recommended)" if i.recommended else ""),
                 description=i.description, recommended=i.recommended,
                 selected=self._init_id == i.id)
            for i in self._catalog.inits
        ]
        return Screen(key="init", title="Init system", kind="radio", items=items)

    def _sessions_screen(self) -> Screen:
        wms = [s for s in self._catalog.sessions if s.kind == "wm"]
        des = [s for s in self._catalog.sessions if s.kind == "de"]
        items = [session_item(s, s.id in self._session_ids)
                 for s in wms + des]
        return Screen(key="sessions", title="Window managers & desktops",
                      kind="multi", items=items, notice=SESSIONS_NOTICE)

    def _shell_screen(self, session_id: str) -> Screen:
        session = self._sessions_by_id[session_id]
        chosen = self._shell_choice.get(session_id)
        items = [
            shell_item(self._shells_by_id[shell_id], chosen == shell_id)
            for shell_id in session.shell_choices
        ]
        return Screen(key=f"shell:{session_id}",
                      title=f"Shell / bar for {session.name}",
                      kind="radio", items=items)

    def _support_screen(self) -> Screen:
        items = [
            Item(id=t.id, label=t.name, description=t.description,
                 selected=t.id in self._support_ids)
            for t in self._catalog.support
        ]
        return Screen(key="support", title="Support options",
                      kind="multi", items=items)

    def _gaming_screen(self) -> Screen:
        gaming = self._catalog.gaming
        item = Item(id="gaming", label=gaming.name,
                    description=gaming.description, selected=self._gaming)
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
        _add("summary:gaming", f"Gaming Mode: {'on' if self._gaming else 'off'}")
        if self._disks is not None:
            if self._disk_mode == "erase":
                _add("summary:disk",
                     f"Disk: {self._device_path or '(none)'} — WILL BE ERASED")
            else:
                _add("summary:disk",
                     f"Partition: {self._partition_path or '(none)'} — will be "
                     "formatted (rest of the disk untouched)")
        if self._forms is not None:
            for fkey, form in self._forms.items():
                if fkey == "password_confirm":
                    continue  # masked password shown once is enough
                _add(f"summary:{fkey}", f"{form.label}: {masked(form)}")
        return Screen(key="summary", title="Summary",
                      kind="summary", items=items)

    # -- output --------------------------------------------------------------

    def to_selection(self) -> Selection:
        """Build the final Selection; only valid once the wizard is finished."""
        if not self._finished:
            raise ValidationError(
                "to_selection() is only valid after the wizard is finished")
        return Selection(
            kernel_id=self._kernel_id,
            init_id=self._init_id,
            session_ids=sorted(self._session_ids),
            shell_choice=dict(sorted(self._shell_choice.items())),
            support_ids=sorted(self._support_ids),
            gaming=self._gaming,
            minimal=self._mode == "minimal",
        )

    def to_result(self) -> WizardResult:
        """Build the final WizardResult; only valid once the wizard is finished."""
        selection = self.to_selection()
        spec = None
        if self._forms is not None:
            # Imported lazily: identity pulls in the executor's subprocess
            # machinery, which must never load at ui_model import time.
            from wheatley_installer.identity import IdentityError, IdentitySpec
            f = self._forms
            try:
                spec = IdentitySpec(
                    hostname=f["hostname"].value,
                    username=f["username"].value,
                    password=f["password"].value,
                    locale=f["locale"].value,
                    timezone=f["timezone"].value,
                )
            except IdentityError as exc:
                raise ValidationError(str(exc))
        return WizardResult(selection=selection, device_path=self._device_path,
                            identity=spec, disk_mode=self._disk_mode,
                            partition_path=self._partition_path)
