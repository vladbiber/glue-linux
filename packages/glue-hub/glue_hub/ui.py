"""GTK4/libadwaita interface. Business logic stays in the pure modules."""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .catalog import CatalogIndex, load_appstream, system_appstream_paths
from .commands import action_plan, run_commands, update_plan
from .config import HubConfig, THEMES
from .curation import CATEGORIES, RECOMMENDED
from .media import cached_image
from .model import App, AppSource
from .pages import base_page, home_page, keys_page, system_page
from .remote import SearchCache, aur_details, aur_search, flathub_details, flathub_search
from .system import update_counts


PAGE_NAMES = {
    "home": "Acasă", "apps": "Aplicații", "updates": "Actualizări",
    "keys": "Tastele mele", "system": "Sistem", "settings": "Setări",
}

def _installed(argv: list[str]) -> set[str]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
        return set(done.stdout.split()) if done.returncode == 0 else set()
    except (OSError, subprocess.TimeoutExpired):
        return set()


class HubWindow(Adw.ApplicationWindow):
    def __init__(self, app, config: HubConfig, config_path: Path, data_root: Path):
        super().__init__(application=app, title="Glue Hub", default_width=1120,
                         default_height=720)
        self.config = config
        self.config_path = config_path
        self.data_root = data_root
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        self.cache = SearchCache(
            Path(GLib.get_user_cache_dir()) / "glue-hub" / "search.json")
        self.image_cache = Path(GLib.get_user_cache_dir()) / "glue-hub" / "images"
        self.index = CatalogIndex()
        self.native_installed = _installed(["pacman", "-Qq"])
        self.aur_installed = _installed(["pacman", "-Qqm"])
        self.flatpak_installed = _installed(
            ["flatpak", "list", "--user", "--app", "--columns=application"])
        self.current_app: App | None = None
        self.current_sources: tuple[AppSource, ...] = ()
        self.css_provider = Gtk.CssProvider()
        self._build()
        self._apply_theme(config.theme)
        self._load_local_catalog()

    def _build(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        title = Adw.WindowTitle(title="Glue Hub", subtitle="Tot ce ai nevoie, într-un singur loc")
        header.set_title_widget(title)
        root.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.add_css_class("hub-body")
        sidebar = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        sidebar.add_css_class("navigation-sidebar")
        sidebar.set_size_request(190, -1)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        for key, label in PAGE_NAMES.items():
            row = Gtk.ListBoxRow()
            row.page_key = key
            text = Gtk.Label(label=label, xalign=0)
            text.set_margin_top(10); text.set_margin_bottom(10)
            text.set_margin_start(14); text.set_margin_end(14)
            row.set_child(text)
            sidebar.append(row)
        sidebar.connect("row-selected", self._page_selected)
        body.append(sidebar)

        self.stack.add_named(home_page(
            lambda: self.stack.set_visible_child_name("apps")), "home")
        self.stack.add_named(self._apps_page(), "apps")
        self.stack.add_named(self._updates_page(), "updates")
        self.stack.add_named(keys_page(), "keys")
        self.stack.add_named(system_page(), "system")
        self.stack.add_named(self._settings_page(), "settings")
        body.append(self.stack)
        root.append(body)
        self.set_content(root)
        start = os.environ.get("GLUE_HUB_START_PAGE", "home")
        index = list(PAGE_NAMES).index(start) if start in PAGE_NAMES else 0
        sidebar.select_row(sidebar.get_row_at_index(index))

    def _page_selected(self, _box, row) -> None:
        if row:
            self.stack.set_visible_child_name(row.page_key)

    @staticmethod
    def _page(title: str, subtitle: str = "") -> Gtk.Box:
        return base_page(title, subtitle)

    def _apps_page(self) -> Gtk.Widget:
        page = self._page("Aplicații", "O singură căutare pentru toate sursele disponibile.")
        if not self.config.store:
            page.append(Gtk.Label(label="Magazinul nu a fost instalat pe acest sistem.", xalign=0))
            return page
        controls = Gtk.Box(spacing=8)
        self.search = Gtk.SearchEntry(placeholder_text="Caută VLC, Discord, editor foto…")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", self._search_changed)
        self.source_filter = Gtk.DropDown.new_from_strings(
            ["Toate sursele", "Depozitul Glue", "Flathub", "AUR"])
        self.source_filter.connect("notify::selected", self._search_changed)
        self.category_filter = Gtk.DropDown.new_from_strings(list(CATEGORIES))
        self.category_filter.connect("notify::selected", self._search_changed)
        controls.append(self.search); controls.append(self.source_filter)
        controls.append(self.category_filter)
        page.append(controls)

        self.results_title = Gtk.Label(label="Recomandate", xalign=0)
        self.results_title.add_css_class("heading")
        page.append(self.results_title)

        panes = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=510)
        self.results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.results.connect("row-activated", self._result_activated)
        scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroll.set_child(self.results)
        panes.set_start_child(scroll)
        self.detail = self._detail_panel()
        panes.set_end_child(self.detail)
        panes.set_vexpand(True)
        page.append(panes)
        return page

    def _detail_panel(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(22); box.set_margin_top(12)
        self.detail_name = Gtk.Label(label="Alege o aplicație", xalign=0, wrap=True)
        self.detail_name.add_css_class("title-2")
        self.detail_icon = Gtk.Image(pixel_size=64, halign=Gtk.Align.START)
        self.detail_summary = Gtk.Label(label="", xalign=0, wrap=True)
        self.detail_meta = Gtk.Label(label="", xalign=0, wrap=True)
        self.detail_meta.add_css_class("dim-label")
        self.gallery = Gtk.Box(spacing=8)
        gallery_scroll = Gtk.ScrolledWindow(hexpand=True, min_content_height=150)
        gallery_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        gallery_scroll.set_child(self.gallery)
        self.detail_source = Gtk.DropDown.new_from_strings(["Sursă"])
        self.detail_source.connect("notify::selected", self._source_changed)
        self.detail_action = Gtk.Button(label="Instalează")
        self.detail_action.add_css_class("suggested-action")
        self.detail_action.connect("clicked", self._action_clicked)
        self.detail_open = Gtk.Button(label="Deschide")
        self.detail_open.connect("clicked", self._open_clicked)
        self.detail_status = Gtk.Label(label="", xalign=0, wrap=True)
        self.action_progress = Gtk.ProgressBar(show_text=True)
        self.action_progress.set_visible(False)
        self.action_log = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        log_scroll = Gtk.ScrolledWindow(min_content_height=100)
        log_scroll.set_child(self.action_log)
        log_details = Gtk.Expander(label="Detalii operație")
        log_details.set_child(log_scroll)
        for child in (self.detail_icon, self.detail_name, self.detail_summary,
                      self.detail_meta, gallery_scroll, self.detail_source,
                      self.detail_action, self.detail_open, self.detail_status,
                      self.action_progress, log_details):
            box.append(child)
        self.detail_source.set_visible(False)
        gallery_scroll.set_visible(False)
        self.gallery_scroll = gallery_scroll
        self.detail_action.set_visible(False)
        self.detail_open.set_visible(False)
        return box

    def _load_local_catalog(self) -> None:
        paths = system_appstream_paths()
        self.index.extend(load_appstream(paths, self.native_installed))
        self._render_results(self._visible_apps())

    def _source_key(self) -> str:
        return ("all", "repo", "flatpak", "aur")[self.source_filter.get_selected()]

    def _search_changed(self, *_args) -> None:
        query = self.search.get_text().strip()
        self.results_title.set_text("Rezultate" if query else "Recomandate")
        self._render_results(self._visible_apps())
        if len(query) < 2:
            return
        future = self.pool.submit(self._remote_search, query)
        future.add_done_callback(lambda f: GLib.idle_add(self._merge_remote, query, f))

    def _remote_search(self, query: str) -> list[App]:
        source = self._source_key()
        apps: list[App] = []
        if source in {"all", "flatpak"}:
            apps.extend(flathub_search(query, self.cache, self.flatpak_installed))
        if source in {"all", "aur"}:
            apps.extend(aur_search(query, self.cache, self.aur_installed))
        return apps

    def _merge_remote(self, query: str, future) -> bool:
        try:
            self.index.extend(future.result())
        except Exception as exc:
            self.detail_status.set_text(f"Căutarea online a eșuat: {exc}")
            return False
        if self.search.get_text().strip() == query:
            self._render_results(self._visible_apps())
        return False

    def _visible_apps(self) -> list[App]:
        query = self.search.get_text().strip()
        source = self._source_key()
        apps = self.index.search(query, source, limit=120)
        category = list(CATEGORIES)[self.category_filter.get_selected()]
        tags = CATEGORIES[category]
        if tags:
            apps = [app for app in apps if set(app.categories) & set(tags)]
        if not query and not tags:
            featured = [app for app in self.index.featured(RECOMMENDED)
                        if source == "all" or any(s.kind == source for s in app.sources)]
            apps = featured + [app for app in apps if app not in featured]
        return apps[:80]

    def _render_results(self, apps: list[App]) -> None:
        while row := self.results.get_row_at_index(0):
            self.results.remove(row)
        for app in apps:
            row = Gtk.ListBoxRow()
            row.app_model = app
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            box.set_margin_top(8); box.set_margin_bottom(8)
            box.set_margin_start(10); box.set_margin_end(10)
            name = Gtk.Label(label=app.name, xalign=0)
            name.add_css_class("heading")
            kinds = " · ".join(source.kind.upper() for source in app.sources)
            summary = Gtk.Label(label=f"{app.summary}\n{kinds}", xalign=0, wrap=True)
            summary.add_css_class("dim-label")
            box.append(name); box.append(summary)
            row.set_child(box)
            self.results.append(row)

    def _result_activated(self, _list, row) -> None:
        self.current_app = row.app_model
        self.current_sources = self.current_app.sources
        labels = [{"repo": "Din depozitul Glue", "flatpak": "De pe Flathub",
                   "aur": "Din AUR"}[source.kind] for source in self.current_sources]
        self.detail_source.set_model(Gtk.StringList.new(labels))
        self.detail_source.set_selected(0)
        self.detail_source.set_visible(True)
        self.detail_name.set_text(self.current_app.name)
        self.detail_summary.set_text(
            self.current_app.description or self.current_app.summary or "Fără descriere.")
        self.detail_meta.set_text(" · ".join(filter(None, (
            self.current_app.developer, self.current_app.license,
            ", ".join(self.current_app.categories[:3])))))
        self._show_media(self.current_app)
        self._source_changed()
        if any(source.kind == "flatpak" for source in self.current_sources):
            future = self.pool.submit(
                flathub_details,
                next(source.ref for source in self.current_sources if source.kind == "flatpak"),
                self.cache, self.flatpak_installed)
            future.add_done_callback(lambda f: GLib.idle_add(self._details_loaded, f))
        if any(source.kind == "aur" for source in self.current_sources):
            ref = next(source.ref for source in self.current_sources if source.kind == "aur")
            future = self.pool.submit(aur_details, ref, self.cache, self.aur_installed)
            future.add_done_callback(lambda f: GLib.idle_add(self._details_loaded, f))

    def _details_loaded(self, future) -> bool:
        detail = future.result()
        if detail and self.current_app:
            self.index.extend([detail])
            match = self.index.search(self.current_app.name)
            self.current_app = next((app for app in match if app.name == self.current_app.name),
                                    self.current_app)
            self.detail_summary.set_text(
                self.current_app.description or self.current_app.summary)
            self.detail_meta.set_text(" · ".join(filter(None, (
                self.current_app.developer, self.current_app.license,
                ", ".join(self.current_app.categories[:3])))))
            self._show_media(self.current_app)
        return False

    def _clear_box(self, box: Gtk.Box) -> None:
        while child := box.get_first_child():
            box.remove(child)

    def _show_media(self, app: App) -> None:
        self._clear_box(self.gallery)
        self.gallery_scroll.set_visible(bool(app.screenshots))
        if app.icon.startswith("/"):
            self.detail_icon.set_from_file(app.icon)
        elif app.icon.startswith("https://"):
            future = self.pool.submit(cached_image, app.icon, self.image_cache)
            future.add_done_callback(lambda f: GLib.idle_add(self._set_icon, f.result()))
        for url in app.screenshots[:3]:
            if url.startswith("https://"):
                future = self.pool.submit(cached_image, url, self.image_cache)
                future.add_done_callback(lambda f: GLib.idle_add(self._add_screenshot, f.result()))

    def _set_icon(self, path: Path | None) -> bool:
        if path:
            self.detail_icon.set_from_file(str(path))
        return False

    def _add_screenshot(self, path: Path | None) -> bool:
        if path:
            picture = Gtk.Picture.new_for_filename(str(path))
            picture.set_content_fit(Gtk.ContentFit.COVER)
            picture.set_size_request(240, 135)
            self.gallery.append(picture)
        return False

    def _source_changed(self, *_args) -> None:
        if not self.current_sources:
            return
        source = self.current_sources[min(self.detail_source.get_selected(),
                                          len(self.current_sources) - 1)]
        self.detail_action.set_label("Dezinstalează" if source.installed else "Instalează")
        self.detail_action.set_visible(True)
        self.detail_open.set_visible(source.installed)

    def _selected_source(self) -> AppSource | None:
        if not self.current_sources:
            return None
        return self.current_sources[min(self.detail_source.get_selected(),
                                        len(self.current_sources) - 1)]

    def _action_clicked(self, _button) -> None:
        source = self._selected_source()
        if not source:
            return
        action = "remove" if source.installed else "install"
        if source.kind == "aur" and action == "install" and not self.config.aur_warning_seen:
            dialog = Adw.MessageDialog.new(
                self, "Aplicație comunitară",
                "AUR conține rețete scrise de comunitate. Glue Hub le construiește ca utilizator, "
                "dar trebuie să verifici sursa dacă aplicația este sensibilă. Unele rețete care cer "
                "systemd nu funcționează pe Glue Linux.")
            dialog.add_response("cancel", "Renunță")
            dialog.add_response("continue", "Înțeleg, continuă")
            dialog.set_response_appearance("continue", Adw.ResponseAppearance.SUGGESTED)
            dialog.connect("response", lambda _d, response: self._aur_confirmed(source)
                           if response == "continue" else None)
            dialog.present()
            return
        self._run_action(source, action)

    def _aur_confirmed(self, source: AppSource) -> None:
        self.config.aur_warning_seen = True
        self.config.save(self.config_path)
        self._run_action(source, "install")

    def _run_action(self, source: AppSource, action: str) -> None:
        self.detail_action.set_sensitive(False)
        self.detail_status.set_text("Se lucrează…")
        self.action_log.get_buffer().set_text("")
        self.action_progress.set_visible(True)
        self.action_progress.set_text("Se lucrează…")
        self.action_pulse = GLib.timeout_add(120, self._pulse_action)
        callback = lambda text: GLib.idle_add(self._append_action_log, text)
        future = self.pool.submit(
            run_commands, action_plan(source.kind, action, source.ref), callback)
        future.add_done_callback(lambda f: GLib.idle_add(self._action_done, f))

    def _pulse_action(self) -> bool:
        self.action_progress.pulse()
        return True

    def _append_action_log(self, text: str) -> bool:
        self.action_log.get_buffer().insert_at_cursor(text)
        return False

    def _action_done(self, future) -> bool:
        code = future.result()
        GLib.source_remove(self.action_pulse)
        self.action_progress.set_fraction(1 if code == 0 else 0)
        self.action_progress.set_text("Gata" if code == 0 else "A eșuat")
        self.detail_status.set_text("Gata." if code == 0 else f"Operația a eșuat (cod {code}).")
        self.detail_action.set_sensitive(True)
        return False

    def _open_clicked(self, _button) -> None:
        source = self._selected_source()
        if source:
            ref = source.ref
            if source.kind == "repo" and self.current_app:
                ref = self.current_app.app_id.removesuffix(".desktop")
            self.pool.submit(run_commands, action_plan(source.kind, "open", ref))

    def _updates_page(self) -> Gtk.Widget:
        page = self._page("Actualizări", "Verificarea rulează numai când deschizi Hub-ul sau apeși butonul.")
        self.update_label = Gtk.Label(label="Apasă Verifică pentru a vedea actualizările.", xalign=0)
        buttons = Gtk.Box(spacing=8)
        check = Gtk.Button(label="Verifică")
        check.connect("clicked", self._check_updates)
        install = Gtk.Button(label="Actualizează tot")
        install.add_css_class("suggested-action")
        install.connect("clicked", self._install_updates)
        buttons.append(check); buttons.append(install)
        page.append(self.update_label); page.append(buttons)
        page.append(Gtk.Label(
            label="Steam se actualizează singur când îl deschizi.", xalign=0,
            css_classes=["dim-label"]))
        self.update_log = Gtk.TextView(editable=False, monospace=True)
        log_scroll = Gtk.ScrolledWindow(min_content_height=160)
        log_scroll.set_child(self.update_log)
        details = Gtk.Expander(label="Jurnal actualizare")
        details.set_child(log_scroll)
        page.append(details)
        return page

    def _check_updates(self, _button) -> None:
        self.update_label.set_text("Se verifică…")
        future = self.pool.submit(update_counts)
        future.add_done_callback(lambda f: GLib.idle_add(
            self.update_label.set_text,
            f"{f.result()[0]} de sistem, {f.result()[1]} Flathub, {f.result()[2]} AUR."))

    def _install_updates(self, _button) -> None:
        self.update_label.set_text("Se actualizează…")
        self.update_log.get_buffer().set_text("")
        callback = lambda text: GLib.idle_add(
            self.update_log.get_buffer().insert_at_cursor, text)
        future = self.pool.submit(run_commands, update_plan(), callback)
        future.add_done_callback(lambda f: GLib.idle_add(
            self.update_label.set_text, "Gata." if f.result() == 0 else "Actualizarea a eșuat."))

    def _settings_page(self) -> Gtk.Widget:
        page = self._page("Setări")
        page.append(Gtk.Label(label="Aspect", xalign=0, css_classes=["heading"]))
        names = ["Glue", "Vitrină", "Mozaic"]
        themes = Gtk.DropDown.new_from_strings(names)
        themes.set_selected(THEMES.index(self.config.theme))
        themes.connect("notify::selected", self._theme_selected)
        page.append(themes)
        startup = Gtk.CheckButton(label="Arată Glue Hub la pornire", active=self.config.autostart)
        startup.connect("toggled", self._autostart_toggled)
        page.append(startup)
        return page

    def _theme_selected(self, dropdown, _param) -> None:
        self.config.theme = THEMES[dropdown.get_selected()]
        self.config.save(self.config_path)
        self._apply_theme(self.config.theme)

    def _autostart_toggled(self, button) -> None:
        self.config.autostart = button.get_active()
        self.config.save(self.config_path)

    def _apply_theme(self, theme: str) -> None:
        path = self.data_root / "themes" / theme / "theme.css"
        try:
            self.css_provider.load_from_path(str(path))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), self.css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except GLib.Error:
            pass


class HubApplication(Adw.Application):
    def __init__(self, config: HubConfig, config_path: Path, data_root: Path):
        super().__init__(application_id="org.glue.Hub", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.config = config
        self.config_path = config_path
        self.data_root = data_root

    def do_activate(self) -> None:
        window = self.props.active_window
        if not window:
            window = HubWindow(self, self.config, self.config_path, self.data_root)
        window.present()


def run(config: HubConfig, config_path: Path, data_root: Path) -> int:
    return HubApplication(config, config_path, data_root).run(None)
