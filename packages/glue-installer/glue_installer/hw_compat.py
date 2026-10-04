"""
Hardware compatibility: NVIDIA driver branch by GPU generation, Broadcom `wl`,
audio firmware, firmware updates and CPU microcode (roadmap 10.8 a/d/e/f).

Package names verified 2026-10-04 against the real repo DBs (system, world,
galaxy, lib32, cachyos):
  world   nvidia-open-dkms/nvidia-utils 615.71, nvidia-580xx-dkms and
          nvidia-580xx-utils 580.178.04, broadcom-wl-dkms, sof-firmware,
          alsa-ucm-conf, intel-ucode, fwupd, egl-wayland
  lib32   lib32-nvidia-utils, lib32-nvidia-580xx-utils
  galaxy  libva-nvidia-driver
  system  amd-ucode
  cachyos nvidia-470xx-dkms, nvidia-470xx-utils, lib32-nvidia-470xx-utils
No substitution was needed. None of them depends on systemd.

NVIDIA ranges come from the 580.95.05 README, Appendix A (current GPUs start
at 0x1340; the 470.xx legacy table covers 0x0fc6-0x103c, 0x1180-0x11fc,
0x1280-0x12ba). Boundaries are widened to the full family block:
  0x0fc0-0x103f, 0x1180-0x11ff, 0x1280-0x12ff  Kepler       -> 470xx
  0x1340-0x1d7f                                 Maxwell+Pascal -> 580xx
  0x1d80-0x1dff                                 Volta        -> 580xx
  0x1e00-0x2fff  Turing, Ampere, Ada, Blackwell -> open modules
  other ids below 0x1340 (Fermi and older)      -> legacy, no proprietary driver
  0x1300-0x133f and ids above 0x2fff            -> unknown (open modules)
Several NVIDIA GPUs share one kernel module, so the oldest generation wins.

Microcode is installed on every install, servers included: CPU security fixes
matter everywhere (the default mkinitcpio.conf has the `microcode` hook).
fwupd is activated on demand, so no service is enabled.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from typing import Iterable, List, Set, Tuple

from glue_installer.laptop import cpu_vendor
from glue_installer.plan_types import PlannedFile

_NVIDIA_VENDOR = 0x10DE
_BROADCOM_VENDOR = 0x14E4

# (first, last, generation), inclusive
_NVIDIA_RANGES = (
    (0x0FC0, 0x103F, "kepler"),
    (0x1180, 0x11FF, "kepler"),
    (0x1280, 0x12FF, "kepler"),
    (0x0000, 0x12FF, "legacy"),
    (0x1340, 0x1D7F, "maxwell_pascal"),
    (0x1D80, 0x1DFF, "volta"),
    (0x1E00, 0x2FFF, "turing_plus"),
)

# oldest first; the oldest generation present decides the module
_RANK = {"legacy": 0, "kepler": 1, "maxwell_pascal": 2, "volta": 2,
         "turing_plus": 3, "unknown": 3}

_OPEN = ["nvidia-open-dkms", "nvidia-utils", "libva-nvidia-driver", "egl-wayland"]
_DEFAULT_NVIDIA = {"nvidia-open-dkms", "nvidia-utils", "lib32-nvidia-utils",
                   "libva-nvidia-driver"}

_KEPLER_WARNING = (
    "NVIDIA Kepler GPU: the 470xx driver does not support Wayland "
    "compositors, so the gluewc session may not start")
_LEGACY_WARNING = (
    "NVIDIA GPU too old for any packaged proprietary driver: "
    "using the open nouveau/mesa drivers")

# Broadcom PCI ids that need the proprietary `wl` driver (BCM4311/4312/4321/
# 4322/43224/43225/4331/4360 family). Ids served by in-tree b43/brcmsmac
# (0x4727) or brcmfmac (0x43a3, 0x4464, 0x43ec) are deliberately absent.
_BROADCOM_WL_IDS = frozenset({
    0x4311, 0x4312, 0x4313, 0x4315, 0x4328, 0x4329, 0x432A, 0x432B, 0x432C,
    0x432D, 0x4353, 0x4357, 0x4358, 0x4359, 0x4365, 0x43A0, 0x43B1, 0x4331,
    0x4360,
})

BROADCOM_BLACKLIST_PATH = "/etc/modprobe.d/glue-broadcom-wl.conf"
_BROADCOM_BLACKLIST = (
    "# Glue Linux: the proprietary wl driver conflicts with these modules\n"
    "blacklist b43\nblacklist bcma\nblacklist ssb\nblacklist brcmsmac\n")


@dataclass(frozen=True)
class HardwareProfile:
    nvidia_devices: Tuple[int, ...] = ()
    wifi_devices: Tuple[Tuple[int, int], ...] = ()
    cpu_vendor: str = "other"


def nvidia_generation(device_id: int) -> str:
    """Map a 16-bit NVIDIA PCI device id to its driver generation."""
    for first, last, gen in _NVIDIA_RANGES:
        if first <= device_id <= last:
            return gen
    return "unknown"


def _oldest(devices: Iterable[int]) -> str:
    return min((nvidia_generation(d) for d in devices),
               key=lambda g: (_RANK[g], g), default="unknown")


def nvidia_driver_packages(generation: str, lib32: bool) -> Tuple[Set[str], List[str]]:
    """Driver packages and warnings for one generation (lib32 = gaming)."""
    if generation == "legacy":
        return set(), [_LEGACY_WARNING]
    if generation in ("maxwell_pascal", "volta"):
        pkgs = {"nvidia-580xx-dkms", "nvidia-580xx-utils", "egl-wayland"}
        return (pkgs | {"lib32-nvidia-580xx-utils"} if lib32 else pkgs), []
    if generation == "kepler":
        pkgs = {"nvidia-470xx-dkms", "nvidia-470xx-utils", "egl-wayland"}
        return (pkgs | {"lib32-nvidia-470xx-utils"} if lib32 else pkgs), [_KEPLER_WARNING]
    pkgs = set(_OPEN)
    return (pkgs | {"lib32-nvidia-utils"} if lib32 else pkgs), []


def needs_broadcom_wl(wifi_devices: Iterable[Tuple[int, int]]) -> bool:
    return any(v == _BROADCOM_VENDOR and d in _BROADCOM_WL_IDS
               for v, d in wifi_devices)


def compat_packages(hw: HardwareProfile, graphical: bool,
                    gaming: bool) -> Tuple[Set[str], List[str]]:
    """Packages to add for this hardware, plus warnings."""
    pkgs: Set[str] = set()
    warnings: List[str] = []
    if hw.nvidia_devices and (graphical or gaming):
        pkgs, warnings = nvidia_driver_packages(_oldest(hw.nvidia_devices), gaming)
    if graphical:
        pkgs |= {"sof-firmware", "alsa-ucm-conf", "fwupd"}
    if hw.cpu_vendor == "intel":
        pkgs.add("intel-ucode")
    elif hw.cpu_vendor == "amd":
        pkgs.add("amd-ucode")
    if needs_broadcom_wl(hw.wifi_devices):
        pkgs.add("broadcom-wl-dkms")
    return pkgs, warnings


def proprietary_nvidia(hw: HardwareProfile) -> bool:
    """False only when NVIDIA hardware is present but legacy (no driver)."""
    return not (hw.nvidia_devices and _oldest(hw.nvidia_devices) == "legacy")


def apply_hw(packages: Set[str], hw: HardwareProfile, graphical: bool,
             gaming: bool) -> List[str]:
    """Adjust the plan's package set in place; return the new warnings."""
    add, warnings = compat_packages(hw, graphical, gaming)
    if hw.nvidia_devices and _oldest(hw.nvidia_devices) not in ("turing_plus", "unknown"):
        packages -= _DEFAULT_NVIDIA
        if not proprietary_nvidia(hw):
            packages.discard("egl-wayland")
    packages |= add
    return warnings


def hw_files(hw: HardwareProfile) -> List[PlannedFile]:
    if needs_broadcom_wl(hw.wifi_devices):
        return [PlannedFile(path=BROADCOM_BLACKLIST_PATH,
                            content=_BROADCOM_BLACKLIST, mode=0o644)]
    return []


def _read_hex(path: str) -> int | None:
    try:
        with open(path, encoding="ascii") as fh:
            return int(fh.read().strip(), 16)
    except (OSError, ValueError):
        return None


def detect_hardware(sys_pci: str = "/sys/bus/pci/devices",
                    cpuinfo_text: str | None = None) -> HardwareProfile:
    """Read NVIDIA GPUs and Wi-Fi NICs from sysfs; never raises."""
    nvidia: List[int] = []
    wifi: List[Tuple[int, int]] = []
    for dev in sorted(glob.glob(os.path.join(sys_pci, "*"))):
        cls = _read_hex(os.path.join(dev, "class"))
        vendor = _read_hex(os.path.join(dev, "vendor"))
        device = _read_hex(os.path.join(dev, "device"))
        if cls is None or vendor is None or device is None:
            continue
        if vendor == _NVIDIA_VENDOR and cls >> 16 == 0x03:
            nvidia.append(device)
        elif (cls >> 8 == 0x0280) or (cls >> 8 == 0x0200 and vendor == _BROADCOM_VENDOR):
            wifi.append((vendor, device))
    if cpuinfo_text is None:
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as fh:
                cpuinfo_text = fh.read()
        except OSError:
            cpuinfo_text = ""
    return HardwareProfile(tuple(nvidia), tuple(wifi), cpu_vendor(cpuinfo_text))
