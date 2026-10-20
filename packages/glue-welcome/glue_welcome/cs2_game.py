"""CS2 on this machine: Steam library, map list from the game files, NVIDIA driver, warm-up state."""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

APPID = "730"
GAME_DIR = Path("steamapps/common/Counter-Strike Global Offensive/game")
STEAM_ROOTS = (".local/share/Steam", ".steam/steam",
               ".var/app/com.valvesoftware.Steam/data/Steam")
STATE_FILE = Path(".local/state/glue/cs2-warmup.json")
MAP_RE = re.compile(r"^[a-z0-9_]+$")
ONLINE_OPTION = re.compile(r"(^|\s)[+-](connect|playdemo|playcast)\b|steam://", re.I)
# vpk tree entries whose data lives in the _dir file itself
_DIR_ARCHIVE = 0x7FFF
_KV_TOKEN = re.compile(r'"(?P<quoted>(?:[^"\\]|\\.)*)"|(?P<open>\{)|(?P<close>\})'
                       r'|(?P<comment>//[^\n]*)|(?P<bare>[^\s{}"]+)')


def steam_roots(home: Path) -> list[Path]:
    seen, out = set(), []
    for rel in STEAM_ROOTS:
        root = home / rel
        try:
            real = root.resolve(strict=True)
        except OSError:
            continue
        if real not in seen and (root / "steamapps").is_dir():
            seen.add(real)
            out.append(root)
    return out


def library_paths(root: Path) -> list[Path]:
    libs = [root]
    try:
        text = (root / "steamapps" / "libraryfolders.vdf").read_text(errors="replace")
    except OSError:
        return libs
    for raw in re.findall(r'"path"\s+"([^"]*)"', text):
        path = Path(raw.replace("\\\\", "\\"))
        if path not in libs:
            libs.append(path)
    return libs


def find_cs2(home: Path) -> tuple[Path, Path] | None:
    """(steam root, CS2 game dir) when CS2 is installed in any Steam library."""
    for root in steam_roots(home):
        for lib in library_paths(root):
            game = lib / GAME_DIR
            if (lib / "steamapps" / f"appmanifest_{APPID}.acf").is_file() \
                    and (game / "csgo" / "maps").is_dir():
                return root, game
    return None


def build_id(game: Path) -> str:
    manifest = game.parents[2] / f"appmanifest_{APPID}.acf"
    try:
        found = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text(errors="replace"))
    except OSError:
        return ""
    return found.group(1) if found else ""


def _get(kv: dict, *path: str):
    for key in path:
        if not isinstance(kv, dict):
            return None
        kv = next((v for k, v in kv.items() if k.lower() == key), None)
    return kv


def launch_options(root: Path) -> list[str]:
    """CS2 launch options of every Steam account on this machine."""
    out = []
    for cfg in root.glob("userdata/*/config/localconfig.vdf"):
        try:
            kv = parse_kv(cfg.read_text(errors="replace"))
        except OSError:
            continue
        opts = _get(kv, "userlocalconfigstore", "software", "valve", "steam", "apps",
                    APPID, "launchoptions")
        if isinstance(opts, str) and opts:
            out.append(opts)
    return out


def _find_mapping(kv, depth: int = 0) -> str:
    if not isinstance(kv, dict) or depth > 8:
        return ""
    for key, value in kv.items():
        if key.lower() == "compattoolmapping" and isinstance(value, dict):
            entry = _get(value, APPID)
            name = _get(entry, "name") if isinstance(entry, dict) else None
            if isinstance(name, str) and name:
                return name
        found = _find_mapping(value, depth + 1)
        if found:
            return found
    return ""


def compat_tool(root: Path) -> str:
    """Compatibility tool forced on CS2 (e.g. a Proton), empty for the native build."""
    for cfg in [root / "config/config.vdf", *root.glob("userdata/*/config/localconfig.vdf")]:
        try:
            name = _find_mapping(parse_kv(cfg.read_text(errors="replace")))
        except OSError:
            continue
        if name:
            return name
    return ""


def joins_server(options: list[str]) -> bool:
    """Launch options that would take the warm-up onto a server instead of a local map."""
    return any(ONLINE_OPTION.search(o) for o in options)


def nvidia_driver(sys_root: Path = Path("/")) -> str:
    """Loaded NVIDIA kernel module version, empty without one."""
    try:
        return (sys_root / "sys/module/nvidia/version").read_text().strip()
    except OSError:
        return ""


def parse_kv(text: str) -> dict:
    """Valve KeyValues text to nested dicts; later duplicate keys win."""
    root: dict = {}
    stack, key = [root], None
    for tok in _KV_TOKEN.finditer(text):
        kind, word = tok.lastgroup, tok.group(tok.lastgroup)
        if kind == "comment":
            continue
        if kind == "open":
            child: dict = {}
            if key is not None:
                stack[-1][key] = child
            stack.append(child)
            key = None
        elif kind == "close":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = word
        else:
            stack[-1][key] = word
            key = None
    return root


def vpk_file(dir_vpk: Path, wanted: str) -> bytes | None:
    """Bytes of one file from a VPK v1/v2 set (pak01_dir.vpk + pak01_NNN.vpk)."""
    try:
        data = dir_vpk.read_bytes()
    except OSError:
        return None
    if len(data) < 12:
        return None
    sig, version, tree_size = struct.unpack_from("<III", data, 0)
    if sig != 0x55AA1234 or version not in (1, 2):
        return None
    header = 12 if version == 1 else 28
    want_dir, _, want_name = wanted.rpartition("/")
    want_name, _, want_ext = want_name.rpartition(".")
    pos = header

    def cstr() -> str:
        nonlocal pos
        end = data.index(b"\0", pos)
        word = data[pos:end].decode("latin-1")
        pos = end + 1
        return word

    try:
        while ext := cstr():
            while (folder := cstr()):
                while (name := cstr()):
                    _crc, pre, idx, off, size, _term = struct.unpack_from("<IHHIIH", data, pos)
                    pos += 18
                    preload = data[pos:pos + pre]
                    pos += pre
                    if (ext, folder.strip(), name) != (want_ext, want_dir, want_name):
                        continue
                    if idx == _DIR_ARCHIVE:
                        start = header + tree_size + off
                        return preload + data[start:start + size]
                    part = dir_vpk.with_name(dir_vpk.name.replace("_dir.vpk", f"_{idx:03d}.vpk"))
                    with open(part, "rb") as f:
                        f.seek(off)
                        return preload + f.read(size)
    except (ValueError, struct.error, OSError):
        return None
    return None


def installed_maps(game: Path) -> set[str]:
    maps = set()
    try:
        for path in (game / "csgo" / "maps").glob("*.vpk"):
            if MAP_RE.match(path.stem):
                maps.add(path.stem)
    except OSError:
        pass
    return maps


def active_group(game: Path) -> list[str]:
    """mg_active from scripts gamemodes.txt; Valve does not always keep it current."""
    raw = vpk_file(game / "csgo" / "pak01_dir.vpk", "gamemodes.txt")
    if raw is None:
        return []
    kv = parse_kv(raw.decode("utf-8", "replace"))
    group = kv.get("GameModes.txt", {}).get("mapgroups", {}).get("mg_active", {})
    maps = group.get("maps", {}) if isinstance(group, dict) else {}
    return [m for m in maps if isinstance(m, str) and MAP_RE.match(m)]


def warmup_maps(game: Path, everything: bool = False) -> list[str]:
    """Active group first, then competitive maps with a menu (_vanity) variant; all with everything."""
    have = installed_maps(game)
    playable = {m for m in have if not m.endswith("_vanity")
                and m.split("_", 1)[0] in ("de", "cs", "ar")}
    order = [m for m in active_group(game) if m in playable]
    featured = sorted(m for m in playable if m.startswith("de_") and f"{m}_vanity" in have)
    rest = sorted(playable) if everything else []
    for m in featured + rest:
        if m not in order:
            order.append(m)
    return order


class State:
    """Per-user warm-up record, keyed on the NVIDIA driver that owns the cache."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text())
            if not isinstance(self.data, dict):
                raise ValueError
        except (OSError, ValueError):
            self.data = {}

    def maps_done(self, driver: str) -> set[str]:
        if self.data.get("driver") != driver:
            return set()
        return set(self.data.get("maps", {}))

    def mark(self, driver: str, build: str, name: str, info: dict) -> None:
        if self.data.get("driver") != driver:
            self.data = {"declined": self.data.get("declined", False)}
        self.data.pop("complete", None)
        self.data.update(driver=driver, build=build)
        self.data.setdefault("maps", {})[name] = info
        self.save()

    def complete(self, driver: str) -> bool:
        return self.data.get("driver") == driver and bool(self.data.get("complete"))

    def finish(self, driver: str) -> None:
        if self.data.get("driver") == driver:
            self.data["complete"] = True
            self.save()

    @property
    def declined(self) -> bool:
        return bool(self.data.get("declined"))

    def decline(self) -> None:
        self.data["declined"] = True
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
        tmp.replace(self.path)


def needs_warmup(home: Path, sys_root: Path = Path("/")) -> bool:
    """Cheap check for Glue Welcome: NVIDIA + CS2 installed + not finished for this driver."""
    driver = nvidia_driver(sys_root)
    return bool(driver) and find_cs2(home) is not None \
        and not State(home / STATE_FILE).complete(driver)


def pending(home: Path, sys_root: Path = Path("/"),
            everything: bool = False) -> tuple[Path, Path, str, list[str]] | None:
    """(steam root, game dir, driver, maps left) when a warm-up would help; None otherwise."""
    driver = nvidia_driver(sys_root)
    found = find_cs2(home) if driver else None
    if not found:
        return None
    done = State(home / STATE_FILE).maps_done(driver)
    left = [m for m in warmup_maps(found[1], everything) if m not in done]
    return (found[0], found[1], driver, left) if left else None
