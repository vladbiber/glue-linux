"""Every component: enable/disable round trip and its special cases."""

import os
import re
import unittest
from pathlib import Path

from helpers import PKG, TargetCase

from glue_security.actions import set_mime_defaults, mime_defaults
from glue_security.components import COMPONENTS


def snapshot(root: Path):
    """Every path under etc/ with its content or link target."""
    out = {}
    for p in sorted((root / "etc").rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_symlink():
            out[rel] = "-> " + os.readlink(p)
        elif p.is_file():
            out[rel] = p.read_text()
    return out


class RoundTrip(TargetCase):
    def round_trip(self, cid):
        self.ok("enable", cid)
        st = self.status()
        self.assertTrue(st["components"][cid], cid)
        self.assertEqual([k for k, v in st["components"].items() if v], [cid])
        self.assertEqual(st["level"], "custom")
        self.ok("disable", cid)
        st = self.status()
        self.assertFalse(any(st["components"].values()), cid)
        self.assertEqual(st["level"], "none")

    def test_every_component(self):
        for c in COMPONENTS:
            if c.id == "fido2":  # enroll is interactive, enable only explains
                continue
            with self.subTest(c.id):
                before = snapshot(self.root)
                self.round_trip(c.id)
                after = snapshot(self.root)
                # usbguard keeps its generated rules and daemon config
                for snap in (before, after):
                    for k in [k for k in snap if k.startswith("etc/usbguard/")
                              or k == "etc/glue/security.conf"]:
                        snap.pop(k)
                self.assertEqual(before, after, f"{c.id} left changes behind")
                # re-enabling after a disable works too
                self.ok("enable", c.id)
                self.assertTrue(self.status()["components"][c.id])
                self.ok("disable", c.id)

    def test_enable_twice_is_noop(self):
        self.ok("enable", "firewall")
        out = self.ok("enable", "firewall")
        self.assertIn("nothing to change", out)


class FirewallTest(TargetCase):
    def test_ruleset_shape(self):
        nft = (PKG / "firewall.nft").read_text()
        self.assertIn("policy drop;", nft)
        self.assertNotRegex(nft, r"hook (output|forward)")
        self.assertIn("ct state established,related accept", nft)
        self.assertIn("udp dport 546", nft)
        self.assertIn("nd-neighbor-solicit", nft)
        self.assertEqual(set(re.findall(r"table inet (\w+)", nft)), {"glue_firewall"})
        for svc in ("dinit.d/glue-firewall", "runit/glue-firewall/run",
                    "runit/glue-firewall/finish", "openrc/glue-firewall"):
            text = (PKG / svc).read_text()
            self.assertIn("glue_firewall" if "finish" in svc or "openrc" in svc
                          or "dinit" in svc else "firewall.nft", text)

    def test_dinit_link_relative(self):
        self.ok("enable", "firewall")
        self.assertEqual(os.readlink(self.f("etc/dinit.d/boot.d/glue-firewall")),
                         "../glue-firewall")


class SysctlTest(TargetCase):
    def test_base_knobs_are_zero_cost(self):
        self.ok("enable", "sysctl-base")
        text = self.text("etc/sysctl.d/90-glue-security-base.conf")
        for key in ("kernel.dmesg_restrict=1", "kernel.kptr_restrict=2",
                    "-kernel.kexec_load_disabled=1", "fs.protected_fifos=2",
                    "fs.protected_regular=2"):
            self.assertIn(key, text)
        for banned in ("ptrace", "userns", "bpf_jit", "perf_event", "mitigations",
                       "max_map_count", "unprivileged_userfaultfd"):
            self.assertNotIn(banned, text)

    def test_sorts_after_glue_settings(self):
        # 70-glue.conf sets unprivileged_userns_clone=1; ours must load later
        self.ok("enable", "userns-restrict")
        names = sorted(["70-glue.conf", "90-glue-security-userns.conf"])
        self.assertEqual(names[-1], "90-glue-security-userns.conf")


class CmdlineTest(TargetCase):
    def test_tokens_merge_and_drop(self):
        self.ok("enable", "nosmt")
        self.ok("enable", "apparmor")
        self.assertEqual(self.text("etc/glue/cmdline.d/50-glue-security.conf").splitlines()[-1],
                         "lsm=landlock,lockdown,yama,integrity,apparmor,bpf nosmt")
        self.ok("disable", "nosmt")
        self.assertNotIn("nosmt", self.text("etc/glue/cmdline.d/50-glue-security.conf"))
        self.ok("disable", "apparmor")
        self.assertFalse(self.f("etc/glue/cmdline.d/50-glue-security.conf").exists())

    def test_old_init_on_alloc_state_ignored(self):
        # a system set up before kernel-harden-alloc replaced init-on-alloc
        d = self.f("etc/glue/cmdline.d")
        d.mkdir(parents=True)
        (d / "50-glue-security.conf").write_text(
            "# old\nlsm=landlock,lockdown,yama,integrity,apparmor,bpf init_on_alloc=1\n")
        self.f("etc/glue/security.conf").write_text(
            "base=hardened\nlevel=hardened\ncomponent.init-on-alloc=on\n")
        st = self.status()
        self.assertNotIn("init-on-alloc", st["components"])
        self.ok("level", "hardened")
        self.assertEqual(self.status()["level"], "hardened")
        self.assertNotIn("init_on_alloc", self.text("etc/glue/cmdline.d/50-glue-security.conf"))
        self.assertNotIn("init-on-alloc", self.text("etc/glue/security.conf"))
        self.ok("disable", "kernel-harden-alloc")
        self.assertNotIn("slab_nomerge", self.text("etc/glue/cmdline.d/50-glue-security.conf"))

    def test_reboot_note(self):
        out = self.ok("enable", "nosmt")
        self.assertIn("restart", out)

    def test_lockdown_refused_with_dkms(self):
        self.add_pkg("nvidia-open-dkms")
        code, _, err = self.cli("enable", "lockdown")
        self.assertEqual(code, 1)
        self.assertIn("nvidia-open-dkms", err)
        self.assertFalse(self.status()["components"]["lockdown"])
        self.ok("enable", "lockdown", "--force")
        self.assertTrue(self.status()["components"]["lockdown"])


class UsbguardTest(TargetCase):
    def test_policy_from_present_devices(self):
        self.ok("enable", "usbguard")
        rules = self.f("etc/usbguard/rules.conf")
        self.assertEqual(rules.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(rules.read_text().splitlines()), 2)
        conf = self.text("etc/usbguard/usbguard-daemon.conf")
        for line in ("ImplicitPolicyTarget=block", "PresentDevicePolicy=apply-policy",
                     "InsertedDevicePolicy=apply-policy", "IPCAllowedGroups=wheel"):
            self.assertIn(line, conf)
        gen = [a for a in self.commands() if "generate-policy" in a]
        self.assertEqual(gen, [["artix-chroot", str(self.root), "usbguard", "generate-policy"]])

    def test_existing_config_keys_replaced(self):
        conf = self.f("etc/usbguard/usbguard-daemon.conf")
        conf.parent.mkdir(parents=True)
        conf.write_text("# usbguard\nRuleFile=/etc/usbguard/rules.conf\n"
                        "PresentDevicePolicy=keep\nIPCAllowedGroups=\nHidePII=false\n")
        self.ok("enable", "usbguard")
        text = conf.read_text()
        self.assertEqual(text.count("PresentDevicePolicy="), 1)
        self.assertIn("PresentDevicePolicy=apply-policy", text)
        self.assertIn("HidePII=false", text)
        self.assertTrue(text.startswith("# usbguard\n"))

    def test_rules_merged_not_replaced(self):
        rules = self.f("etc/usbguard/rules.conf")
        rules.parent.mkdir(parents=True)
        rules.write_text('allow id 1234:5678 name "My stick"\n')
        self.ok("enable", "usbguard")
        self.assertEqual(len(rules.read_text().splitlines()), 3)

    def test_no_devices_refused(self):
        os.environ["FAKE_USB"] = ""
        code, _, err = self.cli("enable", "usbguard")
        self.assertEqual(code, 1)
        self.assertIn("keyboard", err)
        self.assertFalse(self.status()["components"]["usbguard"])


class KernelTest(TargetCase):
    def test_default_kernel_switch_and_restore(self):
        self.ok("enable", "kernel-hardened")
        boot = self.text("etc/glue/boot.conf")
        self.assertIn("GLUE_BOOT_KERNEL=linux-hardened", boot)
        self.assertEqual(boot.count("GLUE_BOOT_KERNEL="), 1)
        self.assertEqual(self.state()["kernel_prev"], "linux-cachyos")
        self.ok("disable", "kernel-hardened")
        self.assertIn("GLUE_BOOT_KERNEL=linux-cachyos", self.text("etc/glue/boot.conf"))
        self.assertNotIn("kernel_prev", self.state())

    def test_install_failure_restores_boot_conf(self):
        os.environ["FAKE_FAIL"] = "linux-hardened"
        code, _, err = self.cli("enable", "kernel-hardened")
        self.assertEqual(code, 1)
        self.assertIn("GLUE_BOOT_KERNEL=linux-cachyos", self.text("etc/glue/boot.conf"))

    def test_without_boot_conf(self):
        self.f("etc/glue/boot.conf").unlink()
        code, out, err = self.cli("enable", "kernel-hardened")
        self.assertEqual(code, 0, err)
        self.assertNotIn("boot.conf", out + err)
        self.assertFalse(self.f("etc/glue/boot.conf").exists())
        self.assertTrue(self.status()["components"]["kernel-hardened"])
        # the installer's boot.conf, written afterwards, carries the kernel
        self.f("etc/glue/boot.conf").write_text("GLUE_BOOT_KERNEL=linux-hardened\n")
        self.assertTrue(self.status()["components"]["kernel-hardened"])
        self.ok("disable", "kernel-hardened")
        self.assertFalse(self.status()["components"]["kernel-hardened"])

    def test_nvidia_note(self):
        self.add_pkg("nvidia-utils")
        out = self.ok("enable", "kernel-hardened")
        self.assertIn("nvidia-open-dkms", out)


class BrowserTest(TargetCase):
    def test_previous_default_restored(self):
        mime = self.f("etc/xdg/mimeapps.list")
        mime.parent.mkdir(parents=True)
        original = ("[Default Applications]\nx-scheme-handler/http=firefox.desktop\n"
                    "image/png=loupe.desktop\n\n[Added Associations]\ntext/html=firefox.desktop\n")
        mime.write_text(original)
        self.ok("enable", "librewolf")
        cur = mime_defaults(mime.read_text())
        self.assertEqual(cur["x-scheme-handler/http"], "librewolf.desktop")
        self.assertEqual(cur["text/html"], "librewolf.desktop")
        self.assertEqual(cur["image/png"], "loupe.desktop")
        self.assertIn("[Added Associations]\ntext/html=firefox.desktop", mime.read_text())
        self.ok("disable", "librewolf")
        self.assertEqual(mime.read_text(), original)
        # stays installed
        self.assertTrue(list(self.f("var/lib/pacman/local").glob("librewolf-*")))

    def test_mime_editor(self):
        self.assertEqual(set_mime_defaults("", {"a": "b.desktop"}),
                         "[Default Applications]\na=b.desktop\n")
        self.assertEqual(set_mime_defaults("[Default Applications]\na=b\n", {"a": None}),
                         "[Default Applications]\n")


class UpdatesNotifyTest(TargetCase):
    def test_autostart_link(self):
        self.ok("enable", "updates-notify")
        link = self.f("etc/xdg/autostart/glue-update-notify.desktop")
        self.assertEqual(os.readlink(link),
                         "/usr/share/glue-security/glue-update-notify.desktop")
        desktop = (PKG / "glue-update-notify.desktop").read_text()
        self.assertIn("Exec=/usr/bin/glue-update-notify", desktop)
        script = (PKG / "glue-update-notify").read_text()
        self.assertIn("checkupdates", script)
        self.assertIn("glue-apps --page updates", script)
        self.assertNotIn("pacman -S", script)


if __name__ == "__main__":
    unittest.main()


class AdblockTest(TargetCase):
    POLICY = "etc/firefox/policies/policies.json"

    def test_forces_ublock_and_lite(self):
        import json
        self.ok("enable", "adblock")
        ext = json.loads(self.text(self.POLICY))["policies"]["ExtensionSettings"]
        self.assertEqual(ext["uBlock0@raymondhill.net"]["installation_mode"], "force_installed")
        for rel in ("etc/chromium/policies/managed/glue-adblock.json",
                    "etc/opt/chrome/policies/managed/glue-adblock.json"):
            forced = json.loads(self.text(rel))["ExtensionInstallForcelist"]
            self.assertTrue(forced[0].startswith("ddkjiahejlhfcafbddmgiahcphecmpfh;"))

    def test_keeps_an_existing_policy(self):
        import json
        self.f(self.POLICY).parent.mkdir(parents=True)
        self.f(self.POLICY).write_text('{"policies": {"DisableTelemetry": true}}')
        self.ok("enable", "adblock")
        self.assertTrue(json.loads(self.text(self.POLICY))["policies"]["DisableTelemetry"])
        self.ok("disable", "adblock")
        self.assertEqual(json.loads(self.text(self.POLICY)),
                         {"policies": {"DisableTelemetry": True}})


class ServiceComponentsTest(TargetCase):
    def test_service_files_for_every_init(self):
        for svc, cmd in (("glue-snapshot", "/usr/lib/glue-security/snapshot-loop"),
                         ("glue-antivirus", "/usr/bin/glue-antivirus")):
            for rel in (f"dinit.d/{svc}", f"runit/{svc}/run", f"openrc/{svc}"):
                self.assertIn(cmd, (PKG / rel).read_text(), rel)
            self.assertIn(svc, (PKG / "PKGBUILD").read_text())

    def test_enable_links_the_service(self):
        self.ok("enable", "snapshots")
        self.assertEqual(os.readlink(self.f("etc/dinit.d/boot.d/glue-snapshot")),
                         "../glue-snapshot")
        self.ok("enable", "antivirus")
        self.assertTrue(self.f("etc/dinit.d/boot.d/glue-antivirus").is_symlink())


class SuperExtrasTest(TargetCase):
    def test_lid_poweroff_and_no_sleep(self):
        self.ok("enable", "lid-poweroff")
        self.assertIn("HandleLidSwitch=poweroff",
                      self.text("etc/elogind/logind.conf.d/90-glue-lid-poweroff.conf"))
        self.assertIn("AllowSuspend=no",
                      self.text("etc/elogind/sleep.conf.d/90-glue-lid-poweroff.conf"))

    def test_autolock_conf(self):
        self.ok("enable", "autolock")
        self.assertIn("minutes=5", self.text("etc/glue/autolock.conf"))

    def test_autolock_script_respects_off_and_x11(self):
        import subprocess
        script = str(PKG / "glue-autolock")
        env = dict(os.environ, GLUE_AUTOLOCK_CONF=str(self.root / "nope"),
                   WAYLAND_DISPLAY="wayland-x")
        self.assertEqual(subprocess.run(["sh", script], env=env).returncode, 0)
        (self.root / "etc/glue/autolock.conf").write_text("minutes=5\n")
        env = dict(os.environ, GLUE_AUTOLOCK_CONF=str(self.root / "etc/glue/autolock.conf"))
        env.pop("WAYLAND_DISPLAY", None)
        self.assertEqual(subprocess.run(["sh", script], env=env).returncode, 0)

    def test_update_notify_starts_autolock_first(self):
        text = (PKG / "glue-update-notify").read_text()
        self.assertLess(text.index("glue-autolock &"), text.index('[ -e "$AUTOSTART" ] || exit 0'))
