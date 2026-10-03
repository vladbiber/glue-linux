"""Live GTK theme and layout application."""

from __future__ import annotations

from pathlib import Path

from gi.repository import Gdk, GLib, Gtk

from .config import load_layout


class ThemeController:
    def __init__(self, data_root: Path, sidebar: Gtk.ListBox,
                 nav_labels: list[Gtk.Label], hero: Gtk.Widget,
                 tiles: Gtk.Widget) -> None:
        self.data_root = data_root
        self.sidebar = sidebar
        self.nav_labels = nav_labels
        self.hero = hero
        self.tiles = tiles
        self.css = Gtk.CssProvider()
        self.layout_css = Gtk.CssProvider()
        display = Gdk.Display.get_default()
        for provider in (self.css, self.layout_css):
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def apply(self, theme: str) -> None:
        try:
            self.css.load_from_path(
                str(self.data_root / "themes" / theme / "theme.css"))
        except GLib.Error:
            return
        layout = load_layout(self.data_root / "themes", theme)
        compact = layout["density"] == "compact"
        airy = layout["density"] == "airy"
        padding = 5 if compact else 12 if airy else 8
        self.layout_css.load_from_string(
            f".hero, .tile-button {{ border-radius: {layout['card_radius']}px; }} "
            f".app-row {{ padding-top: {padding}px; padding-bottom: {padding}px; }}")
        icons_only = layout["sidebar"] == "icons"
        self.sidebar.set_size_request(82 if icons_only else 190, -1)
        for label in self.nav_labels:
            label.set_visible(not icons_only)
        self.hero.set_visible(bool(layout["hero"]))
        self.tiles.set_visible(not bool(layout["hero"]))
