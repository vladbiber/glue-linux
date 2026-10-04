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
    VMD_DROPIN_PATH, ensure_microcode_hook, hw_files, microcode_hook_steps,
    needs_broadcom_wl, nvidia_driver_packages, nvidia_generation, storage_hint)
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


def _sysfs(devices, vmd_driver_devs=()):
    """<root>/devices/* plus <root>/drivers/vmd/<addr> symlinks."""
    root = Path(tempfile.mkdtemp())
    (root / "devices").mkdir()
    for i, (cls, vendor, device) in enumerate(devices):
        d = root / "devices" / f"0000:0{i}:00.0"
        d.mkdir()
        (d / "class").write_text(cls + "\n")
        (d / "vendor").write_text(vendor + "\n")
        (d / "device").write_text(device + "\n")
    if vmd_driver_devs is not None:
        (root / "drivers" / "vmd").mkdir(parents=True)
        for addr in vmd_driver_devs:
            (root / "drivers" / "vmd" / addr).symlink_to(root / "devices")
    return str(root / "devices")


class TestVmdAndStorage(unittest.TestCase):
    def _paths(self, hw):
        return [f.path for f in hw_files(hw)]

    def test_vmd_by_pci_id(self):
        hw = detect_hardware(_sysfs([("0x010400", "0x8086", "0x9a0b")], None), _INTEL_CPU)
        self.assertTrue(hw.vmd_present)
        self.assertFalse(hw.vmd_driver)
        self.assertEqual(self._paths(hw).count(VMD_DROPIN_PATH), 1)
        f = [f for f in hw_files(hw) if f.path == VMD_DROPIN_PATH][0]
        self.assertIn("MODULES+=(vmd)", f.content)
        self.assertEqual(f.mode, 0o644)

    def test_vmd_by_driver_symlink_only(self):
        hw = detect_hardware(_sysfs([("0x060000", "0x8086", "0x1234")],
                                    ["0000:00:0e.0"]), _INTEL_CPU)
        self.assertTrue(hw.vmd_driver)
        self.assertEqual(self._paths(hw), [VMD_DROPIN_PATH])

    def test_driver_dir_without_devices_is_not_vmd(self):
        hw = detect_hardware(_sysfs([("0x060000", "0x8086", "0x1234")], []), _INTEL_CPU)
        self.assertFalse(hw.vmd_present)
        self.assertEqual(self._paths(hw), [])

    def test_vmd_id_from_other_vendor_ignored(self):
        hw = detect_hardware(_sysfs([("0x010400", "0x1002", "0x9a0b")], None), "")
        self.assertFalse(hw.vmd_present)

    def test_amd_and_nvme_only_have_no_dropin(self):
        for devs in ([("0x030000", "0x1002", "0x1638")],
                     [("0x010802", "0x144d", "0xa808")]):
            hw = detect_hardware(_sysfs(devs, None), _AMD_CPU)
            self.assertEqual(self._paths(hw), [])
            self.assertFalse(hw.intel_raid)

    def test_intel_raid_class(self):
        hw = detect_hardware(_sysfs([("0x010400", "0x8086", "0x2822")], None), _INTEL_CPU)
        self.assertTrue(hw.intel_raid)
        self.assertFalse(hw.vmd_present)
        self.assertEqual(self._paths(hw), [])
        hw = detect_hardware(_sysfs([("0x010400", "0x1000", "0x0079")], None), "")
        self.assertFalse(hw.intel_raid)

    def test_plan_has_dropin_once_and_hw_none_unchanged(self):
        pci = _sysfs([("0x030000", "0x8086", "0x9a49"), ("0x010400", "0x8086", "0x467f")], None)
        plan = _plan(pci)
        self.assertEqual([f.path for f in plan.files].count(VMD_DROPIN_PATH), 1)
        sel = Selection("linux-cachyos", "runit", ["gluewc"], {"gluewc": "glueqs"},
                        [], False, False)
        a = resolve_plan(_CATALOG, sel, hw=None)
        b = resolve_plan(_CATALOG, sel)
        self.assertEqual(a, b)
        self.assertNotIn(VMD_DROPIN_PATH, [f.path for f in a.files])
        self.assertEqual(resolve_plan(_CATALOG, sel, hw=HardwareProfile()).files, a.files)

    def test_storage_hint(self):
        vmd = HardwareProfile(vmd_devices=(0x9a0b,))
        raid = HardwareProfile(intel_raid=True)
        plain = HardwareProfile()
        self.assertIsNone(storage_hint(True, vmd))
        self.assertIsNone(storage_hint(True, plain))
        for hw in (vmd, raid, HardwareProfile(vmd_driver=True)):
            text = storage_hint(False, hw)
            self.assertIn("AHCI", text)
            self.assertIn("Windows", text)
            self.assertIn("Safe Mode", text)
            self.assertIn("bcdedit", text)
        generic = storage_hint(False, plain)
        self.assertIsNotNone(generic)
        self.assertNotIn("AHCI", generic)
        self.assertIn("No disk", generic)

    def test_report_no_disks_prints_instead_of_skipping(self):
        import io
        from glue_installer.run_ui import report_no_disks
        out, waited = io.StringIO(), []
        text = report_no_disks(HardwareProfile(intel_raid=True), out, waited.append)
        self.assertIn("AHCI", out.getvalue())
        self.assertEqual(text, storage_hint(False, HardwareProfile(intel_raid=True)))
        self.assertEqual(len(waited), 1)


class TestMicrocodeHook(unittest.TestCase):
    def test_table(self):
        full = "HOOKS=(base udev autodetect microcode modconf kms block filesystems fsck)"
        cases = [
            (full, full),
            ("HOOKS=(base udev autodetect modconf kms block filesystems fsck)", full.replace(" kms block", " kms block")),
            ("HOOKS=(base systemd autodetect modconf sd-vconsole block filesystems fsck)",
             "HOOKS=(base systemd autodetect microcode modconf sd-vconsole block filesystems fsck)"),
            ("HOOKS=(base udev autodetect)", "HOOKS=(base udev autodetect microcode)"),
            ("HOOKS=(base udev block)", "HOOKS=(base udev block)"),
            ("HOOKS=()", "HOOKS=()"),
            ("", ""),
        ]
        for before, after in cases:
            self.assertEqual(ensure_microcode_hook(before), after, before)
            self.assertEqual(ensure_microcode_hook(after), after)

    def test_resume_order_kept(self):
        line = "HOOKS=(base udev autodetect modconf block filesystems resume fsck)"
        out = ensure_microcode_hook(line)
        self.assertIn("autodetect microcode modconf", out)
        self.assertIn("filesystems resume", out)

    def test_steps_only_with_ucode(self):
        self.assertEqual(microcode_hook_steps(["linux"], "/mnt"), [])
        steps = microcode_hook_steps(["linux", "amd-ucode"], "/mnt/")
        self.assertEqual(steps[0].argv[:3], ["artix-chroot", "/mnt", "sh"])
        self.assertIn("grep -Eq", steps[0].argv[4])

    def test_compile_steps_include_it_with_hw(self):
        from glue_installer.executor import compile_steps
        plan = _plan(_fake_pci([("0x030000", "0x8086", "0x9a49")]))
        steps = compile_steps(plan, init_id="runit")
        n = sum(1 for s in steps if "microcode" in str(getattr(s, "argv", "")))
        self.assertEqual(n, 1)


    def test_vmd_dropin_forces_rebuild_even_if_hook_present(self):
        from glue_installer.plan import PlannedFile
        from glue_installer.hw_compat import VMD_DROPIN, VMD_DROPIN_PATH
        dropin = [PlannedFile(VMD_DROPIN_PATH, VMD_DROPIN, 0o644)]
        steps = microcode_hook_steps(["linux", "intel-ucode"], "/mnt", dropin)
        self.assertEqual(len(steps), 1)
        self.assertIn("rebuild=1", steps[0].argv[4])
        self.assertIn("mkinitcpio -P", steps[0].argv[4])
        # VMD without ucode: rebuild only, no hook edit
        only = microcode_hook_steps(["linux"], "/mnt", dropin)
        self.assertEqual(len(only), 1)
        self.assertIn("mkinitcpio -P", only[0].argv[4])
        self.assertNotIn("sed", only[0].argv[4])
        # ucode without VMD: rebuild only when the hook was added
        plain = microcode_hook_steps(["amd-ucode"], "/mnt", [])
        self.assertIn("rebuild=0", plain[0].argv[4])

    def test_rebuild_step_runs_after_plan_files_are_written(self):
        from glue_installer.executor import compile_steps
        plan = _plan(_fake_pci([("0x010802", "0x8086", "0x9a0b")]))
        plan.files.append(__import__("glue_installer.plan", fromlist=["x"]).PlannedFile(
            VMD_DROPIN_PATH, "MODULES+=(vmd)\n", 0o644))
        steps = compile_steps(plan, init_id="runit")
        paths = [getattr(s, "path", "") for s in steps]
        w = max(i for i, s in enumerate(steps)
                if str(getattr(s, "path", "")).endswith("glue-vmd.conf"))
        r = [i for i, s in enumerate(steps)
             if "mkinitcpio -P" in str(getattr(s, "argv", ""))]
        self.assertEqual(len(r), 1)
        self.assertGreater(r[0], w)


class TestMainNoDisks(unittest.TestCase):
    def _run(self, found, args=()):
        from unittest import mock
        import glue_installer.__main__ as m
        calls = []

        class Stop(Exception):
            pass

        with mock.patch.object(m, "discover", return_value=found), \
             mock.patch.object(m, "discover_partitions", return_value=[]), \
             mock.patch("glue_installer.run_ui.report_no_disks",
                        side_effect=lambda hw: calls.append(hw)), \
             mock.patch("glue_installer.tui.run_tui", side_effect=Stop):
            try:
                m.main(["--dry-run", *args])
            except Stop:
                pass
            except SystemExit:
                pass
        return calls

    def test_reported_when_discovery_finds_no_disks(self):
        self.assertEqual(len(self._run([])), 1)

    def test_not_reported_with_disks(self):
        self.assertEqual(self._run(["disk"]), [])

    def test_not_reported_when_discovery_fails(self):
        from unittest import mock
        import glue_installer.__main__ as m
        from glue_installer.disks import DiskError
        calls = []
        with mock.patch.object(m, "discover", side_effect=DiskError("x")), \
             mock.patch("glue_installer.run_ui.report_no_disks",
                        side_effect=lambda hw: calls.append(hw)), \
             mock.patch("glue_installer.tui.run_tui", side_effect=SystemExit):
            try:
                m.main(["--dry-run"])
            except SystemExit:
                pass
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
