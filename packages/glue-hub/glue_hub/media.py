"""Small, bounded image cache for icons and screenshots."""

from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable


def cached_image(
    url: str, cache_dir: Path, *, timeout: float = 10,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> Path | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".svg", ".jxl"}:
        suffix = ".img"
    target = cache_dir / (hashlib.sha256(url.encode()).hexdigest() + suffix)
    if target.is_file() and target.stat().st_size:
        return target
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "GlueHub/0.1"})
        with opener(request, timeout=timeout) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if not data or len(data) > 8 * 1024 * 1024:
            return None
        cache_dir.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_bytes(data)
        temp.replace(target)
        return target
    except (OSError, TimeoutError):
        return None
