"""glue-cs2-warmup: small window that prepares CS2 shaders for an NVIDIA card, once."""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from . import cs2_game, cs2_warmup
from .strings import RO

ERRORS = {
    "cs2-running": "CS2 is already running. Close it and try again.",
    "no-start": "Steam did not start CS2. Open Steam, sign in, and try again.",
    "no-load": "CS2 started but did not load the map. Try again later.",
    "exited": "CS2 closed before it was ready. Try again later.",
    "launch-options": "Your CS2 launch options join a server. Remove +connect from them "
                      "in Steam, then try again.",
    "proton": "CS2 is set to run with Proton. In Steam, open CS2 Properties, Compatibility, "
              "untick the forced tool, then try again.",
    "steam-busy": "Steam still shows CS2 as running. Wait a moment, then try again.",
}
INTRO = ("CS2 will open by itself and load each map with bots, while your graphics card "
         "prepares its shaders. Do not touch it; it closes when done. Matches after this "
         "start without the stutter of the first minutes. Takes about {minutes} minutes.")
# measured per map on the laptop: load + compile + restart
MINUTES_PER_MAP = 3
# not resizable: GTK sends min == max size, which gluewc and gluemac float
WINDOW = {"resizable": False, "default_width": 460, "width_request": 460}
TEXT_CHARS = 50


class Awake:
    """Idle inhibit while CS2 runs unattended; GTK turns it into Wayland idle-inhibit,
    which the shells' IdleMonitor (respectInhibitors) waits for."""

    def __init__(self, app, flags):
        self.app, self.flags, self.cookie = app, flags, 0

    def hold(self, window, reason: str) -> None:
        if not self.cookie:
            self.cookie = self.app.inhibit(window, self.flags, reason)

    def release(self) -> None:
        if self.cookie:
            self.app.uninhibit(self.cookie)
            self.cookie = 0


def check(home: Path, everything: bool) -> int:
    found = cs2_game.pending(home, everything=everything)
    if not found:
        print("nothing to do")
        return 1
    print(f"driver {found[2]}, game {found[1]}")
    print("maps: " + " ".join(found[3]))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="glue-cs2-warmup")
    parser.add_argument("--offer", action="store_true",
                        help="at login: ask once, exit at once when not needed")
    parser.add_argument("--all", action="store_true", help="every installed map")
    parser.add_argument("--check", action="store_true", help="print what would run")
    parser.add_argument("--maps", help="comma-separated subset, e.g. de_dust2,de_mirage")
    parser.add_argument("--text", action="store_true", help="no window, progress on stdout")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    home = Path.home()
    if args.check:
        return check(home, args.all)
    if args.offer:
        state = cs2_game.State(home / cs2_game.STATE_FILE)
        if state.declined or not cs2_game.needs_warmup(home) or cs2_warmup.game_pids():
            return 0
    found = cs2_game.pending(home, everything=args.all)
    if not found:
        return 0 if args.offer else check(home, args.all)
    if args.maps:
        only = args.maps.split(",")
        found = (*found[:3], [m for m in found[3] if m in only])
        if not found[3]:
            return check(home, args.all)
    steam = cs2_warmup.steam_command(found[0])
    if steam is None:
        print("glue-cs2-warmup: steam is not installed", file=sys.stderr)
        return 1
    if args.text:
        return run_text(found, steam)
    return run_window(found, steam, args.offer)


def run_text(found, steam: list[str]) -> int:
    root, game, driver, maps = found
    state = cs2_game.State(Path.home() / cs2_game.STATE_FILE)

    def report(kind, **info):
        if kind == "compiling":
            print(f"{info['name']} {info['seconds']:6.0f}s {info['cores']:5.2f} cores", flush=True)
        else:
            print(kind, " ".join(f"{k}={v}" for k, v in info.items()), flush=True)

    try:
        cs2_warmup.Runner(root, game, driver, maps, state, steam).run(report)
    except (cs2_warmup.WarmupError, cs2_warmup.Cancelled) as exc:
        print(f"glue-cs2-warmup: {ERRORS.get(str(exc), 'stopped')}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


def run_window(found, steam: list[str], offer: bool) -> int:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib, Gtk
    from glue_apps import i18n
    from glue_apps.app import load_config
    from glue_apps.i18n import _, set_language
    from glue_apps.shell import title_buttons

    i18n.RO.update(RO)
    set_language(load_config().language)
    root, game, driver, maps = found
    home = Path.home()

    class Window(Adw.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title=_("Prepare CS2"), **WINDOW)
            self.awake = Awake(app, Gtk.ApplicationInhibitFlags.IDLE
                               | Gtk.ApplicationInhibitFlags.SUSPEND)
            self.cancel = threading.Event()
            self.running = False
            header = Adw.HeaderBar()
            layout = title_buttons()
            if layout is not None:
                header.set_decoration_layout(layout)
            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                           margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
            self.title = Gtk.Label(label=_("Prepare CS2 for your graphics card"), xalign=0,
                                   wrap=True, max_width_chars=TEXT_CHARS,
                                   css_classes=["title-3"])
            self.detail = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"],
                                    max_width_chars=TEXT_CHARS,
                                    label=_(INTRO, minutes=len(maps) * MINUTES_PER_MAP))
            self.bar = Gtk.ProgressBar(visible=False, show_text=True)
            self.buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
            self.start = Gtk.Button(label=_("Start"), css_classes=["pill", "suggested-action"])
            self.start.connect("clicked", lambda _b: self.begin())
            self.later = Gtk.Button(label=_("Not now"), css_classes=["pill"])
            self.later.connect("clicked", lambda _b: self.close())
            self.never = Gtk.Button(label=_("Don't ask again"), css_classes=["pill", "flat"],
                                    visible=offer)
            self.never.connect("clicked", lambda _b: self.decline())
            for widget in (self.never, self.later, self.start):
                self.buttons.append(widget)
            for widget in (self.title, self.detail, self.bar, self.buttons):
                body.append(widget)
            view = Adw.ToolbarView(content=body)
            view.add_top_bar(header)
            self.set_content(view)
            self.connect("close-request", self.on_close)

        def decline(self):
            cs2_game.State(home / cs2_game.STATE_FILE).decline()
            self.close()

        def begin(self):
            self.running = True
            self.index = 0
            self.awake.hold(self, _("Preparing CS2 shaders"))
            self.title.set_text(_("Preparing CS2 for your graphics card (one time only)"))
            self.detail.set_text(_("Starting CS2 through Steam…"))
            self.bar.set_visible(True)
            self.start.set_visible(False)
            self.never.set_visible(False)
            self.later.set_label(_("Cancel"))
            state = cs2_game.State(home / cs2_game.STATE_FILE)
            runner = cs2_warmup.Runner(root, game, driver, maps, state, steam,
                                       cancel=self.cancel)
            threading.Thread(target=self.work, args=(runner,), daemon=True).start()

        def work(self, runner):
            def report(kind, **info):
                GLib.idle_add(self.progress, kind, info)
            try:
                runner.run(report)
            except cs2_warmup.Cancelled:
                GLib.idle_add(self.finish, _("Stopped. The maps done so far stay ready."))
            except cs2_warmup.WarmupError as exc:
                GLib.idle_add(self.finish, _(ERRORS.get(str(exc), str(exc))))
            except OSError as exc:
                GLib.idle_add(self.finish, str(exc))

        def progress(self, kind, info):
            total = len(maps)
            if kind == "map":
                self.index = info["index"]
                self.bar.set_fraction(self.index / total)
                self.bar.set_text(_("Map {n} of {total}", n=self.index + 1, total=total))
                self.detail.set_text(_("Opening {name}…", name=info["name"]))
            elif kind == "loading":
                self.detail.set_text(_("Loading {name}…", name=info["name"]))
            elif kind == "compiling":
                part = min(info["seconds"] / 240.0, 0.9)
                self.bar.set_fraction((self.index + part) / total)
                self.detail.set_text(_("{name}: preparing shaders", name=info["name"]))
            elif kind == "finished":
                self.finish(_("Done. CS2 is ready for your graphics card."), True)
            return False

        def finish(self, text, ok=False):
            self.running = False
            self.awake.release()
            self.title.set_text(_("CS2 is ready") if ok else _("Prepare CS2"))
            self.detail.set_text(text)
            if ok:
                self.bar.set_fraction(1.0)
            self.later.set_label(_("Close"))
            self.later.set_sensitive(True)
            return False

        def on_close(self, _window):
            if self.running:
                self.cancel.set()
                self.detail.set_text(_("Stopping CS2…"))
                self.later.set_sensitive(False)
                return True
            return False

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.glue.CS2Warmup",
                             flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

        def do_activate(self):
            window = self.props.active_window or Window(self)
            window.present()

    return App().run(None)


if __name__ == "__main__":
    raise SystemExit(main())
