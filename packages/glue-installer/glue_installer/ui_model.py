"""
Pure TUI wizard state machine for the Glue Linux installer.

NO curses imports, NO I/O: takes a loaded Catalog and exposes a deterministic
screen-by-screen wizard producing a plan.Selection. Screen sequence:
  welcome -> network -> mode -> kernel -> init -> sessions -> shell:<sid>... ->
  support -> gaming -> [scheduler] -> [diskmode, disk/partition, swap,
  [hibernate]] -> [form:*] -> summary
Minimal mode skips sessions/shell/gaming; offline mode (allow_offline on the
network screen) skips mode/kernel/init/sessions/gaming/scheduler
and clones the live system (stock kernel, runit, default session).
"""

from __future__ import annotations

from typing import List, Optional

from glue_installer.catalog import Catalog
from glue_installer.clone import CLONE_INIT
from glue_installer.plan import Selection
from glue_installer.ui_forms import (
    FormField, build_identity_fields, build_identity_spec,
    masked, prefill_timezone_field, submit_form)
# Re-exported for compat: these lived here before the ui_view split.
from glue_installer.ui_view import (  # noqa: F401
    DE_SECTION, DISK_MANUAL_NOTICE, DISK_NOTICE, DISKMODE_NOTICE,
    HIBERNATE_NOTICE, NETWORK_NOTICE_TEMPLATE, OFFLINE_SUMMARY, PARTITION_NOTICE,
    SCHEDULER_NOTICE, SESSIONS_NOTICE, SWAP_NOTICE, SWAP_SUMMARY, WELCOME_NOTICE,
    Choose, Item, Screen, SetFlag, Toggle, WizardResult, diskmode_items, summary_items,
    disk_items, hibernate_item, init_items, kernel_items, mode_items, partition_item,
    scheduler_items, session_item, shell_item, sessions_items, shell_items, support_items,
    swap_items)


class ValidationError(Exception):
    """Raised when a wizard action is invalid for the current state."""


# -- wizard --------------------------------------------------------------------

class Wizard:
    """Deterministic wizard state machine over a loaded Catalog."""

    def __init__(self, catalog: Catalog, disks=None, ask_identity: bool = False,
                 identity_defaults=None, cpu_v3: bool = False,
                 is_laptop: bool = False):
        if not isinstance(catalog, Catalog):
            raise ValidationError(
                f"Wizard requires a Catalog, got {type(catalog).__name__}")
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
            self._forms = (build_identity_fields(identity_defaults)
                           if ask_identity else None)
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
        # The catalog's FIRST session is the Glue default (gluewc): it starts
        # preselected, together with its first shell choice (glueqs), so
        # accepting the defaults lands on a working desktop. Deselecting it
        # on the sessions screen stays a single keypress.
        if catalog.sessions:
            default_session = catalog.sessions[0]
            self._session_ids.add(default_session.id)
            if default_session.shell_choices:
                self._shell_choice[default_session.id] = \
                    default_session.shell_choices[0]
        self._support_ids: set = {t.id for t in catalog.support if t.default}
        self._gaming: bool = False
        # catalog default preselected: the scheduler screen never blocks
        self._scheduler: str = next(
            (o.id for o in catalog.gaming.schedulers if o.default), "scx_lavd")
        self._cpu_v3 = cpu_v3
        self._is_laptop = bool(is_laptop)
        self._swap_mode: str = "auto"  # 'auto' | 'zram' | 'none'
        self._hibernate: bool = False  # laptops only, swap auto only

        # Navigation state
        self._current_key: str = "welcome"
        self._finished: bool = False

    # -- driver-injected state (I/O stays in the tui/__main__ layer) --------

    def set_network_status(self, status: str) -> None:
        """Driver injects the current connectivity status ('online'/'offline')."""
        self._network_status = str(status)

    def prefill_timezone(self, tz) -> bool:
        # GeoIP tz from the driver - see ui_forms.prefill_timezone_field
        return prefill_timezone_field(self._forms, tz)

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
        keys = ["welcome", "network"]
        if self._mode != "offline":
            keys += ["mode", "kernel", "init"]
        if self._mode == "custom":
            keys.append("sessions")
        if self._mode != "minimal":
            keys.extend(
                f"shell:{sid}"
                for sid in sorted(self._session_ids)
                if self._sessions_by_id[sid].shell_choices
            )
        keys.append("support")
        if self._mode == "custom":
            keys.append("gaming")
            if self._gaming and self._catalog.gaming.schedulers:
                keys.append("scheduler")
        if self._disks is not None:
            keys.append("diskmode")
            if self._disk_mode in ("erase", "manual"):
                keys.append("disk")
            if self._disk_mode in ("existing", "manual"):
                keys.append("partition")
            keys.append("swap")
            if self._is_laptop and self._swap_mode == "auto":
                keys.append("hibernate")
        if self._forms is not None:
            keys.extend(f"form:{k}" for k in self._forms)
        keys.append("summary")
        return keys

    def _validate_current(self) -> None:
        key = self._current_key
        if key == "network" and self._network_status != "connected" \
                and self._mode != "offline":
            raise ValidationError("No internet connection — press N to "
                                  "connect (nmtui), then Enter to re-check, "
                                  "or O to install offline.")
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

    def allow_offline(self) -> None:
        """Offline install: clone the live system instead of basestrap.
        Only on the network screen. Fixes the live kernel/init and the default
        session (its first shell preselected); gaming/other desktops need the
        online path. The network check no longer blocks afterwards."""
        if self._current_key != "network":
            raise ValidationError("Offline install can only be chosen on the "
                                  "network screen.")
        self._mode = "offline"
        self._kernel_id = self._default_kernel_id()
        self._init_id = CLONE_INIT
        self._gaming = False
        self._session_ids.clear()
        self._shell_choice.clear()
        if self._catalog.sessions:
            default_session = self._catalog.sessions[0]
            self._session_ids.add(default_session.id)
            if default_session.shell_choices:
                self._shell_choice[default_session.id] = \
                    default_session.shell_choices[0]

    def _default_kernel_id(self) -> Optional[str]:
        kernels = self._catalog.kernels
        return next((k.id for k in kernels if k.primary),
                    kernels[0].id if kernels else None)

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
            if screen.key == "hibernate":
                self._hibernate = event.value
            else:
                self._gaming = event.value
        else:
            raise ValidationError(f"Unknown event type: {type(event).__name__}")

    def _check_item(self, screen: Screen, item_id: str) -> None:
        if item_id not in {i.id for i in screen.items}:
            raise ValidationError(f"Unknown item '{item_id}' on '{screen.key}'")

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
        elif key == "scheduler":
            self._scheduler = item_id
        elif key == "swap":
            self._swap_mode = item_id
            if item_id != "auto":
                self._hibernate = False  # hibernation needs the disk swap
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
                session = self._sessions_by_id[item_id]
                if session.shell_choices and item_id not in self._shell_choice:
                    # the first listed shell is the recommended default
                    self._shell_choice[item_id] = session.shell_choices[0]
        elif screen.key == "support":
            self._support_ids.symmetric_difference_update({item_id})

    # -- form text input ---------------------------------------------

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
            return Screen(key="welcome", title="Welcome to Glue Linux",
                          kind="info", items=[], notice=WELCOME_NOTICE)
        if key == "network":
            return Screen(key="network", title="Network", kind="info", items=[],
                          notice=NETWORK_NOTICE_TEMPLATE.format(
                              status=self._network_status))
        if key.startswith("shell:"):
            return self._shell_screen(key.split(":", 1)[1])
        if key == "disk":
            items = disk_items(self._disks, self._device_path)
            manual = self._disk_mode == "manual"
            return Screen(key="disk",
                          title="Disk to partition" if manual
                          else "Installation disk",
                          kind="radio", items=items,
                          notice=DISK_MANUAL_NOTICE if manual else DISK_NOTICE)
        if key.startswith("form:"):
            form = self._forms[key.split(":", 1)[1]]
            return Screen(key=key, title=form.label, kind="form", field=form)
        builders = {
            "diskmode": self._diskmode_screen, "partition": self._partition_screen,
            "mode": self._mode_screen, "kernel": self._kernel_screen,
            "init": self._init_screen, "sessions": self._sessions_screen,
            "support": self._support_screen, "gaming": self._gaming_screen,
            "scheduler": self._scheduler_screen, "swap": self._swap_screen,
            "hibernate": self._hibernate_screen,
        }
        return builders.get(key, self._summary_screen)()

    def _diskmode_screen(self) -> Screen:
        return Screen(key="diskmode", title="Storage", kind="radio",
                      items=diskmode_items(self._disk_mode),
                      notice=DISKMODE_NOTICE)

    def _candidate_partitions(self) -> list:
        """Partitions eligible as an install target: not mounted, not an ESP."""
        return [p for p in (self._partitions or [])
                if not p.is_mounted and not p.is_esp]

    def _partition_screen(self) -> Screen:
        return Screen(key="partition", title="Installation partition",
                      kind="radio", notice=PARTITION_NOTICE,
                      items=[partition_item(p, self._partition_path == p.path)
                             for p in self._candidate_partitions()])

    def _mode_screen(self) -> Screen:
        return Screen(key="mode", title="Installation mode", kind="radio",
                      items=mode_items(self._mode, self._catalog.minimal))

    def _kernel_screen(self) -> Screen:
        return Screen(key="kernel", title="Kernel", kind="radio",
                      items=kernel_items(self._catalog.kernels,
                                         self._kernel_id, self._cpu_v3))

    def _init_screen(self) -> Screen:
        return Screen(key="init", title="Init system", kind="radio",
                      items=init_items(self._catalog.inits, self._init_id))

    def _sessions_screen(self) -> Screen:
        return Screen(key="sessions", title="Window managers & desktops",
                      kind="multi", notice=SESSIONS_NOTICE, items=sessions_items(
                          self._catalog.sessions, self._session_ids))

    def _shell_screen(self, session_id: str) -> Screen:
        session = self._sessions_by_id[session_id]
        return Screen(key=f"shell:{session_id}", kind="radio",
                      title=f"Shell / bar for {session.name}",
                      items=shell_items(session, self._shells_by_id,
                                        self._shell_choice.get(session_id)))

    def _support_screen(self) -> Screen:
        return Screen(key="support", title="Support options", kind="multi",
                      items=support_items(self._catalog.support,
                                          self._support_ids))

    def _swap_screen(self) -> Screen:
        return Screen(key="swap", title="Swap", kind="radio",
                      items=swap_items(self._swap_mode), notice=SWAP_NOTICE)

    def _hibernate_screen(self) -> Screen:
        return Screen(key="hibernate", title="Hibernation", kind="toggle",
                      items=[hibernate_item(self._hibernate)],
                      notice=HIBERNATE_NOTICE)

    def _gaming_screen(self) -> Screen:
        gaming = self._catalog.gaming
        return Screen(key="gaming", title=gaming.name, kind="toggle", items=[
            Item(id="gaming", label=gaming.name,
                 description=gaming.description, selected=self._gaming)])

    def _scheduler_screen(self) -> Screen:
        return Screen(key="scheduler", title="CPU scheduler", kind="radio",
                      notice=SCHEDULER_NOTICE, items=scheduler_items(
                          self._catalog.gaming.schedulers, self._scheduler))

    def _summary_screen(self) -> Screen:
        return Screen(key="summary", title="Summary", kind="summary",
                      items=summary_items(self))

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
            scheduler=self._scheduler,
            swap_mode=self._swap_mode,
            hibernate=self._hibernate,
            offline=self._mode == "offline",
        )

    def to_result(self) -> WizardResult:
        """Build the final WizardResult; only valid once the wizard is finished."""
        selection = self.to_selection()
        try:
            spec = build_identity_spec(self._forms)
        except ValueError as exc:
            raise ValidationError(str(exc))
        return WizardResult(selection=selection, device_path=self._device_path,
                            identity=spec, disk_mode=self._disk_mode,
                            partition_path=self._partition_path)
