"""Glue Welcome window: Welcome, System and Settings pages."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from glue_apps import i18n  # noqa: E402
from glue_apps.config import AppsConfig  # noqa: E402
from glue_apps.i18n import _, set_language  # noqa: E402
from glue_apps.pages import panel, panel_row, settings_page  # noqa: E402
from glue_apps.shell import use_adwaita_icons, ShellWindow  # noqa: E402
from glue_apps.widgets import clickable, grid, label  # noqa: E402

from . import live
from .login_screen import login_screen_panel
from .keybindings import (binding_rows, detect_session, load_shell_choices, running_processes,
                          session_name)
from .strings import RO
from .system import system_page

i18n.RO.update(RO)

ICONS = Path(__file__).resolve().parent.parent / "data" / "icons"
CATALOGS = (Path("/usr/share/glue/catalog.json"),
            Path(__file__).resolve().parents[2] / "glue-installer" / "catalog" / "catalog.json")


def _spawn(argv: list[str]) -> None:
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def session_bindings(env=os.environ, processes: set[str] | None = None,
                     catalog_paths=CATALOGS) -> tuple[str, list[tuple[str, str]]]:
    """Shortcuts of the window manager this window runs under."""
    for path in catalog_paths:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ids = [str(item.get("id", "")) for item in data.get("sessions", [])]
        detected = detect_session(ids, env, running_processes()
                                  if processes is None else processes)
        desktop = detected or env.get("XDG_CURRENT_DESKTOP", "")
        choices = load_shell_choices(Path("/etc/glue/session.conf"))
        try:
            return binding_rows(data, desktop, choices)
        except (KeyError, IndexError, TypeError):
            break
    return env.get("XDG_CURRENT_DESKTOP", "").casefold().split(":", 1)[0], []


def keycaps(keys: str) -> Gtk.Widget:
    box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER, css_classes=["keycaps"])
    for index, part in enumerate(keys.split("+")):
        if index:
            box.append(label("+", "keycap-plus"))
        box.append(label(part.strip(), "keycap", xalign=0.5))
    return box


class WelcomeWindow(ShellWindow):
    NAV = (("welcome", "Welcome", "go-home-symbolic"),
           ("system", "System", "computer-symbolic"),
           ("settings", "Settings", "emblem-system-symbolic"))
    BRAND = "Glue"
    ICON = "org.glue.Welcome"

    def __init__(self, app, config: AppsConfig, config_path: Path, data_root: Path):
        super().__init__(app, config, config_path, data_root, 1100, 760)
        Gtk.IconTheme.get_for_display(self.get_display()).add_search_path(str(ICONS))
        self.set_title(_("Welcome"))
        self.rebuild(os.environ.get("GLUE_WELCOME_PAGE", "welcome"))

    def build_pages(self):
        startup = Gtk.Switch(active=self.config.autostart, valign=Gtk.Align.CENTER)
        startup.connect("notify::active", lambda s, _p: self.set_autostart(s.get_active()))
        extra = ((_("Startup"), panel(panel_row(_("Show Welcome at login"), startup))),
                 (_("Login screen"), login_screen_panel(self)))
        return [("welcome", self._welcome_page()), ("system", system_page(self)),
                ("settings", settings_page(self, extra))]

    def _welcome_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=30,
                       css_classes=["welcome-page"])
        on_live = live.is_live()
        page.append(self._hero(on_live))
        if on_live:
            page.append(self._install_card())
            page.append(self._live_actions())
        else:
            page.append(self._update_banner())
        page.append(self._actions())
        page.append(self._shortcuts())
        page.append(self._startup())
        return page

    def _hero(self, on_live: bool = False) -> Gtk.Widget:
        hero = Gtk.Box(spacing=24, css_classes=["welcome-hero"])
        logo = Gtk.Image.new_from_icon_name(self.ICON)
        logo.set_pixel_size(84)
        hero.append(logo)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                       valign=Gtk.Align.CENTER)
        title = (_("Welcome to Glue Linux — try it, then install it") if on_live
                 else _("Welcome to Glue Linux"))
        text.append(label(title, "welcome-title", wrap=True))
        text.append(label(_("Here are the keys you will use every day. You can open this "
                            "window again with Super+Shift+F1."), "page-subtitle", wrap=True))
        hero.append(text)
        self.on_compact(lambda compact: logo.set_visible(not compact))
        return hero

    def _install_card(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       css_classes=["panel", "update-card"])
        card.append(label(_("Install Glue Linux"), "update-title", wrap=True))
        card.append(label(_("Install Glue Linux on this computer. Your files on other "
                            "drives are not touched until you confirm."),
                          "row-subtitle", wrap=True))
        button = Gtk.Button(label=_("Install Glue Linux"), halign=Gtk.Align.START,
                            css_classes=["pill", "suggested-action"])
        status = label("", "row-subtitle", wrap=True)
        reason = live.explain_unavailable()
        card.append(button)
        card.append(status)
        if reason:
            button.set_sensitive(False)
            status.set_text(_(reason))
        else:
            button.connect("clicked", lambda _b: self._spawn_install(button, status))
        return card

    def _spawn_install(self, button: Gtk.Button, status: Gtk.Label) -> None:
        argv = live.install_command()
        if argv is None:
            status.set_text(_(live.explain_unavailable() or live.FAILED_TEXT))
            return
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError:
            status.set_text(_(live.FAILED_TEXT))
            return
        button.set_sensitive(False)
        status.set_text("")

        def watch() -> None:
            started = time.monotonic()
            code = proc.wait()
            quick_failure = code != 0 and time.monotonic() - started < 3
            GLib.idle_add(self._install_done, button, status, code if quick_failure else 0)

        threading.Thread(target=watch, daemon=True).start()

    def _install_done(self, button: Gtk.Button, status: Gtk.Label, code: int) -> bool:
        button.set_sensitive(True)
        if code:
            status.set_text(_(live.exit_code_message(code)))
        return False

    def _live_actions(self) -> Gtk.Widget:
        cards = []
        for item in live.quick_actions():
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["action-card"])
            card.append(label(_(item.title), "row-title"))
            card.append(label(_(item.subtitle), "row-subtitle", wrap=True))
            argv = list(item.argv) if item.argv else None
            cards.append(clickable(card, (lambda a=argv: _spawn(a)) if argv
                                   else (lambda: self.show_page("system"))))
        return grid(cards, 2)

    def _update_banner(self) -> Gtk.Widget:
        banner = Gtk.Box(spacing=18, css_classes=["panel", "update-card"])
        icon = Gtk.Image.new_from_icon_name("software-update-available-symbolic")
        icon.set_pixel_size(40)
        icon.add_css_class("status-icon")
        banner.append(icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
                       valign=Gtk.Align.CENTER)
        text.append(label(_("Update the whole system"), "update-title", wrap=True))
        text.append(label(_("System, drivers and apps in one go, from Glue Apps."),
                          "row-subtitle", wrap=True))
        banner.append(text)
        button = Gtk.Button(label=_("Update now"), valign=Gtk.Align.CENTER,
                            css_classes=["pill", "suggested-action"])
        button.connect("clicked", lambda _b: _spawn(["glue-apps", "--page", "updates"]))
        banner.append(button)
        self.on_compact(lambda compact: banner.set_orientation(
            Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL))
        return banner

    def _actions(self) -> Gtk.Widget:
        cards = []
        for title, text, icon, action in (
                (_("Get apps"), _("Find and install apps."), "org.glue.Apps",
                 lambda: _spawn(["glue-apps"])),
                (_("System"), _("Power, fonts and hardware."), "computer-symbolic",
                 lambda: self.show_page("system"))):
            card = Gtk.Box(spacing=14, css_classes=["action-card"])
            image = Gtk.Image.new_from_icon_name(icon)
            image.set_pixel_size(28)
            image.add_css_class("action-icon")
            card.append(image)
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
            words.append(label(title, "row-title"))
            words.append(label(text, "row-subtitle", wrap=True))
            card.append(words)
            cards.append(clickable(card, action))
        return grid(cards, 2)

    def _shortcuts(self) -> Gtk.Widget:
        session, rows = session_bindings()
        config = Path.home() / ".config" / session
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        heading = Gtk.Box(css_classes=["section-header"])
        heading.append(label(_("Keyboard shortcuts"), "section-title"))
        heading.append(label(f"  ·  {session_name(session)}", "section-session"))
        heading.append(Gtk.Box(hexpand=True))
        # the classic desktops keep their settings in their own apps
        if config.is_dir():
            open_config = Gtk.Button(label=_("Open config"), css_classes=["flat", "section-link"])
            open_config.connect("clicked", lambda _b: _spawn(["xdg-open", str(config)]))
            heading.append(open_config)
        box.append(heading)
        if not rows:
            box.append(label(_("Shortcuts are not available for this session."), "dim-label"))
            return box
        lines = []
        for keys, action in rows:
            line = Gtk.Box(spacing=16, css_classes=["shortcut-row"])
            line.append(label(action, "shortcut-action", wrap=True, chars=14))
            line.append(Gtk.Box(hexpand=True))
            line.append(keycaps(keys))
            lines.append(line)
        shortcuts = grid(lines, 2)
        shortcuts.add_css_class("panel")
        shortcuts.add_css_class("shortcut-grid")
        shortcuts.set_row_spacing(0)
        box.append(shortcuts)
        return box

    def _startup(self) -> Gtk.Widget:
        row = Gtk.Box(spacing=12, css_classes=["panel", "panel-row"])
        row.append(label(_("Show at startup"), "row-title"))
        row.append(Gtk.Box(hexpand=True))
        switch = Gtk.Switch(active=self.config.autostart, valign=Gtk.Align.CENTER)
        switch.connect("notify::active", lambda s, _p: self.set_autostart(s.get_active()))
        row.append(switch)
        return row


class WelcomeApplication(Adw.Application):
    def __init__(self, config: AppsConfig, config_path: Path, data_root: Path):
        super().__init__(application_id="org.glue.Welcome",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.args = (config, config_path, data_root)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        use_adwaita_icons()

    def do_activate(self) -> None:
        window = self.props.active_window or WelcomeWindow(self, *self.args)
        window.present()


def run(config: AppsConfig, config_path: Path, data_root: Path) -> int:
    set_language(config.language)
    return WelcomeApplication(config, config_path, data_root).run(None)
