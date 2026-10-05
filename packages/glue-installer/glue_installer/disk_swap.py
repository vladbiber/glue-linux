"""Swap on the target disk (roadmap 4.3): layout, mkswap/swapon steps, RAM.

Pure except read_ram_bytes (reads /proc/meminfo) and staged_pacman_conf
(writes a temp file). Disk layout reuses disks.plan_disk for validation.
"""

from __future__ import annotations

import dataclasses
import os
import re
import tempfile
from pathlib import Path
from typing import List, Mapping, Optional

from glue_installer.disks import (
    _TYPE_LINUX, BlockDevice, DiskError, DiskPlan, PartitionSpec, discover,
    partition_path, plan_disk,
)
from glue_installer.executor import RunCommand, Step
from glue_installer.swap import SwapPlan, parse_meminfo

_TYPE_SWAP = "8200"
_DRY_RUN_DISK_BYTES = 32 * 1024 ** 3


def resolve_disk(path: str, *, dry_run: bool) -> BlockDevice:
    """Find the BlockDevice for --disk. In dry-run, a path that is not a real
    block device (or a machine without lsblk) yields a synthetic 32 GiB disk
    so the step list can be previewed anywhere."""
    try:
        devices = discover()
    except DiskError:
        if not dry_run:
            raise
        devices = []
    for device in devices:
        if device.path == path:
            return device
    if dry_run:
        return BlockDevice(
            name=os.path.basename(path), path=path,
            size_bytes=_DRY_RUN_DISK_BYTES, model="dry-run synthetic disk",
            is_removable=False, has_mounted_partitions=False,
        )
    raise DiskError(f"No such disk: {path}")


def read_ram_bytes(meminfo_path: str = "/proc/meminfo",
                   env: Optional[Mapping[str, str]] = None) -> int:
    """Total RAM in bytes; GLUE_RAM_BYTES overrides. 0 when unknown."""
    env = os.environ if env is None else env
    raw = env.get("GLUE_RAM_BYTES")
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            return 0
    try:
        return parse_meminfo(Path(meminfo_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0


def plan_disk_with_swap(device: BlockDevice, firmware: str, swap: SwapPlan,
                        swap_uuid: str = "") -> DiskPlan:
    """Full-disk layout: ESP/BIOS-boot, swap, root. Same as plan_disk when
    the plan has no disk swap."""
    base = plan_disk(device, firmware)
    if swap.disk_bytes <= 0:
        return base
    first = base.partitions[0]
    swap_part = PartitionSpec(
        number=2, path=partition_path(device.path, 2), type_code=_TYPE_SWAP,
        size=f"+{swap.disk_mib}M", filesystem="swap", mountpoint="swap",
    )
    root = PartitionSpec(
        number=3, path=partition_path(device.path, 3), type_code=_TYPE_LINUX,
        size="0", filesystem="ext4", mountpoint="/",
    )
    return dataclasses.replace(
        base, partitions=(first, swap_part, root),
        swap_uuid=swap_uuid if swap.hibernate else "",
    )


def plan_existing_with_swap(disk_plan: DiskPlan, swap: SwapPlan) -> DiskPlan:
    """Existing-partition mode cannot add a partition: use a swapfile."""
    return dataclasses.replace(disk_plan, swapfile_mib=swap.disk_mib)


def swap_steps(disk_plan: DiskPlan, *, target: str = "/mnt") -> List[Step]:
    """mkswap + swapon for the swap partition and/or swapfile (after mounts)."""
    t = target.rstrip("/")
    steps: List[Step] = []
    for part in disk_plan.partitions:
        if part.filesystem != "swap":
            continue
        mkswap = ["mkswap"]
        if disk_plan.swap_uuid:
            mkswap += ["-U", disk_plan.swap_uuid]
        steps.append(RunCommand(
            argv=mkswap + [part.path],
            description=f"Format {part.path} as swap",
        ))
        steps.append(RunCommand(
            argv=["swapon", part.path],
            description=f"Enable swap on {part.path}",
        ))
    if disk_plan.swapfile_mib > 0:
        f = f"{t}/swapfile"
        # Root is ext4 here; on btrfs this would need chattr +C first.
        steps += [
            RunCommand(argv=["fallocate", "-l", f"{disk_plan.swapfile_mib}M", f],
                       description=f"Allocate {disk_plan.swapfile_mib} MiB swapfile"),
            RunCommand(argv=["chmod", "600", f],
                       description="Restrict swapfile permissions"),
            RunCommand(argv=["mkswap", f], description="Format swapfile"),
            RunCommand(argv=["swapon", f], description="Enable swapfile"),
        ]
    return steps


def swapfile_fstab_steps(disk_plan: DiskPlan, *, target: str = "/mnt") -> List[Step]:
    """Make the swapfile fstab entry root-relative (no-op if fstabgen did)."""
    if disk_plan.swapfile_mib <= 0:
        return []
    t = target.rstrip("/")
    return [RunCommand(
        argv=["sed", "-i", f"s|^{t}/swapfile|/swapfile|", f"{t}/etc/fstab"],
        description="Make swapfile fstab path relative to the new root",
    )]


V3_MIRRORLIST = "/etc/pacman.d/cachyos-v3-mirrorlist"


def staged_pacman_conf(path: Optional[str], cpu_v3: bool, kernel_id: str,
                       mirrorlist: str = V3_MIRRORLIST) -> Optional[str]:
    """Path of a pacman.conf with [cachyos-v3] added when it applies.
    Without the v3 mirrorlist on the host the plain conf is kept: pacman
    refuses a config whose Include is missing."""
    if not (cpu_v3 and kernel_id == "linux-cachyos" and path):
        return path
    if not Path(mirrorlist).is_file():
        return path
    try:
        orig = Path(path).read_text(encoding="utf-8")
        # [cachyos-v3] packages are built for arch x86_64_v3
        patched = re.sub(r"(?m)^Architecture\s*=.*$", "Architecture = x86_64 x86_64_v3",
                         orig).replace(
            "[cachyos]\n",
            f"[cachyos-v3]\nInclude = {mirrorlist}\n\n[cachyos]\n",
        )
        with tempfile.NamedTemporaryFile(
            suffix=".conf", delete=False, mode="w", encoding="utf-8"
        ) as tmp:
            tmp.write(patched)
            return tmp.name
    except OSError:
        return path
