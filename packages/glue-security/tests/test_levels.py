"""list/status schema, each level on its own, custom detection, exit codes."""

import json
import os
import subprocess
import sys
import unittest

from helpers import PKG, REPO_PACKAGES, TargetCase

from glue_security.components import COMPONENTS, LEVELS, level_ids

LIST_KEYS = {"id", "name", "description", "protects", "breaks", "cost",
             "needs_reboot", "levels"}


class ListTest(TargetCase):
    def test_list_json_schema(self):
        data = json.loads(self.ok_list())
        self.assertIsInstance(data, list)
        self.assertEqual([c["id"] for c in data], [c.id for c in COMPONENTS])
        for c in data:
            self.assertEqual(set(c), LIST_KEYS)
            for k in LIST_KEYS - {"needs_reboot", "levels"}:
                self.assertIsInstance(c[k], str)
                self.assertTrue(c[k].strip(), f"{c['id']}.{k} empty")
                self.assertNotIn("\u2014", c[k])
            self.assertIsInstance(c["needs_reboot"], bool)
            self.assertTrue(set(c["levels"]) <= set(LEVELS))
            # a level includes everything below it
            if c["levels"]:
                self.assertEqual(c["levels"], list(LEVELS[LEVELS.index(c["levels"][0]):]))

    def test_level_membership(self):
        self.assertEqual(level_ids("basic"),
                         {"firewall", "apparmor", "sysctl-base", "updates-notify"})
        self.assertEqual(level_ids("hardened") - level_ids("basic"),
                         {"ptrace", "mac-random", "kernel-harden-alloc"})
        self.assertEqual(level_ids("super") - level_ids("hardened"),
                         {"lockdown", "usbguard", "kernel-hardened",
                          "userns-restrict", "librewolf"})
        nosmt = next(c for c in COMPONENTS if c.id == "nosmt")
        self.assertEqual(nosmt.levels, ())

    def test_list_human(self):
        code, out, _ = self.cli("list", root=False)
        self.assertEqual(code, 0)
        self.assertIn("opt-in only", out)

    def ok_list(self):
        code, out, err = self.cli("list", "--json", root=False)
        self.assertEqual(code, 0, err)
        return out


class StatusTest(TargetCase):
    def test_fresh_system(self):
        st = self.status()
        self.assertEqual(st["level"], "none")
        self.assertEqual(set(st["components"]), {c.id for c in COMPONENTS})
        self.assertFalse(any(st["components"].values()))
        self.assertIs(st["encrypted_root"], False)
        self.assertEqual(self.commands(), [])

    def test_encrypted_root(self):
        with open(self.f("etc/glue/boot.conf"), "a") as fh:
            fh.write("GLUE_BOOT_CRYPT_UUID=cccc-3333\nGLUE_BOOT_CRYPT_NAME=root\n")
        self.assertIs(self.status()["encrypted_root"], True)

    def test_encrypted_root_crypttab(self):
        self.f("etc/crypttab").write_text("# <name> <device>\nhome UUID=dd none luks\n")
        self.assertIs(self.status()["encrypted_root"], True)

    def test_human_status(self):
        code, out, _ = self.cli("status")
        self.assertEqual(code, 0)
        self.assertIn("Security level: none", out)


class LevelBase(TargetCase):
    def check_level(self, level):
        self.ok("level", level)
        st = self.status()
        self.assertEqual(st["level"], level)
        on = {k for k, v in st["components"].items() if v}
        self.assertEqual(on, level_ids(level))
        state = self.state()
        self.assertEqual(state["level"], level)
        self.assertEqual(state["base"], level)
        self.assertFalse([k for k in state if k.startswith("component.")])
        cmds = self.commands()
        for argv in cmds:
            # every command ran inside the target, never on the host
            self.assertEqual(argv[:2], ["artix-chroot", str(self.root)], argv)
        installed = [p for argv in cmds if "pacman" in argv
                     for p in argv if not p.startswith("-") and p not in
                     ("artix-chroot", str(self.root), "pacman")]
        self.assertTrue(set(installed) <= REPO_PACKAGES, installed)
        return set(installed)

    def check_basic_files(self):
        self.assertTrue(self.f(f"{self.enabled_dir()}/glue-firewall").is_symlink())
        self.assertTrue(self.f("etc/sysctl.d/90-glue-security-base.conf").is_file())
        self.assertTrue(self.f("etc/xdg/autostart/glue-update-notify.desktop").is_symlink())
        self.assertIn("apparmor", self.text("etc/glue/cmdline.d/50-glue-security.conf"))

    def enabled_dir(self):
        return {"dinit": "etc/dinit.d/boot.d", "runit": "etc/runit/runsvdir/default",
                "openrc": "etc/runlevels/default"}[self.init]


class LevelsDinit(LevelBase):
    init = "dinit"

    def test_basic_alone(self):
        pkgs = self.check_level("basic")
        self.check_basic_files()
        self.assertEqual(pkgs, {"nftables", "glue-security", "apparmor",
                                f"apparmor-{self.init}", "pacman-contrib", "libnotify"})
        self.assertEqual(self.text("etc/glue/cmdline.d/50-glue-security.conf").splitlines()[-1],
                         "lsm=landlock,lockdown,yama,integrity,apparmor,bpf")
        # basic never touches the kernel choice or gaming knobs
        self.assertIn("GLUE_BOOT_KERNEL=linux-cachyos", self.text("etc/glue/boot.conf"))
        self.assertFalse(self.f("etc/sysctl.d/90-glue-security-userns.conf").exists())
        self.assertFalse(self.f("etc/sysctl.d/90-glue-security-ptrace.conf").exists())

    def test_hardened_alone(self):
        self.check_level("hardened")
        self.check_basic_files()
        self.assertEqual(self.text("etc/glue/cmdline.d/50-glue-security.conf").splitlines()[-1],
                         "lsm=landlock,lockdown,yama,integrity,apparmor,bpf "
                         "slab_nomerge page_alloc.shuffle=1")
        self.assertIn("kernel.yama.ptrace_scope=1",
                      self.text("etc/sysctl.d/90-glue-security-ptrace.conf"))
        self.assertTrue(self.f("etc/NetworkManager/conf.d/90-glue-mac-random.conf").is_file())

    def test_super_alone(self):
        pkgs = self.check_level("super")
        self.check_basic_files()
        self.assertTrue({"linux-hardened", "linux-hardened-headers", "librewolf",
                         "usbguard", f"usbguard-{self.init}"} <= pkgs)
        self.assertEqual(self.text("etc/glue/cmdline.d/50-glue-security.conf").splitlines()[-1],
                         "lsm=landlock,lockdown,yama,integrity,apparmor,bpf "
                         "slab_nomerge page_alloc.shuffle=1 lockdown=integrity")
        self.assertIn("GLUE_BOOT_KERNEL=linux-hardened", self.text("etc/glue/boot.conf"))
        self.assertTrue(self.f(f"{self.enabled_dir()}/usbguard").is_symlink())
        self.assertIn("046d:c52b", self.text("etc/usbguard/rules.conf"))
        self.assertIn("librewolf.desktop", self.text("etc/xdg/mimeapps.list"))
        self.assertIn("user.max_user_namespaces=0",
                      self.text("etc/sysctl.d/90-glue-security-userns.conf"))
        self.assertNotIn("nosmt", self.text("etc/glue/cmdline.d/50-glue-security.conf"))

    def test_super_then_basic(self):
        self.ok("level", "super")
        self.check_level("basic")
        self.assertIn("GLUE_BOOT_KERNEL=linux-cachyos", self.text("etc/glue/boot.conf"))
        self.assertFalse(self.f(f"{self.enabled_dir()}/usbguard").exists())
        self.assertNotIn("librewolf", self.f("etc/xdg/mimeapps.list").read_text()
                         if self.f("etc/xdg/mimeapps.list").exists() else "")
        # kernels and browsers are never removed
        self.assertTrue(list(self.f("var/lib/pacman/local").glob("linux-hardened-1*")))
        self.assertFalse([a for a in self.commands() if "-R" in a or "-Rns" in a])

    def test_level_twice_installs_once(self):
        self.ok("level", "hardened")
        n = len(self.commands())
        out = self.ok("level", "hardened")
        self.assertIn("nothing to change", out)
        self.assertEqual(len(self.commands()), n)


class LevelsRunit(LevelsDinit):
    init = "runit"

    def test_runit_apparmor_stage1(self):
        self.ok("level", "basic")
        link = self.f("etc/rc/sysinit/62-apparmor")
        self.assertEqual(os.readlink(link), "/usr/lib/rc/sv.d/apparmor")
        self.assertIn("APPARMOR=enforce", self.text("etc/rc/apparmor.conf"))
        self.assertEqual(os.readlink(self.f("etc/runit/runsvdir/default/glue-firewall")),
                         "/etc/runit/sv/glue-firewall")


class LevelsOpenrc(LevelsDinit):
    init = "openrc"

    def test_openrc_apparmor_boot_runlevel(self):
        self.ok("level", "basic")
        self.assertEqual(os.readlink(self.f("etc/runlevels/boot/apparmor")),
                         "/etc/init.d/apparmor")


class CustomTest(TargetCase):
    def test_extra_component_is_custom(self):
        self.ok("level", "basic")
        self.ok("enable", "nosmt")
        self.assertEqual(self.status()["level"], "custom")
        self.assertEqual(self.state()["component.nosmt"], "on")
        self.assertEqual(self.state()["base"], "basic")
        self.ok("disable", "nosmt")
        self.assertEqual(self.status()["level"], "basic")
        self.assertNotIn("component.nosmt", self.state())

    def test_missing_component_is_custom(self):
        self.ok("level", "hardened")
        self.ok("disable", "ptrace")
        self.assertEqual(self.status()["level"], "custom")
        self.assertEqual(self.state()["component.ptrace"], "off")
        self.ok("enable", "ptrace")
        self.assertEqual(self.status()["level"], "hardened")

    def test_components_one_by_one_reach_level(self):
        for cid in sorted(level_ids("basic")):
            self.ok("enable", cid)
        self.assertEqual(self.status()["level"], "basic")
        self.assertEqual(self.state()["level"], "basic")

    def test_level_skips_refused_lockdown(self):
        # the installer fails on non-zero: a safety refusal is only a warning here
        self.add_pkg("nvidia-dkms")
        code, out, err = self.cli("level", "super")
        self.assertEqual(code, 0, err)
        self.assertIn("warning: lockdown", err)
        self.assertIn("nvidia-dkms", err)
        st = self.status()
        self.assertEqual(st["level"], "custom")
        self.assertFalse(st["components"]["lockdown"])
        self.assertTrue(st["components"]["usbguard"])
        self.assertEqual(self.state()["component.lockdown"], "off")
        self.ok("level", "super", "--force")
        self.assertEqual(self.status()["level"], "super")


    def test_level_skips_usbguard_without_devices(self):
        os.environ["FAKE_USB"] = ""
        code, _, err = self.cli("level", "super")
        self.assertEqual(code, 0, err)
        self.assertIn("warning: usbguard", err)
        self.assertFalse(self.status()["components"]["usbguard"])

    def test_super_without_boot_conf(self):
        # installer order: level runs before the bootloader step writes boot.conf
        self.f("etc/glue/boot.conf").unlink()
        code, out, err = self.cli("level", "super")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.status()["level"], "super")
        self.assertFalse(self.f("etc/glue/boot.conf").exists())

    def test_level_package_failure_is_error(self):
        os.environ["FAKE_FAIL"] = "apparmor"
        code, _, err = self.cli("level", "basic")
        self.assertEqual(code, 1)
        self.assertIn("apparmor: skipped", err)


class ExitCodes(TargetCase):
    def test_usage_errors(self):
        self.assertEqual(self.cli("level", "ultra")[0], 2)
        self.assertEqual(self.cli("enable", "nope")[0], 2)
        self.assertEqual(self.cli(root=False)[0], 2)
        self.assertEqual(self.cli("frobnicate", root=False)[0], 2)

    def test_missing_root_dir(self):
        code, _, err = self.cli("status", "--root", str(self.root / "nope"), root=False)
        self.assertEqual(code, 1)
        self.assertIn("no such directory", err)

    @unittest.skipIf(os.geteuid() == 0, "needs a normal user")
    def test_live_needs_root(self):
        code, _, err = self.cli("level", "basic", root=False)
        self.assertEqual(code, 1)
        self.assertIn("run it as root", err)
        self.assertEqual(self.commands(), [])

    def test_launcher_script(self):
        env = dict(os.environ, PYTHONPATH=str(PKG))
        r = subprocess.run([sys.executable, str(PKG / "glue-security"), "list", "--json"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(json.loads(r.stdout)), len(COMPONENTS))
        r = subprocess.run([sys.executable, str(PKG / "glue-security"), "level"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
