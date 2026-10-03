"""Small secondary GTK pages kept separate from the store workflow."""

from __future__ import annotations

import subprocess
import json
from pathlib import Path

from gi.repository import GLib, Gtk

from .system import system_summary
from .keybindings import binding_rows, load_shell_choices


def base_page(title: str, subtitle: str = "") -> Gtk.Box:
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    page.set_margin_top(28); page.set_margin_bottom(28)
    page.set_margin_start(28); page.set_margin_end(28)
    heading = Gtk.Label(label=title, xalign=0)
    heading.add_css_class("title-1")
    page.append(heading)
    if subtitle:
        text = Gtk.Label(label=subtitle, xalign=0, wrap=True)
        text.add_css_class("dim-label")
        page.append(text)
    return page


def home_page(open_apps, disable_autostart) -> tuple[Gtk.Widget, Gtk.Widget, Gtk.Widget]:
    page = base_page(
        "Bine ai venit", "Aplicații, actualizări și setările importante ale sistemului.")
    hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    hero.add_css_class("hero")
    title = Gtk.Label(label="Glue Linux", xalign=0)
    title.add_css_class("title-1")
    hero.append(title)
    hero.append(Gtk.Label(
        label="Caută și instalează aplicații din depozitul Glue, Flathub și AUR.",
        xalign=0, wrap=True))
    button = Gtk.Button(label="Găsește aplicații", halign=Gtk.Align.START)
    button.add_css_class("suggested-action")
    button.connect("clicked", lambda _button: open_apps())
    hero.append(button)
    hide = Gtk.Button(label="Nu mai arăta la pornire", halign=Gtk.Align.START)
    hide.connect("clicked", lambda button: (disable_autostart(), button.set_visible(False)))
    hero.append(hide)
    page.append(hero)
    tiles = Gtk.Grid(column_spacing=10, row_spacing=10)
    for index, label in enumerate(("Internet", "Media", "Jocuri", "Utilitare")):
        tile = Gtk.Button(label=label, hexpand=True, vexpand=True)
        tile.add_css_class("tile-button")
        tile.set_size_request(180, 90)
        tile.connect("clicked", lambda _button: open_apps())
        tiles.attach(tile, index % 2, index // 2, 1, 1)
    tiles.set_visible(False)
    page.append(tiles)
    return page, hero, tiles


def keys_page() -> Gtk.Widget:
    page = base_page("Tastele mele", "Scurtăturile sesiunii curente.")
    catalog_path = Path("/usr/share/glue/catalog.json")
    if not catalog_path.exists():
        catalog_path = Path("/usr/share/glue-installer/catalog/catalog.json")
    desktop = GLib.getenv("XDG_CURRENT_DESKTOP") or "gluewc"
    try:
        data = json.loads(catalog_path.read_text())
        choices = load_shell_choices(Path("/etc/glue/session.conf"))
        session_id, rows = binding_rows(data, desktop, choices)
        for keys, action in rows:
            page.append(Gtk.Label(label=f"{keys}    {action}", xalign=0))
    except (OSError, ValueError, KeyError, IndexError):
        session_id = desktop.casefold().split(":", 1)[0]
        page.append(Gtk.Label(label="Catalogul de taste nu este disponibil.", xalign=0))
    config = Path.home() / ".config" / session_id
    button = Gtk.Button(label="Deschide configul", halign=Gtk.Align.START)
    button.connect("clicked", lambda _button: subprocess.Popen(["xdg-open", str(config)]))
    page.append(button)
    return page


def _command_button(label: str, argv: list[str]) -> Gtk.Button:
    button = Gtk.Button(label=label, halign=Gtk.Align.START)
    button.connect("clicked", lambda _button: subprocess.Popen(argv))
    return button


def system_page() -> Gtk.Widget:
    page = base_page("Sistem")
    for label, value in system_summary().items():
        page.append(Gtk.Label(label=f"{label}: {value}", xalign=0))
    commands = (
        ("Font +", ["termfont", "alacritty", "+1"]),
        ("Font −", ["termfont", "alacritty", "-1"]),
        ("Mod performanță", ["powerprofilesctl", "set", "performance"]),
        ("Mod echilibrat", ["powerprofilesctl", "set", "balanced"]),
        ("Mod economie", ["powerprofilesctl", "set", "power-saver"]),
        ("Deschide terminalul", ["alacritty"]),
    )
    for label, argv in commands:
        page.append(_command_button(label, argv))
    return page
