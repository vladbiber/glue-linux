"""CPU capability detection for the Glue Linux installer."""
from __future__ import annotations

import subprocess


def cpu_supports_v3(ld_help_output: str) -> bool:
    """Return True if the ld.so --help output advertises x86-64-v3 (supported)."""
    return "x86-64-v3 (supported" in ld_help_output


def detect_cpu_v3() -> bool:
    """Probe the running CPU for x86-64-v3 support. Returns False on any error."""
    try:
        result = subprocess.run(
            ["/lib/ld-linux-x86-64.so.2", "--help"],
            capture_output=True, text=True,
        )
        return cpu_supports_v3(result.stdout + result.stderr)
    except Exception:
        return False
