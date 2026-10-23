"""Keyboard layouts for glue-keyboard: pure logic, no GTK, runner injectable."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

EVDEV_LST = Path("/usr/share/X11/xkb/rules/evdev.lst")
XORG_CONF = Path("/etc/X11/xorg.conf.d/00-keyboard.conf")
VCONSOLE_CONF = Path("/etc/vconsole.conf")
KEYMAPS_DIR = Path("/usr/share/kbd/keymaps")
HELPER = "/usr/lib/glue/keyboard-apply"
MAX_LAYOUTS = 4

SWITCH_OPTIONS = (
    ("grp:alt_shift_toggle", "Alt+Shift"),
    ("grp:win_space_toggle", "Super+Space"),
    ("grp:ctrl_shift_toggle", "Ctrl+Shift"),
    ("grp:caps_toggle", "Caps Lock"),
)
DEFAULT_SWITCH = SWITCH_OPTIONS[0][0]

_SAFE = re.compile(r"^[a-z0-9_:,()-]*$")
_OPTION_LINE = re.compile(r'^\s*Option\s+"(Xkb\w+)"\s+"([^"]*)"', re.IGNORECASE)


@dataclass(frozen=True)
class Layout:
    layout: str
    variant: str = ""
    name: str = ""

    @property
    def key(self) -> str:
        return f"{self.layout}({self.variant})" if self.variant else self.layout


@dataclass
class Config:
    layouts: list[Layout]
    options: list[str]
    model: str = ""

    @property
    def switch(self) -> str:
        return next((o for o in self.options if o.startswith("grp:")), "")


def parse_evdev(text: str) -> list[Layout]:
    """Layouts and variants of an evdev.lst, sorted by human name."""
    out, section = [], ""
    for line in text.splitlines():
        if line.startswith("!"):
            section = line[1:].strip()
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        code, desc = parts[0], parts[1].strip()
        if section == "layout":
            out.append(Layout(code, "", desc))
        elif section == "variant" and ":" in desc:
            base, name = desc.split(":", 1)
            out.append(Layout(base.strip(), code, name.strip()))
    return sorted(out, key=lambda item: item.name.casefold())


def load_catalog(path: Path = EVDEV_LST) -> list[Layout]:
    try:
        return parse_evdev(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return [Layout("us", "", "English (US)")]


def describe(layout: Layout, catalog: list[Layout]) -> str:
    for item in catalog:
        if item.layout == layout.layout and item.variant == layout.variant:
            return item.name
    return layout.key


def search(catalog: list[Layout], query: str, limit: int = 200) -> list[Layout]:
    words = query.casefold().split()
    hits = [item for item in catalog
            if all(w in f"{item.name} {item.key}".casefold() for w in words)]
    return hits[:limit]


def merge_options(options: list[str], switch: str) -> list[str]:
    """Replace the grp: option, keep every other one (ctrl:nocaps, …)."""
    kept = [o for o in options if o and not o.startswith("grp:")]
    return ([switch] if switch else []) + kept


def xkb_strings(config: Config) -> tuple[str, str, str]:
    """layout, variant, options as XKB comma lists (variants kept positional)."""
    layouts = ",".join(item.layout for item in config.layouts)
    variants = ",".join(item.variant for item in config.layouts)
    if not variants.strip(","):
        variants = ""
    options = config.options if len(config.layouts) > 1 else \
        [o for o in config.options if not o.startswith("grp:")]
    return layouts, variants, ",".join(options)


def from_strings(layout: str, variant: str = "", options: str = "",
                 model: str = "") -> Config:
    names = [x.strip() for x in layout.split(",")] if layout.strip() else []
    variants = [x.strip() for x in variant.split(",")] if variant else []
    layouts = [Layout(name, variants[i] if i < len(variants) else "")
               for i, name in enumerate(names) if name]
    return Config(layouts or [Layout("us")],
                  [o.strip() for o in options.split(",") if o.strip()], model)


def parse_xorg(text: str) -> Config:
    values: dict[str, str] = {}
    for line in text.splitlines():
        m = _OPTION_LINE.match(line)
        if m:
            values[m.group(1).lower()] = m.group(2)
    return from_strings(values.get("xkblayout", ""), values.get("xkbvariant", ""),
                        values.get("xkboptions", ""), values.get("xkbmodel", ""))


def render_xorg(layout: str, variant: str, options: str, model: str = "") -> str:
    lines = ['# Written by glue-keyboard.', 'Section "InputClass"',
             '        Identifier "system-keyboard"',
             '        MatchIsKeyboard "on"',
             f'        Option "XkbLayout" "{layout}"']
    if model:
        lines.append(f'        Option "XkbModel" "{model}"')
    if variant:
        lines.append(f'        Option "XkbVariant" "{variant}"')
    if options:
        lines.append(f'        Option "XkbOptions" "{options}"')
    lines.append("EndSection")
    return "\n".join(lines) + "\n"


def current(path: Path = XORG_CONF) -> Config:
    try:
        return parse_xorg(path.read_text(encoding="utf-8"))
    except OSError:
        return Config([Layout("us")], [])


def valid(*values: str) -> bool:
    return all(_SAFE.match(v) for v in values)


def console_keymap(layout: str, variant: str = "",
                   root: Path = KEYMAPS_DIR) -> str:
    """Console keymap name for the first layout, "" when kbd has none."""
    names = ([f"{layout}-{variant}"] if variant else []) + [layout]
    if not root.is_dir():
        return ""
    for name in names:
        for suffix in (".map.gz", ".map"):
            if next(root.rglob(name + suffix), None) is not None:
                return name
    return ""


def set_vconsole(text: str, keymap: str) -> str:
    lines = [l for l in text.splitlines() if not l.startswith("KEYMAP=")]
    lines.insert(0, f"KEYMAP={keymap}")
    return "\n".join(lines) + "\n"


def set_conf_keys(text: str, values: dict[str, str]) -> str:
    """Set key = value lines in a gluewc config, appending missing keys."""
    lines, seen = text.splitlines(), set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in values:
            lines[i] = f"{key} = {values[key]}"
            seen.add(key)
    missing = [k for k in values if k not in seen]
    if missing:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# keyboard (glue-keyboard)")
        lines += [f"{k} = {values[k]}" for k in missing]
    return "\n".join(lines) + "\n"


def detect_session(env=os.environ) -> str:
    """gluewc, gluemac, hyprland, kde, gnome, cinnamon, x11 or wayland."""
    glue = env.get("GLUE_SESSION", "")
    desktop = env.get("XDG_CURRENT_DESKTOP", "").casefold()
    if glue.startswith("gluemac") or "gluemac" in desktop:
        return "gluemac"
    if glue.startswith("gluewc") or "gluewc" in desktop:
        return "gluewc"
    if env.get("HYPRLAND_INSTANCE_SIGNATURE") or "hyprland" in desktop:
        return "hyprland"
    for name in ("kde", "gnome", "cinnamon"):
        if name in desktop:
            return name
    if env.get("WAYLAND_DISPLAY"):
        return "wayland"
    return "x11" if env.get("DISPLAY") else ""


def compositor_config(session: str, env=os.environ) -> Path | None:
    if session not in ("gluewc", "gluemac"):
        return None
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME", "~"), ".config")
    return Path(base) / session / "config.conf"


def write_compositor_config(path: Path, layout: str, variant: str, options: str,
                            seeds=(Path("/usr/share"), Path("/usr/local/share"))) -> None:
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        # seed like the session script, or the compositor skips its defaults
        text = ""
        for root in seeds:
            seed = root / path.parent.name / "config.def.conf"
            if seed.is_file():
                text = seed.read_text(encoding="utf-8")
                break
        path.parent.mkdir(parents=True, exist_ok=True)
    text = set_conf_keys(text, {"xkb_layout": layout, "xkb_variant": variant,
                                "xkb_options": options})
    tmp = path.with_name(path.name + ".glue-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


SETTINGS_APPS = {
    "kde": ["kcmshell6", "kcm_keyboard"],
    "gnome": ["gnome-control-center", "keyboard"],
    "cinnamon": ["cinnamon-settings", "keyboard"],
}


def settings_app(session: str) -> list[str] | None:
    argv = SETTINGS_APPS.get(session)
    return argv if argv and shutil.which(argv[0]) else None


def run_command(argv: list[str], timeout: float = 30) -> tuple[int, str, str]:
    """Default runner: (returncode, stdout, stderr), never raises."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except OSError as exc:
        return 126, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def apply_live(config: Config, session: str, env=os.environ, runner=run_command) -> str:
    """Change the running session; returns an error text or ""."""
    layout, variant, options = xkb_strings(config)
    path = compositor_config(session, env)
    if path is not None:
        try:
            write_compositor_config(path, layout, variant, options)
        except OSError as exc:
            return str(exc)
        return ""
    if session == "hyprland":
        for key, value in (("kb_layout", layout), ("kb_variant", variant),
                           ("kb_options", options)):
            code, _out, err = runner(["hyprctl", "keyword", f"input:{key}", value])
            if code:
                return err.strip() or "hyprctl failed"
        return ""
    if session == "x11":
        argv = ["setxkbmap", "-layout", layout, "-variant", variant, "-option", ""]
        if options:
            argv += ["-option", options]
        code, _out, err = runner(argv)
        return (err.strip() or "setxkbmap failed") if code else ""
    return ""


def apply_system(config: Config, runner=run_command) -> str:
    layout, variant, options = xkb_strings(config)
    code, _out, err = runner(["pkexec", HELPER, layout, variant, options], timeout=120)
    if code in (126, 127):
        return "Authorization was cancelled."
    return (err.strip() or "Could not save the layout.") if code else ""


def write_system(layout: str, variant: str, options: str,
                 xorg: Path = XORG_CONF, vconsole: Path = VCONSOLE_CONF,
                 keymaps: Path = KEYMAPS_DIR) -> None:
    """Root side of keyboard-apply: xorg.conf.d and the console keymap."""
    if not layout or not valid(layout, variant, options):
        raise ValueError("invalid layout, variant or options")
    model = current(xorg).model
    xorg.parent.mkdir(parents=True, exist_ok=True)
    tmp = xorg.with_name(xorg.name + ".glue-tmp")
    tmp.write_text(render_xorg(layout, variant, options, model), encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, xorg)
    first_variant = variant.split(",")[0] if variant else ""
    keymap = console_keymap(layout.split(",")[0], first_variant, keymaps)
    if keymap:
        try:
            text = vconsole.read_text(encoding="utf-8")
        except OSError:
            text = ""
        vconsole.write_text(set_vconsole(text, keymap), encoding="utf-8")
