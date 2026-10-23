"""glue-keyboard: add keyboard layouts and pick the keys that switch them."""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from glue_apps import i18n  # noqa: E402
from glue_apps.i18n import _, set_language  # noqa: E402
from glue_apps.shell import title_buttons, use_adwaita_icons  # noqa: E402

from . import keyboard  # noqa: E402
from .strings import RO  # noqa: E402

i18n.RO.update(RO)

CSS = b"""
.status-title { font-size: 1.35em; font-weight: 700; }
.kb-error { color: @error_color; }
"""

NATIVE_NOTE = "This desktop has its own keyboard settings; they open from here."


def _spawn(argv: list[str] | None) -> None:
    if not argv:
        return
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


class KeyboardWindow(Adw.ApplicationWindow):
    def __init__(self, app: "KeyboardApplication"):
        super().__init__(application=app, default_width=480, default_height=620,
                         title=_("Keyboard Layout"))
        self.catalog = keyboard.load_catalog()
        self.config = keyboard.current()
        self.session = keyboard.detect_session()
        self.busy = False

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        header = Adw.HeaderBar()
        layout = title_buttons()
        if layout is not None:
            header.set_decoration_layout(layout)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                       margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        status = Gtk.Box(spacing=14)
        icon = Gtk.Image.new_from_icon_name("input-keyboard-symbolic")
        icon.set_pixel_size(40)
        status.append(icon)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                        valign=Gtk.Align.CENTER, hexpand=True)
        words.append(Gtk.Label(label=_("Keyboard layouts"), xalign=0, wrap=True,
                               css_classes=["status-title"]))
        words.append(Gtk.Label(label=_("The first one is used at login. Switch between "
                                       "them with the keys below."),
                               xalign=0, wrap=True, css_classes=["dim-label"]))
        status.append(words)
        body.append(status)

        native = keyboard.settings_app(self.session)
        if native:
            body.append(Gtk.Label(label=_(NATIVE_NOTE), xalign=0, wrap=True))
            open_native = Gtk.Button(label=_("Open keyboard settings"),
                                     halign=Gtk.Align.START,
                                     css_classes=["pill", "suggested-action"])
            open_native.connect("clicked", lambda _b: _spawn(native))
            body.append(open_native)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                css_classes=["boxed-list"])
        body.append(self.list)

        self.add_button = Gtk.Button(label=_("Add a layout…"), halign=Gtk.Align.START,
                                     css_classes=["pill"])
        self.add_button.connect("clicked", lambda _b: self.open_picker())
        body.append(self.add_button)

        body.append(Gtk.Label(label=_("Switch layouts with"), xalign=0,
                              css_classes=["heading"]))
        self.switch_names = [name for _opt, name in keyboard.SWITCH_OPTIONS]
        self.switch_drop = Gtk.DropDown.new_from_strings(self.switch_names)
        self.switch_drop.set_halign(Gtk.Align.START)
        opts = [opt for opt, _name in keyboard.SWITCH_OPTIONS]
        chosen = self.config.switch
        self.switch_drop.set_selected(opts.index(chosen) if chosen in opts else 0)
        body.append(self.switch_drop)

        self.message = Gtk.Label(xalign=0, wrap=True, visible=False)
        body.append(self.message)

        scroller = Gtk.ScrolledWindow(child=body, vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        view = Adw.ToolbarView(content=scroller)
        view.add_top_bar(header)
        bar = Gtk.Box(margin_top=12, margin_bottom=12, margin_start=18, margin_end=18)
        self.apply_button = Gtk.Button(label=_("Apply"), hexpand=True,
                                       css_classes=["pill", "suggested-action"])
        self.apply_button.connect("clicked", lambda _b: self.apply())
        bar.append(self.apply_button)
        view.add_bottom_bar(bar)
        self.set_content(view)
        self.fill()

    def fill(self) -> None:
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        count = len(self.config.layouts)
        for index, item in enumerate(self.config.layouts):
            line = Gtk.Box(spacing=8, margin_top=8, margin_bottom=8,
                           margin_start=12, margin_end=8)
            name = keyboard.describe(item, self.catalog)
            line.append(Gtk.Label(label=name, xalign=0, hexpand=True,
                                  ellipsize=Pango.EllipsizeMode.END,
                                  css_classes=["heading"] if index == 0 else []))
            if index == 0:
                line.append(Gtk.Label(label=_("Default"), css_classes=["dim-label"]))
            else:
                up = Gtk.Button(icon_name="go-up-symbolic", tooltip_text=_("Make default"),
                                css_classes=["flat"])
                up.connect("clicked", lambda _b, i=index: self.move_up(i))
                line.append(up)
            remove = Gtk.Button(icon_name="list-remove-symbolic", tooltip_text=_("Remove"),
                                css_classes=["flat"], sensitive=count > 1)
            remove.connect("clicked", lambda _b, i=index: self.remove(i))
            line.append(remove)
            self.list.append(Gtk.ListBoxRow(child=line, activatable=False))
        self.add_button.set_sensitive(count < keyboard.MAX_LAYOUTS)

    def move_up(self, index: int) -> None:
        items = self.config.layouts
        items[index - 1], items[index] = items[index], items[index - 1]
        self.fill()

    def remove(self, index: int) -> None:
        if len(self.config.layouts) > 1:
            del self.config.layouts[index]
            self.fill()

    def add(self, item: keyboard.Layout) -> None:
        keys = {l.key for l in self.config.layouts}
        if item.key not in keys and len(self.config.layouts) < keyboard.MAX_LAYOUTS:
            self.config.layouts.append(keyboard.Layout(item.layout, item.variant))
            self.fill()

    def open_picker(self) -> None:
        dialog = Adw.Window(transient_for=self, modal=True, default_width=420,
                            default_height=520, title=_("Add a layout"))
        header = Adw.HeaderBar()
        entry = Gtk.SearchEntry(placeholder_text=_("Search: Romanian, German, French…"),
                                hexpand=True)
        results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                              css_classes=["boxed-list"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        box.append(entry)
        box.append(results)
        view = Adw.ToolbarView(content=Gtk.ScrolledWindow(
            child=box, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        view.add_top_bar(header)
        dialog.set_content(view)

        def show(_e=None) -> None:
            while (row := results.get_row_at_index(0)) is not None:
                results.remove(row)
            for item in keyboard.search(self.catalog, entry.get_text(), 100):
                row = Gtk.ListBoxRow(activatable=True)
                row.set_child(Gtk.Label(label=item.name, xalign=0, margin_top=8,
                                        margin_bottom=8, margin_start=12, margin_end=12,
                                        ellipsize=Pango.EllipsizeMode.END))
                row.item = item
                results.append(row)

        def picked(_l, row) -> None:
            self.add(row.item)
            dialog.close()

        entry.connect("search-changed", show)
        results.connect("row-activated", picked)
        show()
        dialog.present()
        entry.grab_focus()

    def apply(self) -> None:
        if self.busy:
            return
        switch = keyboard.SWITCH_OPTIONS[self.switch_drop.get_selected()][0]
        self.config.options = keyboard.merge_options(self.config.options, switch)
        self.busy = True
        self.apply_button.set_sensitive(False)
        self.show_message("")

        def work() -> None:
            error = keyboard.apply_system(self.config)
            live = keyboard.apply_live(self.config, self.session) if not error else ""
            GLib.idle_add(self._applied, error or live)

        threading.Thread(target=work, daemon=True).start()

    def _applied(self, error: str) -> bool:
        self.busy = False
        self.apply_button.set_sensitive(True)
        if error:
            self.show_message(_("Not changed: ") + _(error), error=True)
        elif keyboard.settings_app(self.session):
            self.show_message(_("Saved for login. Set the same layouts in the desktop's "
                                "keyboard settings."))
        else:
            self.show_message(_("Saved. The new layouts work now."))
        return False

    def show_message(self, text: str, error: bool = False) -> None:
        self.message.set_text(text)
        self.message.set_visible(bool(text))
        if error:
            self.message.add_css_class("kb-error")
        else:
            self.message.remove_css_class("kb-error")


class KeyboardApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.glue.Keyboard",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        use_adwaita_icons()

    def do_activate(self) -> None:
        window = self.props.active_window or KeyboardWindow(self)
        window.present()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="glue-keyboard")
    parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        from glue_apps.app import load_config
        set_language(load_config().language)
    except Exception:
        pass
    app = KeyboardApplication()
    try:
        return app.run(None)
    except GLib.Error as exc:
        print(f"glue-keyboard: {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
