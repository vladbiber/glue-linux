"""Pure parsers and small read-only probes used by System and Updates pages."""

from __future__ import annotations

import os
import json
import platform
import subprocess
from pathlib import Path
from typing import Callable


def parse_checkupdates(output: str) -> list[tuple[str, str, str]]:
    updates: list[tuple[str, str, str]] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[-2] == "->":
            updates.append((parts[0], parts[-3], parts[-1]))
    return updates


def parse_flatpak_updates(output: str) -> list[str]:
    return [line.split("\t", 1)[0].strip() for line in output.splitlines() if line.strip()]


def parse_sensors(output: str) -> str:
    try:
        data = json.loads(output)
    except ValueError:
        return "Indisponibilă"
    values: list[float] = []
    def visit(value) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key.endswith("_input") and isinstance(child, (int, float)):
                    values.append(float(child))
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(data)
    plausible = [value for value in values if -20 <= value <= 150]
    return f"{max(plausible):.0f} °C" if plausible else "Indisponibilă"


def update_counts(
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> tuple[int, int, int]:
    def call(argv: list[str]) -> str:
        try:
            done = runner(argv, check=False, capture_output=True, text=True, timeout=20)
            return done.stdout if done.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""
    native = parse_checkupdates(call(["checkupdates"]))
    flatpak = parse_flatpak_updates(call(
        ["flatpak", "remote-ls", "--user", "--updates", "--columns=application"]
    ))
    aur = parse_checkupdates(call(["yay", "-Qua", "--aur", "--color", "never"]))
    return len(native), len(flatpak), len(aur)


def _gpu_names(root: Path = Path("/sys/class/drm")) -> str:
    names = {"0x1002": "AMD", "0x10de": "NVIDIA", "0x8086": "Intel"}
    found: list[str] = []
    for vendor in root.glob("card*/device/vendor"):
        try:
            name = names.get(vendor.read_text().strip().casefold(), "Alt GPU")
            if name not in found:
                found.append(name)
        except OSError:
            continue
    return ", ".join(found) or "Necunoscut"


def _command_text(argv: list[str]) -> str:
    try:
        done = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=3)
        return done.stdout.strip() if done.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def system_summary() -> dict[str, str]:
    mem = "Necunoscut"
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                gib = int(line.split()[1]) / 1024 / 1024
                mem = f"{gib:.1f} GiB"
                break
    except (OSError, ValueError):
        pass
    return {
        "Sistem": platform.freedesktop_os_release().get("PRETTY_NAME", "Glue Linux"),
        "Kernel": platform.release(),
        "Sesiune": os.environ.get("XDG_CURRENT_DESKTOP", "Necunoscut"),
        "Memorie": mem,
        "GPU": _gpu_names(),
        "Temperatură maximă": parse_sensors(_command_text(["sensors", "-j"])),
        "Profil energie": _command_text(["powerprofilesctl", "get"]) or "Necunoscut",
    }
