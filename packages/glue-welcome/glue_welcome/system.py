"""System page: hardware summary, power mode, terminal font."""

from __future__ import annotations

import subprocess

from gi.repository import Gtk

from glue_apps.i18n import _
from glue_apps.pages import panel, panel_row
from glue_apps.views import page_heading
from glue_apps.widgets import section_header

from .info import system_summary


def _spawn(argv: list[str]) -> None:
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def _power_switch(current: str) -> Gtk.Widget:
    box = Gtk.Box(css_classes=["linked", "segmented"], valign=Gtk.Align.CENTER)
    group = None
    for profile, text in (("power-saver", _("Power saver")), ("balanced", _("Balanced")),
                          ("performance", _("Performance"))):
        button = Gtk.ToggleButton(label=text, group=group, active=profile == current)
        group = group or button
        button.connect("toggled", lambda b, p=profile: b.get_active() and _spawn(
            ["powerprofilesctl", "set", p]))
        box.append(button)
    return box


def system_page(win) -> Gtk.Widget:
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
    page.append(page_heading(_("System")))
    info = system_summary()
    page.append(section_header(_("Your computer")))
    page.append(panel(*(panel_row(_(key), value) for key, value in info.items()
                        if key != "Power mode")))
    page.append(section_header(_("Tools")))
    font = Gtk.Box(css_classes=["linked"], valign=Gtk.Align.CENTER)
    for text, step in ((_("Smaller"), "-1"), (_("Larger"), "+1")):
        button = Gtk.Button(label=text)
        button.connect("clicked", lambda _b, s=step: _spawn(["termfont", "alacritty", s]))
        font.append(button)
    terminal = Gtk.Button(label=_("Open terminal"), valign=Gtk.Align.CENTER)
    terminal.connect("clicked", lambda _b: _spawn(["alacritty"]))
    page.append(panel(
        panel_row(_("Power mode"), _power_switch(info.get("Power mode", ""))),
        panel_row(_("Terminal font"), font),
        panel_row(_("Open terminal"), terminal)))
    return page
