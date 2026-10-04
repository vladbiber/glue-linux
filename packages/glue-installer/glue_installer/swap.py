"""Automatic swap sizing (roadmap Phase 4): pure rules, no I/O.

zramen env keys (verified against upstream
https://raw.githubusercontent.com/atweiden/zramen/master/zramen):
ZRAM_COMP_ALGORITHM, ZRAM_PRIORITY (<= 32767), ZRAM_SIZE (% of RAM, 1-250),
ZRAM_MAX_SIZE (MiB cap).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

GIB = 2 ** 30
MIB = 2 ** 20

_MODES = ("auto", "zram", "none")
# Disk space kept free for the system when a hibernation swap is sized.
_SYSTEM_RESERVE = 12 * GIB
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Per-init zramen config: (path, line prefix). Single definition of the paths;
# plan.py imports ZRAMEN_CONF for the default (zstd only) file.
#   dinit: env-file; runit: sourced via `. ./conf`; openrc: /etc/conf.d/<service>
_ZRAMEN_PATHS = {
    "dinit": ("/etc/dinit.d/config/zramen.conf", ""),
    "runit": ("/etc/runit/sv/zramen/conf", "export "),
    "openrc": ("/etc/conf.d/zramen", ""),
}
ZRAMEN_CONF: dict = {
    init: (path, f"{prefix}ZRAM_COMP_ALGORITHM=zstd\n")
    for init, (path, prefix) in _ZRAMEN_PATHS.items()
}


@dataclass
class SwapPlan:
    zram_bytes: int
    zram_priority: int = 100
    disk_bytes: int = 0
    disk_priority: int = 10
    hibernate: bool = False
    warnings: List[str] = field(default_factory=list)

    @property
    def zram_mib(self) -> int:
        return self.zram_bytes // MIB

    @property
    def disk_mib(self) -> int:
        return self.disk_bytes // MIB

    @property
    def disk_gib_human(self) -> str:
        """Disk swap size as text, e.g. "17 GiB" or "1.5 GiB"."""
        gib = self.disk_bytes / GIB
        text = f"{gib:.1f}".rstrip("0").rstrip(".")
        return f"{text} GiB"


def _ceil_mib(n: int) -> int:
    return -(-n // MIB) * MIB


def _gib_ceil(n: int) -> int:
    return -(-n // GIB)


def swap_plan(ram_bytes: int, disk_bytes: int, hibernate: bool = False,
              mode: str = "auto") -> SwapPlan:
    """Decide zram + disk swap from RAM, target disk size and hibernation.

    zram: ram if ram < 2 GiB else min(ram, 8 GiB), always in auto/zram mode.
    Disk (auto only): ram < 2 GiB -> 2*ram; 2..8 GiB -> ram; 8..32 -> 4 GiB;
    > 32 -> 2 GiB; hibernate raises it to ram + 1 GiB. A disk under 12 GiB gets
    no disk swap, under 24 GiB at most 1 GiB. Hibernation is only kept when the
    resulting swap is >= ram + 1 GiB (and leaves 12 GiB free); otherwise it is
    disabled with a warning and the normal rule applies.
    """
    if mode not in _MODES:
        raise ValueError(f"unknown swap mode: {mode!r}")
    if ram_bytes <= 0:
        raise ValueError("ram_bytes must be positive")
    if disk_bytes < 0:
        raise ValueError("disk_bytes must not be negative")

    plan = SwapPlan(zram_bytes=0)
    if mode != "none":
        plan.zram_bytes = ram_bytes if ram_bytes < 2 * GIB else min(ram_bytes, 8 * GIB)

    need = ram_bytes + GIB
    if mode != "auto":
        if hibernate:
            plan.warnings.append(
                "Hibernation disabled: it needs a disk swap and automatic "
                "swap mode is not selected")
        return plan

    if ram_bytes < 2 * GIB:
        base = 2 * ram_bytes
    elif ram_bytes <= 8 * GIB:
        base = ram_bytes
    elif ram_bytes <= 32 * GIB:
        base = 4 * GIB
    else:
        base = 2 * GIB

    def cap(size: int) -> int:
        if disk_bytes < 12 * GIB:
            return 0
        if disk_bytes < 24 * GIB:
            return min(size, GIB)
        return size

    size = cap(base)
    if hibernate:
        want = max(base, need)
        if want <= disk_bytes - _SYSTEM_RESERVE and cap(want) >= need:
            size = want
            plan.hibernate = True
        else:
            plan.warnings.append(
                "Hibernation disabled: the disk is too small for a resume "
                f"swap of {_gib_ceil(need)} GiB")
    plan.disk_bytes = _ceil_mib(size)
    return plan


def parse_meminfo(text: str) -> int:
    """Return MemTotal from /proc/meminfo text, in bytes."""
    m = re.search(r"^MemTotal:\s+(\d+)\s*kB\s*$", text, re.MULTILINE)
    if not m:
        raise ValueError("MemTotal not found in meminfo")
    return int(m.group(1)) * 1024


def zramen_conf(plan: SwapPlan, ram_bytes: int,
                init_id: str) -> Optional[Tuple[str, str]]:
    """(path, content) of the zramen config for init_id, or None.

    None for an unknown init or when the plan has no zram. Size is written as
    percent of RAM (ZRAM_SIZE) plus a MiB cap (ZRAM_MAX_SIZE).
    """
    if init_id not in _ZRAMEN_PATHS or plan.zram_bytes <= 0 or ram_bytes <= 0:
        return None
    path, prefix = _ZRAMEN_PATHS[init_id]
    percent = min(250, max(1, round(plan.zram_bytes * 100 / ram_bytes)))
    lines = [
        "ZRAM_COMP_ALGORITHM=zstd",
        f"ZRAM_SIZE={percent}",
        f"ZRAM_MAX_SIZE={max(1, plan.zram_mib)}",
        f"ZRAM_PRIORITY={plan.zram_priority}",
    ]
    return path, "".join(f"{prefix}{line}\n" for line in lines)


def resume_cmdline(uuid: str) -> str:
    """Kernel cmdline fragment pointing hibernation resume at the swap UUID."""
    if not _UUID_RE.match(uuid or ""):
        raise ValueError(f"invalid swap UUID: {uuid!r}")
    return f"resume=UUID={uuid}"


def mkinitcpio_hooks_with_resume(hooks: List[str]) -> List[str]:
    """Hooks with "resume" right after "filesystems" (idempotent)."""
    if "filesystems" not in hooks:
        raise ValueError("mkinitcpio HOOKS has no 'filesystems'")
    out = [h for h in hooks if h != "resume"]
    out.insert(out.index("filesystems") + 1, "resume")
    return out
