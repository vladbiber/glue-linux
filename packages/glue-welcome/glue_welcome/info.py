"""Read-only facts about this computer for the System page."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path

try:
    from glue_apps.i18n import _
except ImportError:      # tests run without glue-apps installed
    def _(text: str, **values: object) -> str:
        return text.format(**values) if values else text


def parse_sensors(output: str) -> str:
    try:
        data = json.loads(output)
    except ValueError:
        return _("Unavailable")
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
    return f"{max(plausible):.0f} °C" if plausible else _("Unavailable")


def _gpu_names(root: Path = Path("/sys/class/drm")) -> str:
    names = {"0x1002": "AMD", "0x10de": "NVIDIA", "0x8086": "Intel"}
    found: list[str] = []
    for vendor in root.glob("card*/device/vendor"):
        try:
            name = names.get(vendor.read_text().strip().casefold(), _("Other GPU"))
            if name not in found:
                found.append(name)
        except OSError:
            continue
    return ", ".join(found) or _("Unknown")


def _command_text(argv: list[str]) -> str:
    try:
        done = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=3)
        return done.stdout.strip() if done.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def system_summary() -> dict[str, str]:
    mem = _("Unknown")
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                gib = int(line.split()[1]) / 1024 / 1024
                mem = f"{gib:.1f} GiB"
                break
    except (OSError, ValueError):
        pass
    return {
        "Operating system": platform.freedesktop_os_release().get("PRETTY_NAME", "Glue Linux"),
        "Kernel": platform.release(),
        "Session": os.environ.get("XDG_CURRENT_DESKTOP", _("Unknown")),
        "Memory": mem,
        "Graphics card": _gpu_names(),
        "Temperature": parse_sensors(_command_text(["sensors", "-j"])),
        "Power mode": _command_text(["powerprofilesctl", "get"]),
    }
