"""Laptop-specific hardware detection for the Glue Linux installer (Faza 1.11)."""
from __future__ import annotations

import glob


def is_laptop(bat_paths=None) -> bool:
    """True if the system has a battery (BAT* in /sys/class/power_supply).

    bat_paths: injectable list of paths for unit tests; defaults to real glob.
    """
    if bat_paths is None:
        bat_paths = glob.glob("/sys/class/power_supply/BAT*")
    return bool(bat_paths)


def cpu_vendor(cpuinfo_text: str) -> str:
    """Return 'intel', 'amd', or 'other' from /proc/cpuinfo content."""
    for line in cpuinfo_text.splitlines():
        if line.startswith("vendor_id"):
            vid = line.split(":", 1)[-1].strip()
            if vid == "GenuineIntel":
                return "intel"
            if vid == "AuthenticAMD":
                return "amd"
            return "other"
    return "other"


def amd_needs_pstate_active(cpuinfo_text: str) -> bool:
    """True if CPU is AMD Zen2+ (family >= 23) with CPPC hardware support.

    Rule: vendor AMD + cpu family >= 23 + flag 'cppc' present.
    Zen1 (family 23, no cppc flag) returns False; Zen2+ expose cppc.
    """
    if cpu_vendor(cpuinfo_text) != "amd":
        return False
    family = None
    has_cppc = False
    for line in cpuinfo_text.splitlines():
        if line.startswith("cpu family") and family is None:
            try:
                family = int(line.split(":", 1)[-1].strip())
            except ValueError:
                pass
        if line.startswith("flags"):
            has_cppc = "cppc" in line.split(":", 1)[-1].split()
    return family is not None and family >= 23 and has_cppc


def detect_laptop() -> bool:
    """Probe the running system for a battery (laptop). Returns False on error."""
    try:
        return is_laptop()
    except Exception:
        return False


def detect_cpu_vendor() -> str:
    """Read /proc/cpuinfo and return cpu_vendor(). Returns 'other' on error."""
    try:
        with open("/proc/cpuinfo", encoding="ascii", errors="replace") as fh:
            return cpu_vendor(fh.read())
    except Exception:
        return "other"


def detect_amd_pstate_active() -> bool:
    """Read /proc/cpuinfo and return amd_needs_pstate_active(). Returns False on error."""
    try:
        with open("/proc/cpuinfo", encoding="ascii", errors="replace") as fh:
            return amd_needs_pstate_active(fh.read())
    except Exception:
        return False
