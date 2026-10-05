"""staged_pacman_conf: [cachyos-v3] only when its mirrorlist exists on the host."""

import tempfile
import unittest
from pathlib import Path

from glue_installer.disk_swap import staged_pacman_conf

_CONF = "[options]\nArchitecture = auto\n\n[cachyos]\nInclude = /etc/pacman.d/cachyos-mirrorlist\n"


class TestStagedPacmanConf(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conf = Path(self.tmp.name) / "pacman.conf"
        self.conf.write_text(_CONF)
        self.ml = Path(self.tmp.name) / "cachyos-v3-mirrorlist"

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_mirrorlist_keeps_plain_conf(self):
        out = staged_pacman_conf(str(self.conf), True, "linux-cachyos", str(self.ml))
        self.assertEqual(out, str(self.conf))

    def test_mirrorlist_present_adds_v3_before_cachyos(self):
        self.ml.write_text("Server = https://example.invalid/$arch_v3/$repo\n")
        out = staged_pacman_conf(str(self.conf), True, "linux-cachyos", str(self.ml))
        text = Path(out).read_text()
        self.assertLess(text.index("[cachyos-v3]"), text.index("[cachyos]\n"))
        self.assertIn(f"Include = {self.ml}", text)

    def test_not_v3_or_other_kernel_unchanged(self):
        self.ml.write_text("x\n")
        for v3, kernel in ((False, "linux-cachyos"), (True, "linux-zen")):
            self.assertEqual(staged_pacman_conf(str(self.conf), v3, kernel, str(self.ml)),
                             str(self.conf))


if __name__ == "__main__":
    unittest.main()
