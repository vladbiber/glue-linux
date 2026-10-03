"""Reusable GTK widgets for application results."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

from gi.repository import Gdk, GLib, Gtk, Pango

from .media import cached_image
from .model import App


SOURCE_LABELS = {"repo": "Glue", "flatpak": "Flathub", "aur": "Comunitate"}


def _finish_icon(image: Gtk.Image, future: concurrent.futures.Future) -> bool:
    try:
        path = future.result()
    except Exception:
        return False
    if path:
        _set_local_icon(image, path)
    return False


def _set_local_icon(image: Gtk.Image, path: Path | str) -> None:
    try:
        image.set_from_paintable(Gdk.Texture.new_from_filename(str(path)))
    except GLib.Error:
        image.set_from_icon_name("application-x-executable-symbolic")


def app_result_row(
    app: App,
    pool: concurrent.futures.Executor,
    image_cache: Path,
) -> Gtk.ListBoxRow:
    """Build a result row and fill a remote icon without blocking the UI."""
    row = Gtk.ListBoxRow()
    row.app_model = app
    content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    content.set_margin_top(8); content.set_margin_bottom(8)
    content.set_margin_start(10); content.set_margin_end(10)

    icon = Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
    icon.set_pixel_size(48)
    if app.icon.startswith("/") and Path(app.icon).is_file():
        _set_local_icon(icon, app.icon)
    elif app.icon.startswith("https://"):
        future = pool.submit(cached_image, app.icon, image_cache)
        future.add_done_callback(
            lambda done: GLib.idle_add(_finish_icon, icon, done))
    elif app.icon:
        icon.set_from_icon_name(app.icon)
    content.append(icon)

    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    text.set_hexpand(True)
    name = Gtk.Label(label=app.name, xalign=0)
    name.add_css_class("heading")
    summary = Gtk.Label(label=app.summary or "Fără descriere.", xalign=0,
                        wrap=True, ellipsize=Pango.EllipsizeMode.END, lines=2)
    summary.add_css_class("dim-label")
    sources = Gtk.Label(
        label=" · ".join(SOURCE_LABELS[source.kind] for source in app.sources),
        xalign=0)
    sources.add_css_class("source-badge")
    text.append(name); text.append(summary); text.append(sources)
    content.append(text)
    row.set_child(content)
    return row
