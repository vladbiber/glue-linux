"""calamares_adapter (10.3 lot 1) + the glueinstall Calamares module."""

import importlib.util
import itertools
import re
import sys
import types
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.calamares_adapter import (
    AdapterError, AdapterResult, SELECTION_KEYS, adapter_steps, check_root_mount,
    compile_adapter, parse_selection, swapfile_fstab_line, swapfile_steps)
from glue_installer.catalog import load_catalog
from glue_installer.disks import DiskPlan, PartitionSpec
from glue_installer.executor import RunCommand, WriteTargetFile, compile_steps
from glue_installer.limine import BootSpec
from glue_installer.plan import resolve_plan
from glue_installer.swap import swap_plan

_CATALOG = load_catalog(_PKG_ROOT / "catalog" / "catalog.json")
_MODULE = (_PKG_ROOT.parent / "glue-calamares-config" / "modules" / "glueinstall")
_GIB = 2 ** 30
_MOUNTED = lambda p: True  # noqa: E731
_FORBIDDEN = re.compile(
    r"(?<![\w./-])(fstabgen|mkfs(\.\w+)?|parted|sgdisk|wipefs|mount|umount|useradd|"
    r"passwd|chpasswd|locale-gen|hostnamectl)(?![\w-])")
_SEL_GLUEWC = {"sessions": ["gluewc"], "shell": "glueqs"}
_SEL_KDE = {"sessions": ["kde-plasma"]}
_SEL_GAMING = {"sessions": ["gluewc"], "shell": "glueqs", "gaming": True,
               "scheduler": "scx_lavd"}


def _argv_text(steps):
    return [" ".join(s.argv) for s in steps if isinstance(s, RunCommand)]


def _compile(sel_dict, **kw):
    kw.setdefault("root_mount", "/tmp/calamares-root")
    kw.setdefault("ram_bytes", 16 * _GIB)
    kw.setdefault("is_laptop", False)
    kw.setdefault("firmware", "uefi")
    kw.setdefault("is_mount", _MOUNTED)
    kw.setdefault("root_bytes", 100 * _GIB)
    return compile_adapter(parse_selection(sel_dict, _CATALOG), _CATALOG, **kw)


class TestParseSelection(unittest.TestCase):
    def test_defaults_and_mapping(self):
        sel = parse_selection({"sessions": ["gluewc", "kde-plasma"], "shell": "noctalia-shell",
                               "gaming": True, "scheduler": "scx_bpfland",
                               "bluetooth": False, "swap_mode": "zram"}, _CATALOG)
        self.assertEqual((sel.kernel_id, sel.init_id), ("linux-cachyos", "dinit"))
        self.assertEqual(sel.shell_choice, {"gluewc": "noctalia"})
        self.assertEqual(sel.support_ids, ["app-store"])
        self.assertFalse(sel.minimal)
        self.assertEqual((sel.scheduler, sel.swap_mode, sel.hibernate),
                         ("scx_bpfland", "zram", False))
        self.assertTrue(parse_selection({}, _CATALOG).minimal)

    def test_invalid_values_raise(self):
        bad = [{"init": "systemd"}, {"kernel": "linux-lts"}, {"sessions": ["sway"]},
               {"sessions": ["gluewc"], "shell": "waybar"}, {"sessions": ["gluewc"]},
               {"sessions": ["kde-plasma", "kde-plasma"]}, {"sessions": "gluewc"},
               {"scheduler": "scx_rusty"}, {"swap_mode": "file"}, {"gaming": "yes"},
               {"hibernate": 1}, {"app_store": None}, {"sessions": ["gluewc"], "shell": 3}]
        for d in bad:
            with self.assertRaises(AdapterError, msg=d):
                parse_selection(d, _CATALOG)
        with self.assertRaises(AdapterError):
            parse_selection(["gluewc"], _CATALOG)

    def test_unknown_keys_ignored_without_catalog(self):
        sel = parse_selection({"sessions": ["gluewc"], "shell": "glueqs", "password": "x"})
        self.assertEqual(sel.shell_choice, {"gluewc": "glueqs"})
        self.assertNotIn("password", SELECTION_KEYS)


class TestRootMount(unittest.TestCase):
    def test_refusals(self):
        for bad in ("/", "//", "mnt", "/mnt/../", "/mnt/../x", ""):
            with self.assertRaises(AdapterError, msg=bad):
                check_root_mount(bad, _MOUNTED)
        with self.assertRaises(AdapterError):
            check_root_mount("/tmp/not-mounted", lambda p: False)
        with self.assertRaises(AdapterError):
            _compile(_SEL_KDE, root_mount="/", is_mount=_MOUNTED)
        with self.assertRaises(AdapterError):
            _compile(_SEL_KDE, root_mount="/mnt", is_mount=lambda p: False)
        with self.assertRaises(AdapterError):
            _compile(_SEL_KDE, root_mount="../mnt")

    def test_accepts_mounted_absolute_path(self):
        seen = []
        self.assertEqual(check_root_mount("/tmp/root/", lambda p: seen.append(p) or True),
                         "/tmp/root")
        self.assertEqual(seen, ["/tmp/root"])

    def test_bad_firmware_and_devices(self):
        with self.assertRaises(AdapterError):
            _compile(_SEL_KDE, firmware="efi")
        with self.assertRaises(AdapterError):
            _compile(_SEL_KDE, swap_partition="sda2")
        with self.assertRaises(AdapterError):
            _compile(_SEL_KDE, root_device="/dev/../etc")


class TestForbiddenAndOrder(unittest.TestCase):
    def _combos(self):
        for init, sessions, gaming, swap in itertools.product(
                ("dinit", "runit", "openrc"), (_SEL_GLUEWC, _SEL_KDE, {}),
                (False, True), ("auto", "zram", "none")):
            d = dict(sessions, init=init, gaming=gaming, swap_mode=swap)
            yield d, _compile(d)

    def test_no_forbidden_tools_and_deploy_last(self):
        for d, result in self._combos():
            for text in _argv_text(result.steps):
                self.assertIsNone(_FORBIDDEN.search(text), (d, text))
            last = result.steps[-1]
            self.assertIn("glue-boot-update --deploy", last.argv[-1], d)
            self.assertEqual(last.argv[:4], ["artix-chroot", "/tmp/calamares-root", "sh", "-c"])
            self.assertEqual(sum("glue-boot-update" in t for t in _argv_text(result.steps)), 1)

    def test_order_bootstrap_config_swapfile_boot(self):
        for d, result in self._combos():
            kinds = []
            for s in result.steps:
                text = " ".join(getattr(s, "argv", ()))
                if text.startswith("pacman-key"):
                    kinds.append("keyring")
                elif text.startswith("basestrap"):
                    kinds.append("bootstrap")
                elif isinstance(s, WriteTargetFile) or "Enable service" in s.description \
                        or "ModemManager" in s.description:
                    kinds.append("config")
                elif "swapfile" in text:
                    kinds.append("swapfile")
                elif "glue-boot-update" in text:
                    kinds.append("boot")
                else:
                    self.fail(f"unclassified step {s.description!r} for {d}")
            order = [k for k, _ in itertools.groupby(kinds)]
            expect = ["keyring", "bootstrap", "config"]
            if d["swap_mode"] == "auto":
                expect.append("swapfile")
                self.assertEqual(result.swapfile_path, "/tmp/calamares-root/swapfile", d)
            else:
                self.assertIsNone(result.swapfile_path, d)
            self.assertEqual(order, expect + ["boot"], d)

    def test_swapfile_steps_without_fstab_or_swapon(self):
        steps = swapfile_steps("/mnt/", 2048, "btrfs")
        self.assertEqual([s.argv[0] for s in steps],
                         ["truncate", "chattr", "fallocate", "chmod", "mkswap"])
        self.assertTrue(all(s.argv[-1] == "/mnt/swapfile" for s in steps))
        self.assertEqual([s.argv[0] for s in swapfile_steps("/mnt", 1)],
                         ["truncate", "fallocate", "chmod", "mkswap"])
        self.assertEqual(swapfile_steps("/mnt", 0), [])
        self.assertEqual(swapfile_fstab_line(), "/swapfile none swap defaults 0 0\n")

    def test_swap_partition_from_calamares_means_no_swapfile_and_resume(self):
        r = _compile(dict(_SEL_KDE, hibernate=True), swap_partition="/dev/sda2")
        self.assertIsNone(r.swapfile_path)
        self.assertIn("blkid -s UUID -o value /dev/sda2", r.steps[-1].argv[-1])
        self.assertNotIn("swapfile", " ".join(_argv_text(r.steps)))
        r2 = _compile(dict(_SEL_KDE, hibernate=True))
        self.assertNotIn("RESUME", r2.steps[-1].argv[-1].split("GLUE_BOOT_RESUME_UUID=")[1][:1])
        self.assertTrue(any("Hibernation disabled" in w for w in r2.warnings))
        r3 = _compile(_SEL_KDE, ram_bytes=0)
        self.assertIsNone(r3.swapfile_path)

    def test_bios_has_no_efibootmgr_and_small_root_has_no_swapfile(self):
        r = _compile(_SEL_KDE, firmware="bios", root_bytes=0)
        basestrap = next(s for s in r.steps if s.argv[0] == "basestrap")
        self.assertNotIn("efibootmgr", basestrap.argv)
        self.assertIn("limine", basestrap.argv)
        self.assertIsNone(r.swapfile_path)

    def test_other_os_fragments_become_plan_files_before_boot(self):
        from glue_installer.osdetect import DetectResult, ForeignOS
        det = DetectResult(entries=[ForeignOS(
            title="Windows Boot Manager", device="/dev/sda1", partuuid="deadbeef-0000",
            fs_uuid="", disk="/dev/sda", kind="efi", loader="/EFI/Microsoft/Boot/bootmgfw.efi")])
        r = _compile(_SEL_KDE, other_os=det)
        idx = next(i for i, s in enumerate(r.steps) if isinstance(s, WriteTargetFile)
                   and s.path.endswith("/etc/glue/boot.d/10-windows-boot-manager-deadbeef.conf"))
        self.assertLess(idx, len(r.steps) - 1)

    def test_adapter_steps_returns_the_same_list(self):
        sel = parse_selection(_SEL_KDE, _CATALOG)
        kw = dict(root_mount="/mnt", ram_bytes=8 * _GIB, is_laptop=True, firmware="uefi",
                  is_mount=_MOUNTED, boot_spec=BootSpec(kernel="linux-cachyos"))
        self.assertEqual(adapter_steps(sel, _CATALOG, **kw),
                         compile_adapter(sel, _CATALOG, **kw).steps)
        self.assertIsInstance(compile_adapter(sel, _CATALOG, **kw), AdapterResult)


class TestParityWithTui(unittest.TestCase):
    """basestrap package set == resolve_plan/compile_steps for the same Selection."""

    def _tui_steps(self, sel, firmware, ram, root_bytes):
        swap = swap_plan(ram, root_bytes, False, "zram" if sel.swap_mode == "auto"
                         else sel.swap_mode)
        plan = resolve_plan(_CATALOG, sel, swap=swap, ram_bytes=ram)
        boot = PartitionSpec(1, "/dev/sda1", "ef00", "+512M", "vfat", "/boot")
        root = PartitionSpec(2, "/dev/sda2", "8300", "0", "ext4", "/")
        parts = (boot, root) if firmware == "uefi" else (root,)
        disk = DiskPlan("/dev/sda", firmware, parts, mode="existing")
        return plan, compile_steps(plan, target="/mnt", init_id=sel.init_id, disk_plan=disk)

    def test_packages_and_config_identical(self):
        for d in (_SEL_GLUEWC, _SEL_KDE, _SEL_GAMING,
                  dict(_SEL_GAMING, init="runit", swap_mode="none"),
                  dict(_SEL_KDE, init="openrc")):
            for firmware in ("uefi", "bios"):
                sel = parse_selection(d, _CATALOG)
                r = _compile(d, root_mount="/mnt", firmware=firmware, swap_partition="/dev/sda3")
                plan, tui = self._tui_steps(sel, firmware, 16 * _GIB, 100 * _GIB)
                mine = next(s for s in r.steps if s.argv[0] == "basestrap")
                theirs = next(s for s in tui if s.argv[0] == "basestrap")
                self.assertEqual(mine.argv, theirs.argv, (d, firmware))
                self.assertEqual(sorted(r.packages), mine.argv[2:])
                self.assertTrue(set(plan.packages) <= set(mine.argv), (d, firmware))
                # configuration part (files + chrooted service enables) identical
                cfg = lambda steps: [s for s in steps if isinstance(s, WriteTargetFile)  # noqa: E731
                                     or (s.argv[0] == "artix-chroot"
                                         and "glue-boot-update" not in s.argv[-1])]
                self.assertEqual(cfg(r.steps), cfg(tui), (d, firmware))


class _FakeGS:
    def __init__(self, data):
        self.data = dict(data)

    def value(self, key):
        return self.data.get(key)

    def insert(self, key, value):
        self.data[key] = value


def _fake_calamares(gs_data, config=None):
    cala = types.ModuleType("libcalamares")
    cala.globalstorage = _FakeGS(gs_data)
    cala.job = types.SimpleNamespace(configuration=config or {}, progress=[],
                                     setprogress=lambda v: cala.job.progress.append(v))
    cala.log = []
    cala.utils = types.SimpleNamespace(debug=cala.log.append, warning=cala.log.append)
    return cala


def _load_module():
    spec = importlib.util.spec_from_file_location("glueinstall_main", _MODULE / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestGlueinstallModule(unittest.TestCase):
    FACTS = dict(ram_bytes=8 * _GIB, is_laptop=False, gpu_vendors=None, cpu_v3=False,
                 cpu_vendor_id="other", amd_pstate_active=False)

    def setUp(self):
        self.mod = _load_module()
        self.mod._root_bytes = lambda p: 100 * _GIB
        import glue_installer.calamares_adapter as adapter
        self._orig = adapter.os.path.ismount
        adapter.os.path.ismount = lambda p: p == "/tmp/calamares-root"

    def tearDown(self):
        import glue_installer.calamares_adapter as adapter
        adapter.os.path.ismount = self._orig
        sys.modules.pop("libcalamares", None)

    def _run(self, gs, config=None):
        cala = _fake_calamares(gs, config)
        sys.modules["libcalamares"] = cala
        ran = []

        def fake_execute(steps, *, dry_run, progress, output_path):
            for i, s in enumerate(steps):
                progress(i, len(steps), s.description)
                ran.append(s)
        return self.mod.run(execute_steps=fake_execute, facts=dict(self.FACTS)), ran, cala

    def test_runs_steps_in_order_and_hides_canary(self):
        gs = {"rootMountPoint": "/tmp/calamares-root", "firmwareType": "efi",
              "partitions": [{"device": "/dev/sda1", "mountPoint": "/boot", "fs": "fat32"},
                             {"device": "/dev/sda2", "mountPoint": "/", "fs": "ext4"}],
              "glue_selection": dict(_SEL_GAMING, init="runit", password="hunter2",
                                     rootPassword="hunter2")}
        result, ran, cala = self._run(gs, {"log_file": "/tmp/x.log", "pacman_conf": "/nope"})
        self.assertIsNone(result)
        self.assertEqual(ran[0].argv[0], "pacman-key")
        self.assertEqual(ran[2].argv[0], "basestrap")
        self.assertIn("glue-boot-update --deploy", ran[-1].argv[-1])
        self.assertEqual(cala.job.progress[-1], 1.0)
        self.assertEqual(cala.globalstorage.value("glue_swapfile_fstab_line"),
                         "/swapfile none swap defaults 0 0\n")
        blob = " ".join(" ".join(getattr(s, "argv", ())) + getattr(s, "content", "")
                        for s in ran) + " ".join(cala.log)
        self.assertNotIn("hunter2", blob)
        self.assertTrue(any("basestrap" in line for line in cala.log))

    def test_adapter_error_becomes_title_message(self):
        gs = {"rootMountPoint": "/", "glue_selection": {"init": "systemd", "pw": "hunter2"}}
        result, ran, cala = self._run(gs)
        self.assertEqual(ran, [])
        self.assertEqual(result[0], "Glue Linux configuration rejected")
        self.assertIn("systemd", result[1])
        self.assertNotIn("hunter2", result[1] + " ".join(cala.log))
        result, ran, _ = self._run({"rootMountPoint": "/", "glue_selection": {}})
        self.assertIn("refusing", result[1])

    def test_missing_root_mount_is_a_clear_error(self):
        for gs in ({"glue_selection": _SEL_KDE}, {"rootMountPoint": None, "glue_selection": _SEL_KDE},
                   {"rootMountPoint": "", "glue_selection": _SEL_KDE}):
            result, ran, _ = self._run(gs)
            self.assertEqual(ran, [])
            self.assertEqual(result[0], "Glue Linux configuration rejected")
            self.assertIn("rootMountPoint is missing", result[1])
        with self.assertRaises(AdapterError) as ctx:
            check_root_mount(None, _MOUNTED)
        self.assertIn("missing", str(ctx.exception))

    def test_selection_from_packagechooser_pages(self):
        gs = {"rootMountPoint": "/tmp/calamares-root", "firmwareType": "efi",
              "partitions": [{"device": "/dev/sda1", "mountPoint": "/boot", "fs": "fat32"},
                             {"device": "/dev/sda2", "mountPoint": "/", "fs": "ext4"}],
              "packagechooser_gluesessions": "gluewc,kde-plasma",
              "packagechooser_glueshell": "noctalia", "packagechooser_gluekernel": "linux-cachyos",
              "packagechooser_glueinit": "openrc", "packagechooser_gluegaming": "gaming-scx_lavd",
              "packagechooser_glueextras": "app-store,bluetooth"}
        result, ran, _ = self._run(gs, {"selection_file": "/nonexistent.json"})
        self.assertIsNone(result)
        basestrap = next(s for s in ran if s.argv[0] == "basestrap").argv
        for pkg in ("noctalia-shell", "plasma-meta", "steam", "scx-scheds", "bluez", "openrc"):
            self.assertIn(pkg, basestrap, pkg)
        self.assertNotIn("glueqs", basestrap)
        result, ran, _ = self._run(dict(gs, packagechooser_gluekernel="linux-rt"))
        self.assertEqual(ran, [])
        self.assertIn("unknown choice", result[1])

    def test_selection_file_fallback_and_module_desc(self):
        import json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(_SEL_KDE, fh)
        gs = {"rootMountPoint": "/tmp/calamares-root", "firmwareType": "bios",
              "partitions": [{"device": "/dev/sda1", "mountPoint": "/", "fs": "btrfs"},
                             {"device": "/dev/sda2", "mountPoint": "", "fs": "linuxswap"}]}
        result, ran, _ = self._run(gs, {"selection_file": fh.name})
        self.assertIsNone(result)
        self.assertNotIn("swapfile", " ".join(_argv_text(ran)))
        self.assertNotIn("efibootmgr", next(s for s in ran if s.argv[0] == "basestrap").argv)
        desc = (_MODULE / "module.desc").read_text()
        for line in ('type: "job"', 'interface: "python"', 'name: "glueinstall"',
                     'script: "main.py"'):
            self.assertIn(line, desc)
        self.assertIn("selection_file: /etc/glue/selection.json",
                      (_MODULE.parent / "glueinstall.conf").read_text())
        self.assertNotIn("import libcalamares",
                         (_PKG_ROOT / "glue_installer" / "calamares_adapter.py").read_text())


if __name__ == "__main__":
    unittest.main()
