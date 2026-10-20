"""Encrypted root: Calamares partitions -> boot.conf -> cmdline + mkinitcpio
hooks, the gluecrypt/gluefstab jobs and the partition.conf switch. sed and
glue-boot-update run on temporary files only."""

import importlib.util
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.calamares_adapter import compile_adapter, parse_selection
from glue_installer.catalog import load_catalog
from glue_installer.crypt import (
    ENCRYPT_HOOK_SCRIPT, CryptError, CryptRoot, crypt_layout, crypt_warnings,
    drop_fstab_specs, ensure_encrypt_hook, rebuild_steps)
from glue_installer.limine import BootSpec, boot_conf, kernel_cmdline, limine_conf

_CATALOG = load_catalog(_PKG_ROOT / "catalog" / "catalog.json")
_CFG = _PKG_ROOT.parent / "glue-calamares-config"
_UPDATE = _PKG_ROOT.parent / "glue-boot" / "glue-boot-update"
_ROOT_UUID = "3f1c2a9e-7b4d-4c1e-9a6f-0d2e8b5c1a77"
_LUKS_UUID = "0b1c2d3e-4f50-4617-8899-aabbccddeeff"
_RESUME_UUID = "b7e0d2c4-5a6f-4e8b-8c1d-2f3a4b5c6d7e"
_NAME = f"luks-{_LUKS_UUID}"
_CRYPT = BootSpec("linux-cachyos", crypt_device="/dev/sda2", crypt_name=_NAME)
_GIB = 2 ** 30
_DEFAULT_HOOKS = ("HOOKS=(base udev autodetect microcode modconf kms keyboard keymap "
                  "consolefont block filesystems fsck)")


def _erase_encrypted(swap=True):
    """globalstorage `partitions` of Erase disk + Encrypt system (Calamares 3.4)."""
    parts = [{"device": "/dev/sda1", "mountPoint": "/boot", "fs": "fat32", "fsName": "fat32",
              "claimed": True, "uuid": "ABCD-1234"},
             {"device": "/dev/sda2", "mountPoint": "/", "fs": "ext4", "fsName": "ext4",
              "claimed": True, "uuid": _ROOT_UUID, "luksMapperName": _NAME,
              "luksUuid": _LUKS_UUID, "luksPassphrase": "hunter2"}]
    if swap:
        parts.append({"device": "/dev/sda3", "mountPoint": "", "fs": "linuxswap",
                      "claimed": True, "uuid": "", "luksMapperName": "luks-swap0",
                      "luksUuid": "1111", "luksPassphrase": "hunter2"})
    return parts


class LayoutTest(unittest.TestCase):
    def test_erase_with_encryption(self):
        layout = crypt_layout(_erase_encrypted())
        self.assertEqual(layout.root, CryptRoot("/dev/sda2", _NAME))
        self.assertEqual(layout.dropped_swap, ["/dev/sda3"])
        self.assertEqual(layout.dropped_fstab, ["/dev/mapper/luks-swap0"])
        self.assertIn("/dev/sda3", crypt_warnings(layout)[0])

    def test_without_encryption_nothing(self):
        plain = [{"device": "/dev/sda1", "mountPoint": "/boot", "fs": "fat32"},
                 {"device": "/dev/sda2", "mountPoint": "/", "fs": "ext4"},
                 {"device": "/dev/sda3", "mountPoint": "", "fs": "linuxswap", "claimed": True}]
        for parts in (plain, [], None, ["junk"]):
            layout = crypt_layout(parts)
            self.assertIsNone(layout.root)
            self.assertEqual((layout.dropped_swap, crypt_warnings(layout)), ([], []))

    def test_foreign_luks_and_plain_root_ignored(self):
        parts = [{"device": "/dev/sda1", "mountPoint": "/boot", "fs": "fat32"},
                 {"device": "/dev/sda2", "mountPoint": "/", "fs": "ext4"},
                 {"device": "/dev/sdb1", "mountPoint": "", "fs": "ext4", "luksMapperName": "luks-x"},
                 {"device": "/dev/sdb2", "mountPoint": "", "fs": "linuxswap",
                  "luksMapperName": "luks-y", "claimed": True}]
        layout = crypt_layout(parts)
        self.assertIsNone(layout.root)
        self.assertEqual(layout.dropped_fstab, [])
        unclaimed = _erase_encrypted()
        unclaimed[2]["claimed"] = False
        self.assertEqual(crypt_layout(unclaimed).dropped_swap, [])

    def test_unbootable_layouts_refused_without_secrets(self):
        home = _erase_encrypted(swap=False) + [
            {"device": "/dev/sda4", "mountPoint": "/home", "fs": "ext4",
             "luksMapperName": "luks-home", "luksPassphrase": "hunter2"}]
        boot = _erase_encrypted(swap=False)
        boot[0]["luksMapperName"] = "luks-boot"
        no_boot = _erase_encrypted(swap=False)[1:]
        bad_name = _erase_encrypted(swap=False)
        bad_name[1]["luksMapperName"] = "a b:c"
        for parts, needle in ((home, "/home"), (boot, "/boot partition must not"),
                              (no_boot, "separate, unencrypted /boot"),
                              (bad_name, "mapper name")):
            with self.assertRaises(CryptError) as cm:
                crypt_layout(parts)
            self.assertIn(needle, str(cm.exception))
            self.assertNotIn("hunter2", str(cm.exception))
            self.assertTrue(issubclass(CryptError, ValueError))


def _sed_hooks(line):
    with tempfile.TemporaryDirectory() as tmp:
        conf = Path(tmp) / "mkinitcpio.conf"
        conf.write_text(f"MODULES=()\n{line}\nCOMPRESSION=zstd\n")
        script = ENCRYPT_HOOK_SCRIPT.replace("f=/etc/mkinitcpio.conf", f"f={conf}")
        r = subprocess.run(["sh", "-ec", script], capture_output=True, text=True)
        return r, conf.read_text().splitlines()[1]


class HookTest(unittest.TestCase):
    CASES = (
        _DEFAULT_HOOKS,
        "HOOKS=(base udev autodetect modconf block filesystems fsck)",
        "HOOKS=(base udev keymap autodetect block filesystems)",
        "HOOKS=(base udev keyboard block encrypt filesystems fsck)",
        "HOOKS=(base udev block filesystems resume fsck)",
    )

    def test_python_rule(self):
        self.assertEqual(ensure_encrypt_hook(_DEFAULT_HOOKS),
                         "HOOKS=(base udev autodetect microcode modconf kms keyboard keymap "
                         "consolefont block encrypt filesystems fsck)")
        self.assertEqual(ensure_encrypt_hook(self.CASES[1]),
                         "HOOKS=(base udev autodetect modconf block keyboard keymap encrypt "
                         "filesystems fsck)")
        for line in self.CASES:
            once = ensure_encrypt_hook(line)
            self.assertEqual(ensure_encrypt_hook(once), once)
            self.assertLess(once.index("encrypt"), once.index("filesystems"))
        self.assertEqual(ensure_encrypt_hook("HOOKS=(base udev)"), "HOOKS=(base udev)")
        self.assertEqual(ensure_encrypt_hook("#HOOKS=(filesystems)"), "#HOOKS=(filesystems)")

    def test_shell_sed_matches_python(self):
        for line in self.CASES:
            r, out = _sed_hooks(line)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(out, ensure_encrypt_hook(line), line)
            self.assertEqual(_sed_hooks(out)[1], out)

    def test_shell_fails_without_filesystems(self):
        r, out = _sed_hooks("HOOKS=(base udev block)")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no filesystems hook", r.stderr)


class CmdlineTest(unittest.TestCase):
    def test_cryptdevice_replaces_root_uuid(self):
        cmd = kernel_cmdline(_CRYPT, _ROOT_UUID, crypt_uuid=_LUKS_UUID)
        self.assertTrue(cmd.startswith(f"cryptdevice=UUID={_LUKS_UUID}:{_NAME} "
                                       f"root=/dev/mapper/{_NAME} rw quiet"))
        self.assertNotIn("root=UUID", cmd)
        with self.assertRaises(ValueError):
            kernel_cmdline(_CRYPT, _ROOT_UUID)
        with self.assertRaises(ValueError):
            kernel_cmdline(BootSpec("linux", crypt_device="/dev/sda2", crypt_name="a:b"),
                           _ROOT_UUID, crypt_uuid=_LUKS_UUID)
        with self.assertRaises(ValueError):
            kernel_cmdline(BootSpec("linux", crypt_device="sda2", crypt_name=_NAME),
                           _ROOT_UUID, crypt_uuid=_LUKS_UUID)

    def test_boot_conf_keys_only_when_encrypted(self):
        text = boot_conf(_CRYPT, "uefi", _ROOT_UUID, crypt_uuid=_LUKS_UUID)
        self.assertIn(f"GLUE_BOOT_CRYPT_UUID={_LUKS_UUID}\nGLUE_BOOT_CRYPT_NAME={_NAME}\n", text)
        self.assertNotIn("CRYPT", boot_conf(BootSpec("linux-cachyos"), "uefi", _ROOT_UUID))


class BootUpdateParityTest(unittest.TestCase):
    """glue-boot-update --print == limine_conf() with crypt and cmdline.d."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        boot = self.root / "boot"
        boot.mkdir()
        for name in ("vmlinuz-linux-cachyos", "initramfs-linux-cachyos.img",
                     "initramfs-linux-cachyos-fallback.img"):
            (boot / name).write_bytes(b"x")
        (self.root / "etc" / "glue").mkdir(parents=True)

    def run_print(self, text):
        (self.root / "etc/glue/boot.conf").write_text(text)
        r = subprocess.run(["sh", str(_UPDATE), "--root", str(self.root), "--print"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def dropin(self, name, text):
        d = self.root / "etc/glue/cmdline.d"
        d.mkdir(exist_ok=True)
        (d / name).write_text(text)

    def test_encrypted_root_uefi_and_bios(self):
        for fw in ("uefi", "bios"):
            out = self.run_print(boot_conf(_CRYPT, fw, _ROOT_UUID, crypt_uuid=_LUKS_UUID))
            self.assertEqual(out, limine_conf(_CRYPT, _ROOT_UUID, firmware=fw,
                                              crypt_uuid=_LUKS_UUID))
            self.assertIn(f"cmdline: cryptdevice=UUID={_LUKS_UUID}:{_NAME} "
                          f"root=/dev/mapper/{_NAME} ", out)

    def test_encrypted_root_with_resume(self):
        spec = BootSpec("linux-cachyos", ("amd_pstate=active",), True,
                        crypt_device="/dev/sda2", crypt_name=_NAME)
        out = self.run_print(boot_conf(spec, "uefi", _ROOT_UUID, _RESUME_UUID, _LUKS_UUID))
        self.assertEqual(out, limine_conf(spec, _ROOT_UUID, _RESUME_UUID, crypt_uuid=_LUKS_UUID))

    def test_crypt_name_required(self):
        (self.root / "etc/glue/boot.conf").write_text(
            boot_conf(BootSpec("linux-cachyos"), "uefi", _ROOT_UUID)
            + f"GLUE_BOOT_CRYPT_UUID={_LUKS_UUID}\n")
        r = subprocess.run(["sh", str(_UPDATE), "--root", str(self.root), "--print"],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("GLUE_BOOT_CRYPT_NAME", r.stderr)

    def test_cmdline_d_order_comments_and_whitespace(self):
        files = {
            "50-glue-security.conf": "# glue-security hardened\nslab_nomerge init_on_alloc=1\n"
                                     "\n\tpage_alloc.shuffle=1   # trailing\n",
            "10-a.conf": "lockdown=integrity",
            "9-b.conf": "  # only a comment\n",
            "A-upper.conf": "\n\n",
            "README": "not=read",
            "60-x.conf.disabled": "not=read",
        }
        for name, text in files.items():
            self.dropin(name, text)
        texts = tuple(files[n] for n in sorted(n for n in files if n.endswith(".conf")))
        for spec, crypt in ((BootSpec("linux-cachyos"), None), (_CRYPT, _LUKS_UUID)):
            out = self.run_print(boot_conf(spec, "uefi", _ROOT_UUID, crypt_uuid=crypt))
            self.assertEqual(out, limine_conf(spec, _ROOT_UUID, crypt_uuid=crypt,
                                              cmdline_d=texts))
            self.assertIn("zswap.enabled=0 lockdown=integrity slab_nomerge init_on_alloc=1 "
                          "page_alloc.shuffle=1\n", out)
            self.assertNotIn("not=read", out)

    def test_empty_cmdline_d_changes_nothing(self):
        (self.root / "etc/glue/cmdline.d").mkdir()
        out = self.run_print(boot_conf(BootSpec("linux-cachyos"), "uefi", _ROOT_UUID))
        self.assertEqual(out, limine_conf(BootSpec("linux-cachyos"), _ROOT_UUID))


def _compile(crypt_root=None, **kw):
    sel = parse_selection({"sessions": ["kde-plasma"]}, _CATALOG)
    return compile_adapter(sel, _CATALOG, root_mount="/tmp/calamares-root",
                           ram_bytes=16 * _GIB, is_laptop=False, firmware="uefi",
                           is_mount=lambda p: True, root_bytes=100 * _GIB,
                           root_device="/dev/sda2", boot_device="/dev/sda1",
                           crypt_root=crypt_root, **kw)


class AdapterTest(unittest.TestCase):
    def test_encrypted_root_install(self):
        r = _compile(CryptRoot("/dev/sda2", _NAME))
        basestrap = next(s for s in r.steps if s.argv[0] == "basestrap")
        self.assertIn("cryptsetup", basestrap.argv)
        self.assertIn("cryptsetup", r.packages)
        script = r.steps[-1].argv[-1]
        self.assertIn("CRYPT_UUID=$(blkid -s UUID -o value /dev/sda2)", script)
        self.assertIn(ENCRYPT_HOOK_SCRIPT.rstrip("\n"), script)
        self.assertIn(f"GLUE_BOOT_CRYPT_NAME={_NAME}", script)
        self.assertIn('sed -i "s|@CRYPT_UUID@|$CRYPT_UUID|g" /etc/glue/boot.conf', script)
        self.assertEqual(script.count("mkinitcpio -P"), 1)
        # hooks and rebuild before boot.conf and the deploy
        self.assertLess(script.index("mkinitcpio -P"), script.index("glue-boot-update --deploy"))
        self.assertIn("encrypted root", r.steps[-1].description)

    def test_plain_install_unchanged(self):
        r = _compile()
        self.assertNotIn("cryptsetup", r.packages)
        script = r.steps[-1].argv[-1]
        self.assertNotIn("CRYPT", script)
        self.assertNotIn("mkinitcpio", script)

    def test_rebuild_steps(self):
        (step,) = rebuild_steps("/tmp/calamares-root/")
        self.assertEqual(step.argv[:4], ["artix-chroot", "/tmp/calamares-root", "sh", "-c"])
        self.assertEqual(step.argv[4], "set -e\nmkinitcpio -P\nglue-boot-update\n")


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


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"{name}_main", _CFG / "modules" / name / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class JobModulesTest(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("libcalamares", None)

    def glueinstall(self, partitions):
        mod = _load("glueinstall")
        mod._root_bytes = lambda p: 100 * _GIB
        mod._other_os = lambda gs, fw, root, boot: (None, [])
        cala = _fake_calamares({"rootMountPoint": "/tmp/calamares-root", "firmwareType": "efi",
                                "partitions": partitions,
                                "glue_selection": {"sessions": ["kde-plasma"]}})
        sys.modules["libcalamares"] = cala
        ran = []

        def fake_execute(steps, **kw):
            ran.extend(steps)
        import glue_installer.calamares_adapter as adapter
        orig = adapter.os.path.ismount
        adapter.os.path.ismount = lambda p: p == "/tmp/calamares-root"
        try:
            facts = dict(ram_bytes=8 * _GIB, is_laptop=False, gpu_vendors=None, cpu_v3=False,
                         cpu_vendor_id="other", amd_pstate_active=False)
            result = mod.run(execute_steps=fake_execute, facts=facts, online=lambda: True)
        finally:
            adapter.os.path.ismount = orig
        return result, ran, cala

    def test_glueinstall_encrypted_erase(self):
        result, ran, cala = self.glueinstall(_erase_encrypted())
        self.assertIsNone(result)
        gs = cala.globalstorage
        self.assertEqual(gs.value("glue_crypt_root"), _NAME)
        self.assertEqual(gs.value("glue_fstab_drop"), ["/dev/mapper/luks-swap0"])
        # the dropped encrypted swap becomes a swapfile inside the encrypted root
        self.assertEqual(gs.value("glue_swapfile_fstab_line"), "/swapfile none swap defaults 0 0\n")
        self.assertIn(f"GLUE_BOOT_CRYPT_NAME={_NAME}", ran[-1].argv[-1])
        self.assertTrue(any("left unused" in line for line in cala.log))
        blob = " ".join(" ".join(getattr(s, "argv", ())) + getattr(s, "content", "")
                        for s in ran) + " ".join(map(str, cala.log))
        self.assertNotIn("hunter2", blob)

    def test_glueinstall_plain_sets_no_crypt_keys(self):
        parts = [{"device": "/dev/sda1", "mountPoint": "/boot", "fs": "fat32"},
                 {"device": "/dev/sda2", "mountPoint": "/", "fs": "ext4"}]
        result, ran, cala = self.glueinstall(parts)
        self.assertIsNone(result)
        self.assertIsNone(cala.globalstorage.value("glue_crypt_root"))
        self.assertIsNone(cala.globalstorage.value("glue_fstab_drop"))
        self.assertNotIn("GLUE_BOOT_CRYPT", ran[-1].argv[-1])

    def test_glueinstall_refuses_encrypted_home_before_any_step(self):
        parts = _erase_encrypted(swap=False) + [
            {"device": "/dev/sda4", "mountPoint": "/home", "fs": "ext4",
             "luksMapperName": "luks-home", "luksPassphrase": "hunter2"}]
        result, ran, _ = self.glueinstall(parts)
        self.assertEqual(ran, [])
        self.assertEqual(result[0], "Glue Linux configuration rejected")
        self.assertIn("/home", result[1])
        self.assertNotIn("hunter2", result[1])

    def test_gluecrypt(self):
        mod = _load("gluecrypt")
        ran = []
        cala = _fake_calamares({"rootMountPoint": "/tmp/calamares-root"})
        sys.modules["libcalamares"] = cala
        self.assertIsNone(mod.run(is_mount=lambda p: True,
                                  execute_steps=lambda s, **kw: ran.extend(s)))
        self.assertEqual(ran, [])
        cala = _fake_calamares({"rootMountPoint": "/tmp/calamares-root", "glue_crypt_root": _NAME})
        sys.modules["libcalamares"] = cala
        self.assertIsNone(mod.run(is_mount=lambda p: True,
                                  execute_steps=lambda s, **kw: ran.extend(s)))
        self.assertEqual(ran, rebuild_steps("/tmp/calamares-root"))
        self.assertEqual(cala.job.progress, [1.0])
        from glue_installer.executor import ExecutorError

        def boom(steps, **kw):
            raise ExecutorError("Command failed (exit 1): mkinitcpio")
        result = mod.run(is_mount=lambda p: True, execute_steps=boom)
        self.assertEqual(result[0], "Glue Linux disk encryption step failed")
        cala = _fake_calamares({"rootMountPoint": "/", "glue_crypt_root": _NAME})
        sys.modules["libcalamares"] = cala
        self.assertIn("refusing", mod.run(is_mount=lambda p: True)[1])
        desc = (_CFG / "modules" / "gluecrypt" / "module.desc").read_text()
        self.assertIn('name: "gluecrypt"', desc)

    def test_gluefstab_comments_out_dropped_swap(self):
        mod = _load("gluefstab")
        with tempfile.TemporaryDirectory() as root:
            fstab = Path(root) / "etc" / "fstab"
            fstab.parent.mkdir()
            fstab.write_text(f"/dev/mapper/{_NAME} / ext4 defaults 0 1\n"
                             "/dev/mapper/luks-swap0 swap swap defaults 0 0\n")
            gs = {"rootMountPoint": root, "glue_fstab_drop": ["/dev/mapper/luks-swap0"],
                  "glue_swapfile_fstab_line": "/swapfile none swap defaults 0 0\n"}
            for _ in range(2):
                sys.modules["libcalamares"] = _fake_calamares(gs)
                self.assertIsNone(mod.run(is_mount=lambda p: True))
            lines = fstab.read_text().splitlines()
            self.assertEqual(lines[0], f"/dev/mapper/{_NAME} / ext4 defaults 0 1")
            self.assertTrue(lines[1].startswith("# unused"))
            self.assertEqual(lines[2:], ["/swapfile none swap defaults 0 0"])
            sys.modules["libcalamares"] = _fake_calamares(
                {"rootMountPoint": root, "glue_fstab_drop": "x"})
            self.assertIn("list of strings", mod.run(is_mount=lambda p: True)[1])
        self.assertEqual(drop_fstab_specs("# a\n/dev/x / ext4\n", ["/dev/y"]),
                         "# a\n/dev/x / ext4\n")


class PartitionConfTest(unittest.TestCase):
    def test_encryption_offered_as_luks2(self):
        text = (_CFG / "modules" / "partition.conf").read_text()
        self.assertIn("enableLuksAutomatedPartitioning: true\n", text)
        self.assertIn("luksGeneration: luks2\n", text)
        self.assertIn("showNotEncryptedBootMessage: false\n", text)


if __name__ == "__main__":
    unittest.main()
