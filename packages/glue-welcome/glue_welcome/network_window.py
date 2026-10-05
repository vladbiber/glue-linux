"""glue-network: a simple Wi-Fi window for the live ISO and the installed system."""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from . import network  # noqa: E402

POLL_SECONDS = 3
CLOSE_DELAY_MS = 1500
HINT_TEXT = "Using a cable? Plug it in — it connects by itself."

CSS = b"""
.status-title { font-size: 1.35em; font-weight: 700; }
.net-error { color: @error_color; }
"""


def _spawn(argv: list[str] | None) -> None:
    if not argv:
        return
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


class NetworkRow(Gtk.ListBoxRow):
    def __init__(self, window: "NetworkWindow", net: network.Network, saved: bool):
        super().__init__(activatable=True)
        self.window, self.net, self.saved = window, net, saved
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)
        line = Gtk.Box(spacing=12)
        line.append(Gtk.Image.new_from_icon_name(net.signal_icon))
        line.append(Gtk.Label(label=net.ssid, xalign=0, hexpand=True,
                              ellipsize=Pango.EllipsizeMode.END, css_classes=["heading"] if net.in_use else []))
        if net.secured:
            line.append(Gtk.Image.new_from_icon_name("system-lock-screen-symbolic"))
        if net.in_use:
            line.append(Gtk.Image.new_from_icon_name("object-select-symbolic"))
        box.append(line)

        self.entry = Gtk.PasswordEntry(show_peek_icon=True, hexpand=True,
                                       placeholder_text="Password")
        self.entry.connect("activate", lambda _e: self.submit())
        self.button = Gtk.Button(label="Connect", css_classes=["suggested-action"])
        self.button.connect("clicked", lambda _b: self.submit())
        form = Gtk.Box(spacing=8)
        form.append(self.entry)
        form.append(self.button)
        self.error = Gtk.Label(xalign=0, wrap=True, visible=False, css_classes=["net-error"])
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.append(form)
        inner.append(self.error)
        self.revealer = Gtk.Revealer(child=inner, reveal_child=False)
        box.append(self.revealer)
        self.set_child(box)

    def open_password(self, error: str = "") -> None:
        self.revealer.set_reveal_child(True)
        self.error.set_text(error)
        self.error.set_visible(bool(error))
        self.entry.grab_focus()

    def close_password(self) -> None:
        self.revealer.set_reveal_child(False)
        self.entry.set_text("")
        self.error.set_visible(False)

    @property
    def open(self) -> bool:
        return self.revealer.get_reveal_child()

    def submit(self) -> None:
        self.window.connect_to(self, self.entry.get_text())


class NetworkWindow(Adw.ApplicationWindow):
    def __init__(self, app: "NetworkApplication"):
        super().__init__(application=app, default_width=460, default_height=560,
                         title="Network")
        self.app = app
        self.state: network.State | None = None
        self.busy = False
        self.connecting = ""
        self.closing = False
        self.rows: list[NetworkRow] = []
        self.shown: tuple = ()

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        header = Adw.HeaderBar()
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic",
                                         tooltip_text="Refresh")
        self.refresh_button.connect("clicked", lambda _b: self.refresh(rescan=True))
        header.pack_start(self.refresh_button)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                       margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        status = Gtk.Box(spacing=14)
        self.status_icon = Gtk.Image.new_from_icon_name("network-offline-symbolic")
        self.status_icon.set_pixel_size(40)
        status.append(self.status_icon)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                        valign=Gtk.Align.CENTER, hexpand=True)
        self.status_title = Gtk.Label(label="Checking…", xalign=0, wrap=True,
                                      css_classes=["status-title"])
        self.status_detail = Gtk.Label(xalign=0, wrap=True, visible=False,
                                       css_classes=["dim-label"])
        words.append(self.status_title)
        words.append(self.status_detail)
        status.append(words)
        body.append(status)

        self.radio_button = Gtk.Button(label="Turn on Wi-Fi", visible=False,
                                       halign=Gtk.Align.START,
                                       css_classes=["pill", "suggested-action"])
        self.radio_button.connect("clicked", lambda _b: self.turn_on_wifi())
        body.append(self.radio_button)

        body.append(Gtk.Label(label=HINT_TEXT, xalign=0, wrap=True, css_classes=["dim-label"]))

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                css_classes=["boxed-list"])
        self.list.connect("row-activated", lambda _l, row: self.on_row(row))
        self.empty = Gtk.Label(label="Looking for networks…", xalign=0, wrap=True,
                               css_classes=["dim-label"])
        body.append(self.empty)
        body.append(self.list)

        advanced = Gtk.Button(label="Advanced…", halign=Gtk.Align.START,
                              css_classes=["flat"])
        advanced.connect("clicked", lambda _b: _spawn(network.advanced_command()))
        advanced.set_sensitive(network.advanced_command() is not None)
        body.append(advanced)

        scroller = Gtk.ScrolledWindow(child=body, vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        view = Adw.ToolbarView(content=scroller)
        view.add_top_bar(header)
        self.continue_button = None
        if app.installer:
            bar = Gtk.Box(margin_top=12, margin_bottom=12, margin_start=18, margin_end=18)
            self.continue_button = Gtk.Button(label="Continue without internet",
                                              hexpand=True, css_classes=["pill"])
            self.continue_button.connect("clicked", lambda _b: app.quit())
            bar.append(self.continue_button)
            view.add_bottom_bar(bar)
        self.set_content(view)

        self.refresh(rescan=True)
        GLib.timeout_add_seconds(POLL_SECONDS, self._tick)

    def _tick(self) -> bool:
        if not self.connecting:
            self.refresh()
        return True

    def refresh(self, rescan: bool = False) -> None:
        if self.busy:
            return
        self.busy = True
        self.refresh_button.set_sensitive(False)

        def work() -> None:
            try:
                state = network.snapshot(rescan=rescan)
            except Exception:
                state = network.State(nm_running=False, connectivity="none")
            GLib.idle_add(self._apply, state)

        threading.Thread(target=work, daemon=True).start()

    def _apply(self, state: network.State) -> bool:
        self.busy = False
        self.refresh_button.set_sensitive(True)
        self.state = state
        self._show_status(state)
        self.radio_button.set_visible(state.nm_running and state.wifi_device
                                      and not state.wifi_on)
        if not any(row.open for row in self.rows):
            self._fill_list(state)
        if self.continue_button is not None:
            online = state.connectivity == "full"
            self.continue_button.set_label("Continue" if online else "Continue without internet")
            if online:
                self.continue_button.add_css_class("suggested-action")
            else:
                self.continue_button.remove_css_class("suggested-action")
            if online and self.app.close_when_online and not self.closing:
                self.closing = True
                GLib.timeout_add(CLOSE_DELAY_MS, self._close_online)
        return False

    def _close_online(self) -> bool:
        self.app.quit()
        return False

    def _show_status(self, state: network.State) -> None:
        if self.connecting:
            title, detail = f"Connecting to {self.connecting}…", ""
        else:
            title, detail = network.status_text(state)
        self.status_title.set_text(title)
        self.status_detail.set_text(detail)
        self.status_detail.set_visible(bool(detail))
        if state.connectivity == "full":
            icon = "network-wireless-signal-excellent-symbolic" if state.active_ssid \
                else "network-wired-symbolic"
        elif state.active_ssid or state.ethernet:
            icon = "network-wireless-acquiring-symbolic"
        else:
            icon = "network-offline-symbolic"
        self.status_icon.set_from_icon_name(icon)

    def _fill_list(self, state: network.State) -> None:
        shown = tuple((n, n.ssid in state.saved) for n in state.networks)
        if shown == self.shown and self.rows:
            return
        self.shown = shown
        for row in self.rows:
            self.list.remove(row)
        self.rows = [NetworkRow(self, net, saved) for net, saved in shown]
        for row in self.rows:
            self.list.append(row)
        self.list.set_visible(bool(self.rows))
        if not state.nm_running:
            text = network.NO_NM_TEXT
        elif not state.wifi_device:
            text = "No Wi-Fi adapter was found on this computer."
        elif not state.wifi_on:
            text = "Wi-Fi is off."
        else:
            text = "Looking for networks…"
        self.empty.set_text(text)
        self.empty.set_visible(not self.rows)

    def on_row(self, row: NetworkRow) -> None:
        if self.connecting or row.net.in_use:
            return
        for other in self.rows:
            if other is not row:
                other.close_password()
        if row.net.secured and not row.saved and not row.net.enterprise:
            if row.open:
                row.close_password()
            else:
                row.open_password()
            return
        self.connect_to(row, "")

    def connect_to(self, row: NetworkRow, password: str) -> None:
        if self.connecting:
            return
        net = row.net
        self.connecting = net.ssid
        row.button.set_sensitive(False)
        row.entry.set_sensitive(False)
        if self.state is not None:
            self._show_status(self.state)

        def work() -> None:
            try:
                error = network.connect(net, password)
            except Exception:
                error = network.CONNECT_FAILED_TEXT
            GLib.idle_add(self._connected, row, error, bool(password))

        threading.Thread(target=work, daemon=True).start()

    def _connected(self, row: NetworkRow, error: str, had_password: bool) -> bool:
        self.connecting = ""
        row.button.set_sensitive(True)
        row.entry.set_sensitive(True)
        if error:
            if row.net.secured and not row.net.enterprise:
                row.saved = False
                row.open_password(network.WRONG_PASSWORD_TEXT if not had_password else error)
            else:
                self.status_title.set_text(error)
        else:
            row.close_password()
        self.refresh()
        return False

    def turn_on_wifi(self) -> None:
        self.radio_button.set_sensitive(False)

        def work() -> None:
            network.set_wifi_radio(True)
            GLib.idle_add(self._radio_done)

        threading.Thread(target=work, daemon=True).start()

    def _radio_done(self) -> bool:
        self.radio_button.set_sensitive(True)
        self.refresh(rescan=True)
        return False


class NetworkApplication(Adw.Application):
    def __init__(self, installer: bool, close_when_online: bool):
        super().__init__(application_id="org.glue.Network",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.installer = installer
        self.close_when_online = close_when_online

    def do_activate(self) -> None:
        window = self.props.active_window or NetworkWindow(self)
        window.present()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="glue-network")
    parser.add_argument("--installer", action="store_true",
                        help="show a Continue button for the installer")
    parser.add_argument("--close-when-online", action="store_true",
                        help="exit on its own once the internet works")
    parser.add_argument("url", nargs="?", help=argparse.SUPPRESS)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    app = NetworkApplication(args.installer, args.close_when_online)
    try:
        return app.run(None)
    except GLib.Error as exc:
        print(f"glue-network: {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
