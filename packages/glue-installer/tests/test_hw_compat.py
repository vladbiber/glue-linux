"""Unit tests for glue_installer.hw_compat and its wiring into resolve_plan."""

import sys
import tempfile
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.catalog import load_catalog
from glue_installer.gpu import detect_gpu_vendors
from glue_installer.hw_compat import (
    BROADCOM_BLACKLIST_PATH, HardwareProfile, compat_packages, detect_hardware,
    needs_broadcom_wl, nvidia_driver_packages, nvidia_generation)
from glue_installer.plan import Selection, resolve_plan

_CATALOG = load_catalog(_PKG_ROOT / "catalog" / "catalog.json")
_INTEL_CPU = "vendor_id\t: GenuineIntel\n"
_AMD_CPU = "vendor_id\t: AuthenticAMD\n"


def _fake_pci(devices):
    """Fake /sys/bus/pci/devices: [(class, vendor, device), ...]."""
    root = tempfile.mkdtemp()
    for i, (cls, vendor, device) in enumerate(devices):
        d = Path(root) / f"0000:0{i}:00.0"
        d.mkdir()
        (d / "class").write_text(cls + "\n")
        (d / "vendor").write_text(vendor + "\n")
        (d / "device").write_text(device + "\n")
    return root


def _plan(pci, cpu=_INTEL_CPU, gaming=False, sessions=("gluewc",)):
    sel = Selection("linux-cachyos", "runit", list(sessions),
                    {"gluewc": "glueqs"} if "gluewc" in sessions else {},
                    [], gaming, False)
    return resolve_plan(_CATALOG, sel, gpu_vendors=detect_gpu_vendors(pci),
                        hw=detect_hardware(pci, cpu))


def _nv(dev, cls="0x030000"):
    return (cls, "0x10de", dev)


class TestScenarios(unittest.TestCase):
    def test_a_intel_ampere_hybrid(self):
        plan = _plan(_fake_pci([("0x030000", "0x8086", "0x9a49"), _nv("0x2520")]),
                     gaming=True)
        for pkg in ("nvidia-open-dkms", "nvidia-utils", "intel-ucode",
                    "sof-firmware", "alsa-ucm-conf", "fwupd",
                    "libva-nvidia-driver"):
            self.assertIn(pkg, plan.packages)
        self.assertFalse([p for p in plan.packages if "580xx" in p])
        self.assertIn("/usr/local/bin/prime-run", [f.path for f in plan.files])

    def test_b_amd_pascal_gaming(self):
        plan = _plan(_fake_pci([("0x030000", "0x1002", "0x1638"), _nv("0x1c03")]),
                     cpu=_AMD_CPU, gaming=True)
        for pkg in ("nvidia-580xx-dkms", "nvidia-580xx-utils",
                    "lib32-nvidia-580xx-utils", "amd-ucode", "egl-wayland"):
            self.assertIn(pkg, plan.packages)
        for pkg in ("nvidia-open-dkms", "nvidia-utils", "lib32-nvidia-utils",
                    "libva-nvidia-driver"):
            self.assertNotIn(pkg, plan.packages)
        self.assertIn("/usr/local/bin/prime-run", [f.path for f in plan.files])

    def test_pascal_without_gaming_has_no_lib32(self):
        plan = _plan(_fake_pci([_nv("0x1c03")]))
        self.assertNotIn("lib32-nvidia-580xx-utils", plan.packages)

    def test_c_kepler(self):
        plan = _plan(_fake_pci([_nv("0x128b")]), gaming=True)
        self.assertIn("nvidia-470xx-dkms", plan.packages)
        self.assertIn("nvidia-470xx-utils", plan.packages)
        self.assertIn("lib32-nvidia-470xx-utils", plan.packages)
        self.assertNotIn("nvidia-open-dkms", plan.packages)
        self.assertTrue(any("Wayland" in w for w in plan.warnings))

    def test_d_legacy(self):
        plan = _plan(_fake_pci([_nv("0x1040")]), gaming=True)
        self.assertFalse([p for p in plan.packages
                          if "nvidia" in p or p == "egl-wayland"])
        self.assertIn("mesa", plan.packages)
        self.assertTrue(any("too old" in w for w in plan.warnings))
        self.assertNotIn("/usr/local/bin/prime-run", [f.path for f in plan.files])

    def test_e_mixed_turing_pascal(self):
        plan = _plan(_fake_pci([_nv("0x1f82"), _nv("0x1c03", "0x030200")]))
        self.assertIn("nvidia-580xx-dkms", plan.packages)
        self.assertNotIn("nvidia-open-dkms", plan.packages)

    def test_mixed_legacy_wins_over_everything(self):
        hw = HardwareProfile(nvidia_devices=(0x2882, 0x1c03, 0x1040))
        pkgs, warns = compat_packages(hw, True, True)
        self.assertFalse([p for p in pkgs if "nvidia" in p])
        self.assertEqual(len(warns), 1)

    def test_f_broadcom(self):
        plan = _plan(_fake_pci([("0x028000", "0x14e4", "0x43b1")]))
        self.assertIn("broadcom-wl-dkms", plan.packages)
        blk = [f for f in plan.files if f.path == BROADCOM_BLACKLIST_PATH]
        self.assertEqual(len(blk), 1)
        for mod in ("b43", "bcma", "ssb", "brcmsmac"):
            self.assertIn(f"blacklist {mod}\n", blk[0].content)
        plan = _plan(_fake_pci([("0x028000", "0x14e4", "0x43a3")]))
        self.assertNotIn("broadcom-wl-dkms", plan.packages)
        self.assertNotIn(BROADCOM_BLACKLIST_PATH, [f.path for f in plan.files])

    def test_broadcom_ethernet_class_and_other_vendors(self):
        # 0x0200 only counts for Broadcom; a Realtek id equal to a wl id is ignored
        hw = detect_hardware(_fake_pci([("0x020000", "0x14e4", "0x4311"),
                                        ("0x020000", "0x10ec", "0x4311")]), "")
        self.assertEqual(hw.wifi_devices, ((0x14e4, 0x4311),))
        self.assertFalse(needs_broadcom_wl([(0x10ec, 0x4311)]))
        for dev in (0x4727, 0x43a3, 0x4464, 0x43ec):
            self.assertFalse(needs_broadcom_wl([(0x14e4, dev)]))

    def test_g_hw_none_unchanged(self):
        pci = _fake_pci([_nv("0x1c03")])
        for gaming, sessions in ((True, ("gluewc",)), (False, ()), (True, ())):
            sel = Selection("linux-cachyos", "dinit", list(sessions),
                            {"gluewc": "glueqs"} if sessions else {},
                            [], gaming, False)
            kw = dict(gpu_vendors=detect_gpu_vendors(pci))
            base = resolve_plan(_CATALOG, sel, **kw)
            self.assertEqual(base, resolve_plan(_CATALOG, sel, hw=None, **kw))
            self.assertNotIn("fwupd", base.packages)
            self.assertNotIn("intel-ucode", base.packages)
            if gaming or sessions:
                self.assertIn("nvidia-open-dkms", base.packages)
                self.assertNotIn("nvidia-580xx-dkms", base.packages)

    def test_h_server_like(self):
        plan = _plan(_fake_pci([_nv("0x1c03")]), cpu=_AMD_CPU, sessions=())
        self.assertIn("amd-ucode", plan.packages)
        for pkg in ("sof-firmware", "alsa-ucm-conf", "fwupd", "nvidia-580xx-dkms"):
            self.assertNotIn(pkg, plan.packages)

    def test_other_cpu_gets_no_microcode(self):
        plan = _plan(_fake_pci([]), cpu="vendor_id\t: Hygon\n")
        self.assertFalse([p for p in plan.packages if p.endswith("-ucode")])


class TestGenerations(unittest.TestCase):
    def test_i_boundaries(self):
        table = {
            0x1380: "maxwell_pascal", 0x1401: "maxwell_pascal",
            0x1c03: "maxwell_pascal", 0x1d01: "maxwell_pascal",
            0x1340: "maxwell_pascal", 0x1d7f: "maxwell_pascal",
            0x1d81: "volta", 0x1dff: "volta",
            0x1f82: "turing_plus", 0x2520: "turing_plus", 0x2882: "turing_plus",
            0x2f58: "turing_plus", 0x1e00: "turing_plus",
            0x1180: "kepler", 0x128b: "kepler", 0x0fc8: "kepler",
            0x12ff: "kepler", 0x1003: "kepler",
            0x1040: "legacy", 0x1080: "legacy", 0x1200: "legacy",
            0x0000: "legacy", 0x0fbf: "legacy",
            0x1300: "unknown", 0x133f: "unknown", 0x3000: "unknown",
        }
        for dev, gen in table.items():
            self.assertEqual(nvidia_generation(dev), gen, hex(dev))

    def test_driver_packages(self):
        self.assertEqual(nvidia_driver_packages("legacy", True)[0], set())
        pkgs, _ = nvidia_driver_packages("volta", False)
        self.assertIn("nvidia-580xx-utils", pkgs)
        self.assertNotIn("lib32-nvidia-580xx-utils", pkgs)
        self.assertIn("libva-nvidia-driver", nvidia_driver_packages("unknown", False)[0])
        self.assertNotIn("libva-nvidia-driver",
                         nvidia_driver_packages("maxwell_pascal", True)[0])


class TestDetect(unittest.TestCase):
    def test_j_empty_and_unreadable(self):
        empty = HardwareProfile()
        self.assertEqual(detect_hardware("/nonexistent/path", ""), empty)
        self.assertEqual(detect_hardware(tempfile.mkdtemp(), ""), empty)
        root = tempfile.mkdtemp()
        (Path(root) / "0000:00:00.0").mkdir()          # no files at all
        d = Path(root) / "0000:00:01.0"
        d.mkdir()
        (d / "class").write_text("garbage\n")
        (d / "vendor").write_text("0x10de\n")
        (d / "device").write_text("0x1c03\n")
        self.assertEqual(detect_hardware(root, ""), empty)

    def test_audio_function_ignored(self):
        hw = detect_hardware(_fake_pci([_nv("0x10f1", "0x040300"), _nv("0x1c03")]), "")
        self.assertEqual(hw.nvidia_devices, (0x1c03,))

    def test_cpuinfo_none_never_raises(self):
        self.assertIn(detect_hardware("/nonexistent").cpu_vendor,
                      ("intel", "amd", "other"))


class TestNoSystemd(unittest.TestCase):
    def test_k_no_systemd_package(self):
        pcis = [[_nv("0x2520"), ("0x028000", "0x14e4", "0x43b1")],
                [_nv("0x1c03")], [_nv("0x128b")], [_nv("0x1040")], []]
        for devs in pcis:
            for gaming in (True, False):
                plan = _plan(_fake_pci(devs), gaming=gaming)
                self.assertFalse([p for p in plan.packages
                                  if p.startswith("systemd")])


if __name__ == "__main__":
    unittest.main()
