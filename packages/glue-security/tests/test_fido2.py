"""glue-fido2 and its initcpio hook with stub FIDO2 tools and a stub
cryptsetup. tests/fido2-docker.sh runs the same flow against a real LUKS2
header and the stock encrypt hook."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
STUBS = PKG / "tests" / "fido2-stubs"
HOOKS = "HOOKS=(base udev autodetect keyboard keymap block encrypt filesystems fsck)\n"
FAKE_CRYPTSETUP = """#!/bin/sh
echo "$*" >> "$CS_LOG"
case "$*" in
    *--test-passphrase*) echo "Key slot 2 unlocked."; echo "Command successful." ;;
esac
exit 0
"""


class Fido2Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.t = t
        self.dir = t / "fido2"
        self.keyfile = t / "crypto_keyfile.bin"
        self.mk = t / "mkinitcpio.conf"
        self.mk.write_text(HOOKS)
        (t / "bin").mkdir()
        cs = t / "bin" / "cryptsetup"
        cs.write_text(FAKE_CRYPTSETUP)
        cs.chmod(0o755)
        self.cs_log = t / "cs.log"
        self.env = dict(os.environ, PATH=f"{t / 'bin'}:{STUBS}:{os.environ['PATH']}",
                        GLUE_FIDO2_DIR=str(self.dir), GLUE_FIDO2_KEYFILE=str(self.keyfile),
                        GLUE_MKINITCPIO_CONF=str(self.mk), GLUE_FIDO2_LUKS_DEV="/dev/fake",
                        GLUE_FIDO2_NO_INITRAMFS="1", CS_LOG=str(self.cs_log),
                        FIDO_LOG=str(t / "fido.log"), TMPDIR=str(t))

    def tool(self, *args, **env):
        return subprocess.run(["sh", str(PKG / "glue-fido2"), *args], env={**self.env, **env},
                              capture_output=True, text=True, input="pass\n")

    def hook(self, **env):
        script = f'. "{PKG}/initcpio/hooks/glue-fido2"\nrun_hook\n'
        return subprocess.run(["sh", "-c", script], env={**self.env, **env},
                              capture_output=True, text=True, timeout=30)

    def cs(self):
        return self.cs_log.read_text() if self.cs_log.exists() else ""


class ToolTest(Fido2Case):
    def test_enroll_adds_slot_conf_and_hook(self):
        r = self.tool("enroll")
        self.assertEqual(r.returncode, 0, r.stderr)
        conf = (self.dir / "key-2.conf").read_text()
        self.assertIn("CRED=Q1JFRElELWdsdWU=", conf)
        self.assertIn("PIN=0", conf)
        self.assertEqual(self.dir.stat().st_mode & 0o777, 0o700)
        self.assertIn("block glue-fido2 encrypt filesystems", self.mk.read_text())
        self.assertIn("luksAddKey /dev/fake", self.cs())

    def test_enroll_twice_keeps_one_hook(self):
        self.tool("enroll")
        self.tool("enroll")
        self.assertEqual(self.mk.read_text().count("glue-fido2"), 1)

    def test_pin_flag_is_stored_and_used(self):
        self.assertEqual(self.tool("enroll", "--pin").returncode, 0)
        self.assertIn("PIN=1", (self.dir / "key-2.conf").read_text())
        self.assertIn("pin=1", (self.t / "fido.log").read_text())

    def test_remove_restores_hooks(self):
        self.tool("enroll")
        r = self.tool("remove", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.mk.read_text(), HOOKS)
        self.assertFalse((self.dir / "key-2.conf").exists())
        self.assertIn("luksKillSlot /dev/fake 2", self.cs())

    def test_remove_refuses_a_slot_it_did_not_make(self):
        self.tool("enroll")
        r = self.tool("remove", "0")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("luksKillSlot", self.cs())

    def test_no_key_plugged_in(self):
        r = self.tool("enroll", FIDO_NOKEY="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no security key found", r.stderr)
        self.assertNotIn("luksAddKey", self.cs())

    def test_status(self):
        self.assertIn("no security key enrolled", self.tool("status").stdout)
        self.tool("enroll", "--pin")
        out = self.tool("status").stdout
        self.assertIn("keyslot 2 (with PIN)", out)
        self.assertIn("boot hook: on", out)


class HookTest(Fido2Case):
    def enroll(self):
        self.assertEqual(self.tool("enroll").returncode, 0)

    def test_key_gives_the_enrolled_secret(self):
        self.enroll()
        self.assertEqual(self.hook().returncode, 0)
        enrolled = [l for l in self.cs().splitlines() if "luksAddKey" in l]
        self.assertTrue(enrolled)
        self.assertEqual(self.keyfile.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(self.keyfile.read_text()), 43)

    def test_failures_leave_no_keyfile(self):
        self.enroll()
        for env in ({"FIDO_NOKEY": "1"}, {"FIDO_FAIL": "1"}):
            with self.subTest(env):
                self.assertEqual(self.hook(**env).returncode, 0)
                self.assertFalse(self.keyfile.exists())

    def test_nothing_enrolled_touches_nothing(self):
        r = self.hook()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertFalse(self.keyfile.exists())

    def test_existing_keyfile_wins(self):
        self.enroll()
        self.keyfile.write_text("embedded")
        self.hook()
        self.assertEqual(self.keyfile.read_text(), "embedded")

    def test_install_script(self):
        text = (PKG / "initcpio/install/glue-fido2").read_text()
        for needle in ("fido2-assert", "fido2-token", "timeout", "usbhid",
                       "/etc/glue/fido2/key-*.conf", "add_runscript"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
