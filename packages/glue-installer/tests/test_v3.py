"""
Tests for roadmap 1.7 (cpu x86-64-v3 detection + [cachyos-v3] repo),
1.8 (cmdline nowatchdog+zswap.enabled=0), and
1.9 (Mesa + GL shader cache size in glue-gaming.sh).
"""

import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from glue_installer.cpu import cpu_supports_v3
from glue_installer.catalog import (
    Catalog, Gaming, Init, Kernel, Keybinding, Minimal,
    Session, Shell, SupportToggle, load_catalog,
)
from glue_installer.plan import Selection, resolve_plan

_CATALOG_PATH = _PKG_ROOT / "catalog" / "catalog.json"

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

_KBS = [
    Keybinding("Super+Return", "Open terminal"),
    Keybinding("Super+q", "Close window"),
    Keybinding("Super+h", "Focus left"),
    Keybinding("Super+j", "Focus down"),
    Keybinding("Super+k", "Focus up"),
]


def _make_catalog() -> Catalog:
    return Catalog(
        version=1,
        kernels=[
            Kernel("linux-cachyos", "CachyOS Kernel", "desc",
                   ["linux-cachyos", "linux-cachyos-headers"], primary=True),
            Kernel("linux-zen", "Zen Kernel", "desc", ["linux-zen"], primary=False),
        ],
        inits=[
            Init("dinit", "dinit", "desc", ["dinit"], recommended=True),
        ],
        sessions=[
            Session("wm-bare", "Bare WM", "wm", "desc", 3, 5, _KBS, None,
                    ["wm-bare-pkg"], [], []),
        ],
        shells=[],
        support=[],
        gaming=Gaming(
            name="Gaming Mode", description="Gaming",
            packages=["steam"], services=[],
            gpu_autodetect=False,
        ),
        minimal=Minimal("Minimal", "Bare system"),
    )


# ---------------------------------------------------------------------------
# 1.7 — cpu_supports_v3 pure function
# ---------------------------------------------------------------------------

class TestCpuSupportsV3(unittest.TestCase):

    def test_supported_string_returns_true(self):
        output = (
            "Subdirectories of glibc-hwcaps directories, in priority order:\n"
            "  x86-64-v4 (supported, searched)\n"
            "  x86-64-v3 (supported, searched)\n"
            "  x86-64-v2 (supported, searched)\n"
        )
        self.assertTrue(cpu_supports_v3(output))

    def test_not_supported_returns_false(self):
        output = (
            "Subdirectories of glibc-hwcaps directories, in priority order:\n"
            "  x86-64-v3 (unsupported, skipped)\n"
            "  x86-64-v2 (supported, searched)\n"
        )
        self.assertFalse(cpu_supports_v3(output))

    def test_empty_string_returns_false(self):
        self.assertFalse(cpu_supports_v3(""))

    def test_unrelated_string_returns_false(self):
        self.assertFalse(cpu_supports_v3("usage: ld-linux-x86-64.so.2 [OPTION]..."))

    def test_exact_substring_match(self):
        # Must contain "x86-64-v3 (supported" (without closing paren required)
        self.assertTrue(cpu_supports_v3("x86-64-v3 (supported, searched)"))
        self.assertFalse(cpu_supports_v3("x86-64-v3 (not-supported)"))


# ---------------------------------------------------------------------------
# 1.7 — [cachyos-v3] in pacman.conf and packages
# ---------------------------------------------------------------------------

class TestV3PacmanConf(unittest.TestCase):

    def _plan(self, kernel_id="linux-cachyos", cpu_v3=False, gaming=False):
        catalog = _make_catalog()
        sel = Selection(kernel_id, "dinit", [], {}, [], gaming, False)
        return resolve_plan(catalog, sel, cpu_v3=cpu_v3)

    def test_v3_linux_cachyos_inserts_cachyos_v3_before_cachyos(self):
        plan = self._plan(kernel_id="linux-cachyos", cpu_v3=True)
        conf = next(f for f in plan.files if f.path == "/etc/pacman.conf")
        v3_pos = conf.content.index("[cachyos-v3]")
        cachyos_pos = conf.content.index("[cachyos]\n")
        self.assertLess(v3_pos, cachyos_pos,
                        "[cachyos-v3] must appear before [cachyos]")

    def test_v3_linux_cachyos_conf_has_v3_mirrorlist_include(self):
        plan = self._plan(kernel_id="linux-cachyos", cpu_v3=True)
        conf = next(f for f in plan.files if f.path == "/etc/pacman.conf")
        self.assertIn("cachyos-v3-mirrorlist", conf.content)

    def test_v3_linux_cachyos_adds_mirrorlist_package(self):
        plan = self._plan(kernel_id="linux-cachyos", cpu_v3=True)
        self.assertIn("cachyos-v3-mirrorlist", plan.packages)

    def test_no_v3_flag_no_v3_repo(self):
        plan = self._plan(kernel_id="linux-cachyos", cpu_v3=False)
        conf = next(f for f in plan.files if f.path == "/etc/pacman.conf")
        self.assertNotIn("[cachyos-v3]", conf.content)
        self.assertNotIn("cachyos-v3-mirrorlist", plan.packages)

    def test_v3_true_but_linux_zen_no_v3_repo(self):
        plan = self._plan(kernel_id="linux-zen", cpu_v3=True)
        conf = next(f for f in plan.files if f.path == "/etc/pacman.conf")
        self.assertNotIn("[cachyos-v3]", conf.content)
        self.assertNotIn("cachyos-v3-mirrorlist", plan.packages)

    def test_no_cachyos_core_v3_or_extra_v3(self):
        plan = self._plan(kernel_id="linux-cachyos", cpu_v3=True)
        for pkg in plan.packages:
            self.assertNotIn("cachyos-core-v3", pkg)
            self.assertNotIn("cachyos-extra-v3", pkg)

    def test_real_catalog_v3_active(self):
        catalog = load_catalog(_CATALOG_PATH)
        sel = Selection("linux-cachyos", "dinit", [], {}, [], False, False)
        plan = resolve_plan(catalog, sel, cpu_v3=True)
        conf = next(f for f in plan.files if f.path == "/etc/pacman.conf")
        v3_pos = conf.content.index("[cachyos-v3]")
        cachyos_pos = conf.content.index("[cachyos]\n")
        self.assertLess(v3_pos, cachyos_pos)
        self.assertIn("cachyos-v3-mirrorlist", plan.packages)

    def test_real_catalog_v3_inactive(self):
        catalog = load_catalog(_CATALOG_PATH)
        sel = Selection("linux-cachyos", "dinit", [], {}, [], False, False)
        plan = resolve_plan(catalog, sel, cpu_v3=False)
        conf = next(f for f in plan.files if f.path == "/etc/pacman.conf")
        self.assertNotIn("[cachyos-v3]", conf.content)
        self.assertNotIn("cachyos-v3-mirrorlist", plan.packages)


# ---------------------------------------------------------------------------
# 1.8 — cmdline contains nowatchdog + zswap.enabled=0
# ---------------------------------------------------------------------------

class TestCmdline(unittest.TestCase):

    def test_grub_script_has_nowatchdog(self):
        from glue_installer.disks import _GRUB_BRAND_SCRIPT
        self.assertIn("nowatchdog", _GRUB_BRAND_SCRIPT)

    def test_grub_script_has_zswap_disabled(self):
        from glue_installer.disks import _GRUB_BRAND_SCRIPT
        self.assertIn("zswap.enabled=0", _GRUB_BRAND_SCRIPT)

    def test_grub_cmdline_both_flags_in_every_branch(self):
        from glue_installer.disks import _GRUB_BRAND_SCRIPT
        # Only the sed/echo lines that write the value must carry the flags
        setting_lines = [
            l for l in _GRUB_BRAND_SCRIPT.splitlines()
            if "GRUB_CMDLINE_LINUX_DEFAULT=" in l and (
                l.strip().startswith("sed ") or l.strip().startswith("echo ")
            )
        ]
        self.assertGreater(len(setting_lines), 0, "no cmdline-setting lines found")
        for line in setting_lines:
            self.assertIn("nowatchdog", line, line)
            self.assertIn("zswap.enabled=0", line, line)


# ---------------------------------------------------------------------------
# 1.9 — MESA and GL shader cache sizes in glue-gaming.sh
# ---------------------------------------------------------------------------

class TestGamingShaderCache(unittest.TestCase):

    def _gaming_file(self, **kw):
        catalog = _make_catalog()
        sel = Selection("linux-cachyos", "dinit", [], {}, [], True, False)
        plan = resolve_plan(catalog, sel, **kw)
        return next(f for f in plan.files if f.path == "/etc/profile.d/glue-gaming.sh")

    def test_mesa_cache_max_size_12g(self):
        f = self._gaming_file()
        self.assertIn("MESA_SHADER_CACHE_MAX_SIZE=12G", f.content)

    def test_gl_shader_disk_cache_size_12gb(self):
        f = self._gaming_file()
        self.assertIn("__GL_SHADER_DISK_CACHE_SIZE=12000000000", f.content)

    def test_existing_nvidia_vars_still_present(self):
        f = self._gaming_file()
        self.assertIn("__GL_SHADER_DISK_CACHE=1", f.content)
        self.assertIn("__GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1", f.content)

    def test_real_catalog_gaming_has_both_cache_vars(self):
        catalog = load_catalog(_CATALOG_PATH)
        sel = Selection("linux-cachyos", "dinit", [], {}, [], True, False)
        plan = resolve_plan(catalog, sel)
        f = next(f for f in plan.files if f.path == "/etc/profile.d/glue-gaming.sh")
        self.assertIn("MESA_SHADER_CACHE_MAX_SIZE=12G", f.content)
        self.assertIn("__GL_SHADER_DISK_CACHE_SIZE=12000000000", f.content)


if __name__ == "__main__":
    unittest.main()
