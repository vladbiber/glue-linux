"""
Data types shared by the plan resolver and its helpers (no logic, no I/O).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

class PlanError(Exception):
    """Raised for any selection validation failure. Message names the offending field/id."""


@dataclass
class Selection:
    kernel_id: str
    init_id: str
    session_ids: List[str]
    shell_choice: Dict[str, str]  # session_id -> shell_id, required when shell_choices non-empty
    support_ids: List[str]
    gaming: bool
    minimal: bool
    # CPU scheduler from the Gaming screen (roadmap 1.4); only consulted when
    # gaming is True. Defaults to scx_lavd so existing callers keep working.
    scheduler: str = "scx_lavd"
    # Swap screen (roadmap 4.2; consumed by 4.3): auto|zram|none, hibernate laptop-only
    swap_mode: str = "auto"
    hibernate: bool = False


@dataclass
class PlannedFile:
    path: str
    content: str
    mode: int  # e.g. 0o644


@dataclass
class InstallPlan:
    packages: List[str]    # deduplicated, sorted alphabetically
    services: List[str]    # deduplicated, sorted alphabetically
    files: List[PlannedFile]  # sorted by path
    warnings: List[str]    # in the fixed order documented above
    # extra kernel cmdline tokens for limine.conf (sorted, deduplicated)
    cmdline_extra: List[str] = field(default_factory=list)
