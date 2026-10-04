"""Settings panel that changes the ReGreet background through the root helper."""

from __future__ import annotations

from pathlib import Path

from gi.repository import Gio, GLib, Gtk

from glue_apps.i18n import _
from glue_apps.pages import panel, panel_row
from glue_apps.widgets import label

from . import greeter


def _current() -> str:
    try:
        return greeter.current_background(greeter.CONFIG.read_text(encoding="utf-8"))
    except OSError:
        return greeter.DEFAULT


def login_screen_panel(win) -> Gtk.Widget:
    reason = greeter.availability()
    preview = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True,
                          halign=Gtk.Align.START, margin_start=22, margin_bottom=16,
                          css_classes=["login-preview"])
    preview.set_size_request(320, 180)
    preview.set_overflow(Gtk.Overflow.HIDDEN)
    status = label("", "row-subtitle", wrap=True)
    status.set_margin_start(22)
    status.set_margin_bottom(16)
    status.set_visible(False)
    status.connect("notify::label", lambda w, _p: w.set_visible(bool(w.get_label())))

    def show(path: str) -> None:
        preview.set_filename(path if Path(path).is_file() else None)

    def run(args: list[str], data: bytes | None = None) -> None:
        flags = Gio.SubprocessFlags.STDERR_PIPE | Gio.SubprocessFlags.STDOUT_PIPE
        if data is not None:
            flags |= Gio.SubprocessFlags.STDIN_PIPE
        try:
            proc = Gio.Subprocess.new(["pkexec", greeter.HELPER, *args], flags)
        except GLib.Error as error:
            status.set_text(_("Not changed: ") + error.message)
            return
        payload = GLib.Bytes.new(data) if data is not None else None
        proc.communicate_async(payload, None, finished)

    def finished(proc: Gio.Subprocess, result) -> None:
        try:
            proc.communicate_finish(result)
        except GLib.Error as error:
            status.set_text(_("Not changed: ") + error.message)
            return
        if proc.get_successful():
            status.set_text(_("Saved. You will see it at the next login."))
            show(_current())
        elif proc.get_exit_status() in (126, 127):
            status.set_text(_("Not changed: authorization was cancelled."))
        else:
            status.set_text(_("Not changed: the file is not a PNG, JPEG or WebP "
                              "image under 30 MB."))

    def loaded(file: Gio.File, result) -> None:
        try:
            ok, data, _etag = file.load_contents_finish(result)
        except GLib.Error as error:
            status.set_text(_("Not changed: ") + error.message)
            return
        if len(data) > greeter.MAX_BYTES or greeter.image_type(data[:16]) is None:
            status.set_text(_("Not changed: the file is not a PNG, JPEG or WebP "
                              "image under 30 MB."))
            return
        run(["set"], data)

    def choose(_button) -> None:
        images = Gtk.FileFilter(name=_("Images"))
        for mime in ("image/png", "image/jpeg", "image/webp"):
            images.add_mime_type(mime)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(images)
        dialog = Gtk.FileDialog(title=_("Choose a picture"), filters=filters,
                                default_filter=images)

        def picked(d: Gtk.FileDialog, result) -> None:
            try:
                file = d.open_finish(result)
            except GLib.Error:
                return
            if file:
                file.load_contents_async(None, loaded)

        dialog.open(win, None, picked)

    choose_button = Gtk.Button(label=_("Choose a picture…"), valign=Gtk.Align.CENTER,
                               css_classes=["pill", "suggested-action"])
    choose_button.connect("clicked", choose)
    default_button = Gtk.Button(label=_("Glue wallpaper"), valign=Gtk.Align.CENTER,
                                css_classes=["pill"])
    default_button.connect("clicked", lambda _b: run(["clear"]))
    buttons = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
    buttons.append(default_button)
    buttons.append(choose_button)

    show(_current())
    if reason:
        for button in (choose_button, default_button):
            button.set_sensitive(False)
        status.set_text(_(reason))

    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    body.append(panel_row(_("Login screen picture"), buttons,
                          _("Shown behind the login card.")))
    body.append(preview)
    body.append(status)
    return panel(body)
