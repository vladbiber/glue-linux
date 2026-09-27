"""Unit tests for glue_installer.gpu (GPU detection + Vulkan driver pins)."""

import sys
import tempfile
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.gpu import detect_gpu_vendors, gaming_gpu_packages


def _fake_pci(devices):
    """Create a fake /sys/bus/pci/devices tree: [(class, vendor), ...]."""
    root = tempfile.mkdtemp()
    for i, (cls, vendor) in enumerate(devices):
        d = Path(root) / f"0000:0{i}:00.0"
        d.mkdir()
        (d / "class").write_text(cls + "\n")
        (d / "vendor").write_text(vendor + "\n")
    return root


class TestDetectGpuVendors(unittest.TestCase):
    def test_amd_gpu_detected(self):
        root = _fake_pci([("0x030000", "0x1002"), ("0x020000", "0x8086")])
        self.assertEqual(detect_gpu_vendors(root), frozenset({"amd"}))

    def test_nvidia_and_intel_hybrid(self):
        root = _fake_pci([("0x030000", "0x8086"), ("0x030200", "0x10de")])
        self.assertEqual(detect_gpu_vendors(root), frozenset({"nvidia", "intel"}))

    def test_non_display_devices_ignored(self):
        # 0x02 = network controller even with a GPU vendor id
        root = _fake_pci([("0x020000", "0x10de")])
        self.assertEqual(detect_gpu_vendors(root), frozenset())

    def test_missing_sysfs_returns_empty(self):
        self.assertEqual(detect_gpu_vendors("/nonexistent/path"), frozenset())

    def test_unknown_vendor_ignored(self):
        root = _fake_pci([("0x030000", "0x1234")])
        self.assertEqual(detect_gpu_vendors(root), frozenset())


class TestGamingGpuPackages(unittest.TestCase):
    def test_amd(self):
        self.assertEqual(
            gaming_gpu_packages(frozenset({"amd"})),
            ["lib32-vulkan-radeon", "vulkan-radeon"],
        )

    def test_nvidia(self):
        self.assertEqual(
            gaming_gpu_packages(frozenset({"nvidia"})),
            ["lib32-nvidia-utils", "nvidia-open-dkms", "nvidia-utils"],
        )

    def test_intel(self):
        self.assertEqual(
            gaming_gpu_packages(frozenset({"intel"})),
            ["lib32-vulkan-intel", "vulkan-intel"],
        )

    def test_hybrid_union_sorted(self):
        pkgs = gaming_gpu_packages(frozenset({"intel", "nvidia"}))
        self.assertEqual(pkgs, sorted(pkgs))
        self.assertIn("nvidia-utils", pkgs)
        self.assertIn("vulkan-intel", pkgs)

    def test_empty_falls_back_to_mesa(self):
        pkgs = gaming_gpu_packages(frozenset())
        self.assertIn("vulkan-radeon", pkgs)
        self.assertIn("vulkan-intel", pkgs)
        self.assertNotIn("nvidia-utils", pkgs)

    def test_deterministic(self):
        v = frozenset({"amd", "intel"})
        self.assertEqual(gaming_gpu_packages(v), gaming_gpu_packages(v))


class TestSessionGpuPackages(unittest.TestCase):
    """Driver stack for graphical sessions (mesa always, NVIDIA extras)."""

    def test_mesa_always_present(self):
        from glue_installer.gpu import session_gpu_packages
        self.assertIn("mesa", session_gpu_packages(frozenset()))
        self.assertIn("mesa", session_gpu_packages(frozenset({"intel"})))

    def test_nvidia_gets_module_userspace_and_egl_wayland(self):
        from glue_installer.gpu import session_gpu_packages
        pkgs = session_gpu_packages(frozenset({"nvidia"}))
        self.assertIn("nvidia-open-dkms", pkgs)
        self.assertIn("nvidia-utils", pkgs)
        self.assertIn("egl-wayland", pkgs)

    def test_intel_amd_get_vulkan(self):
        from glue_installer.gpu import session_gpu_packages
        self.assertIn("vulkan-intel", session_gpu_packages(frozenset({"intel"})))
        self.assertIn("vulkan-radeon", session_gpu_packages(frozenset({"amd"})))

    def test_sorted_and_deterministic(self):
        from glue_installer.gpu import session_gpu_packages
        v = frozenset({"nvidia", "intel"})
        self.assertEqual(session_gpu_packages(v), sorted(session_gpu_packages(v)))
        self.assertEqual(session_gpu_packages(v), session_gpu_packages(v))
