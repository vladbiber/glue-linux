"""Login screen background: image checks and regreet.toml edits, no GTK."""

from __future__ import annotations

import glob
import os
import re
import shutil
from pathlib import Path

DEFAULT = "/usr/share/backgrounds/glue/wallpaper.png"
CONFIG = Path("/etc/greetd/regreet.toml")
STATE_DIR = Path("/var/lib/glue")
HELPER = "/usr/lib/glue/greeter-background"
MAX_BYTES = 30 * 1024 * 1024
EXTENSIONS = ("png", "jpg", "webp")

_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*(#.*)?$")
_KEY = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")


def image_type(head: bytes) -> str | None:
    """Extension for PNG, JPEG or WebP magic bytes, None for anything else."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def read_image(stream, limit: int = MAX_BYTES) -> tuple[bytes, str]:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("image is larger than 30 MB")
    kind = image_type(data[:16])
    if kind is None:
        raise ValueError("not a PNG, JPEG or WebP image")
    return data, kind


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_background(text: str, path: str) -> str:
    """Set [background] path and fit = "Cover", leaving every other line alone."""
    lines = text.splitlines()
    wanted = {"path": f"path = {_quote(path)}", "fit": 'fit = "Cover"'}
    start = next((i for i, line in enumerate(lines)
                  if (m := _SECTION.match(line)) and m.group(1).strip() == "background"), None)
    if start is None:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", "[background]", *wanted.values()]
        return "\n".join(lines) + "\n"
    end = next((i for i in range(start + 1, len(lines)) if _SECTION.match(lines[i])),
               len(lines))
    seen = set()
    for i in range(start + 1, end):
        m = _KEY.match(lines[i])
        if m and m.group(1) in wanted:
            lines[i] = wanted[m.group(1)]
            seen.add(m.group(1))
    insert = [wanted[k] for k in ("path", "fit") if k not in seen]
    lines[start + 1:start + 1] = insert
    return "\n".join(lines) + "\n"


def current_background(text: str) -> str:
    in_section = False
    for line in text.splitlines():
        if m := _SECTION.match(line):
            in_section = m.group(1).strip() == "background"
        elif in_section and (m := re.match(r'^\s*path\s*=\s*"((?:[^"\\]|\\.)*)"', line)):
            return m.group(1).replace('\\"', '"').replace("\\\\", "\\")
    return DEFAULT


def _write_atomic(path: Path, data: bytes, mode: int = 0o644) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _update_config(config: Path, path: str) -> None:
    text = config.read_text(encoding="utf-8") if config.exists() else ""
    _write_atomic(config, set_background(text, path).encode())


def apply_set(stream, config: Path = CONFIG, state_dir: Path = STATE_DIR) -> Path:
    data, kind = read_image(stream)
    state_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    if state_dir.is_symlink():
        raise ValueError(f"{state_dir} is a symlink")
    target = state_dir / f"greeter-background.{kind}"
    _write_atomic(target, data)
    for ext in EXTENSIONS:
        old = state_dir / f"greeter-background.{ext}"
        if ext != kind and (old.exists() or old.is_symlink()):
            old.unlink()
    _update_config(config, str(target))
    return target


def apply_clear(config: Path = CONFIG, state_dir: Path = STATE_DIR) -> None:
    _update_config(config, DEFAULT)
    for ext in EXTENSIONS:
        old = state_dir / f"greeter-background.{ext}"
        if old.exists() or old.is_symlink():
            old.unlink()


def availability(dri: str = "/dev/dri/card*") -> str | None:
    """None when ReGreet draws the login screen, otherwise why it does not."""
    if not shutil.which("regreet"):
        return "The graphical login screen is not installed."
    if not glob.glob(dri):
        return "This computer uses the text login screen, which has no picture."
    return None
