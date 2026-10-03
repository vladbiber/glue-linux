"""Network and command-backed application sources with persistent cache."""

from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable

from .catalog import parse_flathub_search, parse_yay_info, parse_yay_search
from .model import App


FLATHUB_SEARCH = "https://flathub.org/api/v2/search"
FLATHUB_DETAILS = "https://flathub.org/api/v2/appstream/{}"


class SearchCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def get(self, source: str, query: str) -> object | None:
        return self.data.get(source, {}).get(query.casefold())

    def put(self, source: str, query: str, payload: object) -> None:
        self.data.setdefault(source, {})[query.casefold()] = payload
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)


def flathub_search(
    query: str, cache: SearchCache, installed: set[str] | None = None, *, timeout: float = 8,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> list[App]:
    request = urllib.request.Request(
        FLATHUB_SEARCH,
        data=json.dumps({"query": query}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "GlueHub/0.1"},
        method="POST",
    )
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        cache.put("flatpak", query, payload)
    except (OSError, ValueError, TimeoutError):
        payload = cache.get("flatpak", query) or {"hits": []}
    return parse_flathub_search(payload, installed or ())


def flathub_details(
    app_id: str, cache: SearchCache, installed: set[str] | None = None, *, timeout: float = 8,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> App | None:
    cached = cache.get("flatpak-detail", app_id)
    try:
        request = urllib.request.Request(
            FLATHUB_DETAILS.format(app_id), headers={"User-Agent": "GlueHub/0.1"})
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        cache.put("flatpak-detail", app_id, payload)
    except (OSError, ValueError, TimeoutError):
        payload = cached or {}
    apps = parse_flathub_search(
        {"hits": [payload]} if payload else {"hits": []}, installed or ())
    return apps[0] if apps else None


def aur_search(
    query: str, cache: SearchCache, installed: set[str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[App]:
    try:
        completed = runner(
            ["yay", "-Ssa", "--aur", "--color", "never", query],
            check=False, capture_output=True, text=True, timeout=15,
        )
        if completed.returncode != 0:
            raise OSError(completed.stderr)
        payload = completed.stdout
        cache.put("aur", query, payload)
    except (OSError, subprocess.TimeoutExpired):
        payload = cache.get("aur", query) or ""
    return parse_yay_search(str(payload), installed or ())


def aur_details(
    name: str, cache: SearchCache, installed: set[str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> App | None:
    try:
        completed = runner(
            ["yay", "-Sia", "--aur", "--color", "never", name],
            check=False, capture_output=True, text=True, timeout=15,
        )
        if completed.returncode != 0:
            raise OSError(completed.stderr)
        payload = completed.stdout
        cache.put("aur-detail", name, payload)
    except (OSError, subprocess.TimeoutExpired):
        payload = cache.get("aur-detail", name) or ""
    return parse_yay_info(str(payload), installed or ())
