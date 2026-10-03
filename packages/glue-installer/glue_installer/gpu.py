"""
GPU detection for the gaming toggle (catalog gaming.gpu_autodetect).

Steam depends on the virtual `vulkan-driver`/`lib32-vulkan-driver` packages;
without an explicit provider in the basestrap set, pacman auto-picks the first
provider alphabetically — nvidia userspace on every machine, including AMD and
Intel ones. Detection reads sysfs directly (no lspci/pciutils needed on the
live ISO) and the plan pins the right provider explicitly.

Pure logic split from I/O: `gaming_gpu_packages` is a pure mapping;
`detect_gpu_vendors` only reads sysfs and is parameterized for tests.
"""

from __future__ import annotations

import glob
import os
from typing import FrozenSet, List

# PCI vendor ids of the three GPU vendors we can pin a Vulkan driver for
_VENDOR_IDS = {
    "0x10de": "nvidia",
    "0x1002": "amd",
    "0x8086": "intel",
}

# PCI class prefix 0x03 = display controller (VGA, 3D, display, ...)
_DISPLAY_CLASS_PREFIX = "0x03"

# Explicit Vulkan userspace per vendor (verified in Artix world / lib32).
# nvidia-open-dkms builds the kernel module against the installed headers
# (the catalog kernels all ship their -headers package) — without it the
# nvidia userspace has no module to pair with and nothing renders.
_GPU_PACKAGES = {
    # libva-nvidia-driver: VA-API video decode on NVIDIA (browsers, mpv)
    "nvidia": ["nvidia-open-dkms", "nvidia-utils", "lib32-nvidia-utils",
               "libva-nvidia-driver"],
    "amd": ["vulkan-radeon", "lib32-vulkan-radeon"],
    "intel": ["vulkan-intel", "lib32-vulkan-intel"],
}

# Driver stack any graphical session needs (Wayland compositors hard-require
# working EGL: wlroots compositors die with "Failed to allocate device list" without
# it). mesa covers Intel/AMD GL+EGL; NVIDIA needs its module + userspace and
# egl-wayland for Wayland compositors.
_SESSION_GPU_PACKAGES = {
    "nvidia": ["nvidia-open-dkms", "nvidia-utils", "egl-wayland"],
    "amd": ["vulkan-radeon"],
    "intel": ["vulkan-intel"],
}

# Nothing detected (VM, exotic GPU): install both Mesa Vulkan drivers — they
# are small, coexist fine, and cover the machines nvidia userspace would break.
_FALLBACK_PACKAGES = [
    "vulkan-radeon", "lib32-vulkan-radeon",
    "vulkan-intel", "lib32-vulkan-intel",
]


def detect_gpu_vendors(sys_pci: str = "/sys/bus/pci/devices") -> FrozenSet[str]:
    """Return the set of GPU vendors present ({'nvidia','amd','intel'} ⊆).

    Reads PCI class/vendor from sysfs; never raises — a machine where sysfs is
    unreadable simply reports no vendors (the caller falls back to Mesa).
    """
    vendors = set()
    for dev in glob.glob(os.path.join(sys_pci, "*")):
        try:
            with open(os.path.join(dev, "class"), encoding="ascii") as fh:
                if not fh.read().strip().startswith(_DISPLAY_CLASS_PREFIX):
                    continue
            with open(os.path.join(dev, "vendor"), encoding="ascii") as fh:
                vendor = _VENDOR_IDS.get(fh.read().strip())
        except OSError:
            continue
        if vendor:
            vendors.add(vendor)
    return frozenset(vendors)


def gaming_gpu_packages(vendors: FrozenSet[str]) -> List[str]:
    """Pure map: detected vendors -> sorted explicit Vulkan driver packages."""
    packages: set = set()
    for vendor in vendors:
        packages.update(_GPU_PACKAGES.get(vendor, []))
    if not packages:
        packages.update(_FALLBACK_PACKAGES)
    return sorted(packages)


def session_gpu_packages(vendors: FrozenSet[str]) -> List[str]:
    """Pure map: detected vendors -> sorted driver packages any graphical
    session needs (mesa always; NVIDIA module/userspace when present)."""
    packages: set = {"mesa"}
    for vendor in vendors:
        packages.update(_SESSION_GPU_PACKAGES.get(vendor, []))
    return sorted(packages)
