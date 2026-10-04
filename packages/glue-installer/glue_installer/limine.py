"""Limine bootloader for the installed system (roadmap 3.1 + 3.2).

Pure module: no subprocess, no filesystem access. Produces the text of
/boot/limine.conf and the executor Steps that put Limine on the ESP (UEFI)
or in the MBR (BIOS). The ESP is mounted at /boot, so kernel + initramfs
live next to limine.conf and `boot():/` resolves to that partition.

Option names come from Limine v12.9.0 CONFIG.md
(https://raw.githubusercontent.com/limine-bootloader/limine/v12.9.0/CONFIG.md);
_CONFIG_KEYS lists every key this module may emit — nothing else is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from glue_installer.disks import DiskError, DiskPlan, PartitionSpec
from glue_installer.executor import RunCommand, Step

# Keys verified in CONFIG.md v12.9.0 (globals + `protocol: linux` locals).
_CONFIG_KEYS = frozenset({
    "timeout", "default_entry", "interface_branding",
    "interface_branding_colour", "interface_help_colour", "backdrop",
    "term_background", "term_foreground", "term_palette",
    "term_palette_bright", "comment", "protocol", "kernel_path",
    "module_path", "cmdline",
})

# packages/glue-branding/palette.json: bg, lines, text (RRGGBB, no '#').
_PALETTE_BG = "100A02"
_PALETTE_LINES = "A66900"
_PALETTE_TEXT = "F1B00A"

BASE_CMDLINE: Tuple[str, ...] = (
    "rw", "quiet", "loglevel=3", "rd.udev.log_level=3", "nowatchdog",
    "zswap.enabled=0",
)

_VALID_FIRMWARE = frozenset({"uefi", "bios"})
_LIMINE_SHARE = "/usr/share/limine"
_CONF_PATH = "/boot/limine.conf"
_ROOT_PLACEHOLDER = "@ROOT_UUID@"
_RESUME_PLACEHOLDER = "@RESUME_UUID@"
_HEREDOC_TAG = "GLUE_LIMINE_CONF"


@dataclass(frozen=True)
class BootSpec:
    kernel: str                            # package name: linux-cachyos, linux
    cmdline_extra: Tuple[str, ...] = ()    # e.g. ('amd_pstate=active',)
    resume: bool = False                   # hibernation: resume= + hook


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _check_token(value: str, what: str) -> None:
    if not value or any(c.isspace() for c in value):
        raise ValueError(f"{what} must be a non-empty token without spaces: {value!r}")


def _check_spec(spec: BootSpec) -> None:
    _check_token(spec.kernel, "kernel")
    if "/" in spec.kernel:
        raise ValueError(f"kernel must be a package name, not a path: {spec.kernel!r}")
    for extra in spec.cmdline_extra:
        _check_token(extra, "cmdline_extra entry")


def _check_firmware(firmware: str) -> None:
    if firmware not in _VALID_FIRMWARE:
        raise ValueError(
            f"Unknown firmware: {firmware!r}; expected one of {sorted(_VALID_FIRMWARE)}")


# ---------------------------------------------------------------------------
# limine.conf text
# ---------------------------------------------------------------------------

def kernel_cmdline(spec: BootSpec, root_uuid: str,
                   resume_uuid: Optional[str] = None) -> str:
    """root=UUID=… + BASE_CMDLINE + sorted extras + optional resume=UUID=…"""
    _check_spec(spec)
    _check_token(root_uuid, "root_uuid")
    parts = [f"root=UUID={root_uuid}", *BASE_CMDLINE,
             *sorted(set(spec.cmdline_extra))]
    if spec.resume and resume_uuid:
        _check_token(resume_uuid, "resume_uuid")
        # Same shape as swap.resume_cmdline (which only accepts real UUIDs;
        # the chroot writer passes a placeholder here).
        parts.append(f"resume=UUID={resume_uuid}")
    return " ".join(parts)


def _entry(title: str, kernel: str, initramfs: str, cmdline: str) -> List[str]:
    return [
        f"/{title}",
        "protocol: linux",
        f"kernel_path: boot():/vmlinuz-{kernel}",
        f"module_path: boot():/{initramfs}",
        f"cmdline: {cmdline}",
    ]


def limine_conf(spec: BootSpec, root_uuid: str,
                resume_uuid: Optional[str] = None,
                firmware: str = "uefi") -> str:
    """Full /boot/limine.conf: amber globals + Glue entry + fallback entry.

    firmware is validated only; the Linux entries are identical on UEFI and
    BIOS (boot():/ is the partition holding this file on both).
    """
    _check_firmware(firmware)
    cmdline = kernel_cmdline(spec, root_uuid, resume_uuid)
    k = spec.kernel
    lines = [
        "# Glue Linux boot menu (written by the installer; glue-boot-update regenerates it)",
        "timeout: 5",
        "interface_branding: Glue Linux",
        f"interface_branding_colour: {_PALETTE_TEXT}",
        f"interface_help_colour: {_PALETTE_LINES}",
        f"backdrop: {_PALETTE_BG}",
        f"term_background: 00{_PALETTE_BG}",
        f"term_foreground: {_PALETTE_TEXT}",
        "",
        *_entry("Glue Linux", k, f"initramfs-{k}.img", cmdline),
        "",
        *_entry("Glue Linux (fallback initramfs)", k,
                f"initramfs-{k}-fallback.img", cmdline),
    ]
    return "\n".join(lines) + "\n"


def emitted_keys(text: str) -> List[str]:
    """Option names (`key: value`) present in a limine.conf text."""
    keys = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("/") or ":" not in s:
            continue
        keys.append(s.split(":", 1)[0].strip())
    return keys


# ---------------------------------------------------------------------------
# Helpers for __main__ (pure)
# ---------------------------------------------------------------------------

def kernel_name(packages) -> str:
    """The kernel package (first 'linux*' that is not a -headers package)."""
    for pkg in packages:
        if pkg.startswith("linux") and not pkg.endswith("-headers"):
            return pkg
    raise ValueError(f"no kernel package in {list(packages)!r}")


def swap_partition(disk_plan: Optional[DiskPlan]) -> Optional[PartitionSpec]:
    if disk_plan is None:
        return None
    return next((p for p in disk_plan.partitions if p.mountpoint == "swap"), None)


def resume_wanted(disk_plan: Optional[DiskPlan], hibernate: bool) -> bool:
    """resume= only for a NEW swap partition (erase mode). A swapfile would
    need resume_offset — not supported here."""
    return bool(hibernate and disk_plan is not None
                and disk_plan.mode == "erase"
                and swap_partition(disk_plan) is not None)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def bootloader_packages(disk_plan: DiskPlan) -> List[str]:
    """Packages the bootloader needs in the basestrap set, sorted."""
    packages = ["limine"]
    if disk_plan.firmware == "uefi":
        packages.append("efibootmgr")
    return sorted(packages)


def _writer_script(spec: BootSpec, swap_path: Optional[str]) -> str:
    """Chrooted sh: resolve UUIDs, write limine.conf, resume hook + initramfs."""
    resume = spec.resume and swap_path is not None
    template = limine_conf(
        spec, _ROOT_PLACEHOLDER, _RESUME_PLACEHOLDER if resume else None)
    if _HEREDOC_TAG in template:
        raise ValueError("limine.conf text collides with the heredoc delimiter")
    lines = [
        "set -e",
        'ROOT_UUID=$(findmnt -no UUID /)',
        '[ -n "$ROOT_UUID" ] || { echo "cannot resolve the UUID of /" >&2; exit 1; }',
    ]
    if resume:
        lines += [
            f'RESUME_UUID=$(blkid -s UUID -o value {swap_path})',
            '[ -n "$RESUME_UUID" ] || { echo "cannot resolve the swap UUID" >&2; exit 1; }',
        ]
    lines += [
        f"cat > {_CONF_PATH} <<'{_HEREDOC_TAG}'",
        template.rstrip("\n"),
        _HEREDOC_TAG,
        f'sed -i "s|{_ROOT_PLACEHOLDER}|$ROOT_UUID|g" {_CONF_PATH}',
    ]
    if resume:
        lines += [
            f'sed -i "s|{_RESUME_PLACEHOLDER}|$RESUME_UUID|g" {_CONF_PATH}',
            # resume hook right after filesystems (idempotent), then rebuild
            "grep -Eq '^HOOKS=.*[ (]resume[ )]' /etc/mkinitcpio.conf || "
            "sed -i -E 's/^(HOOKS=.*[ (]filesystems)([ )])/\\1 resume\\2/' /etc/mkinitcpio.conf",
            "mkinitcpio -P",
        ]
    return "\n".join(lines) + "\n"


def bootloader_steps(disk_plan: DiskPlan, spec: BootSpec, *,
                     target: str = "/mnt") -> List[Step]:
    """Compile Limine install Steps. Runs LAST, after service enabling.

    UEFI: BOOTX64.EFI into /boot/EFI/BOOT and /boot/EFI/limine on the ESP
    (mounted at {target}/boot) + efibootmgr entry 'Glue Linux' (live side).
    BIOS: limine-bios.sys into /boot + `limine bios-install <disk>` (live
    side). Then one chrooted step writes /boot/limine.conf with the real UUIDs.
    """
    _check_firmware(disk_plan.firmware)
    _check_spec(spec)
    if not disk_plan.device_path:
        raise DiskError("bootloader_steps: disk plan has no device path")
    t = target.rstrip("/")
    disk = disk_plan.device_path
    steps: List[Step] = []

    if disk_plan.firmware == "uefi":
        esp = next((p for p in disk_plan.partitions if p.mountpoint == "/boot"), None)
        if esp is None:
            raise DiskError("UEFI disk plan has no EFI System Partition at /boot")
        efi_src = f"{t}{_LIMINE_SHARE}/BOOTX64.EFI"
        steps += [
            RunCommand(
                argv=["mkdir", "-p", f"{t}/boot/EFI/BOOT", f"{t}/boot/EFI/limine"],
                description="Create EFI directories on the boot partition",
            ),
            RunCommand(
                argv=["cp", efi_src, f"{t}/boot/EFI/BOOT/BOOTX64.EFI"],
                description="Install Limine as the removable-media EFI loader",
            ),
            RunCommand(
                argv=["cp", efi_src, f"{t}/boot/EFI/limine/BOOTX64.EFI"],
                description="Install Limine EFI loader (EFI/limine)",
            ),
            RunCommand(
                argv=[
                    "efibootmgr", "--create", "--disk", disk,
                    "--part", str(esp.number),
                    "--loader", "\\EFI\\limine\\BOOTX64.EFI",
                    "--label", "Glue Linux", "--unicode",
                ],
                description="Register 'Glue Linux' in the firmware boot menu (efibootmgr)",
            ),
        ]
    else:
        steps += [
            RunCommand(
                argv=["cp", f"{t}{_LIMINE_SHARE}/limine-bios.sys", f"{t}/boot/"],
                description="Install Limine BIOS stage (limine-bios.sys)",
            ),
            RunCommand(
                argv=["limine", "bios-install", disk],
                description=f"Install Limine to the boot sector of {disk}",
            ),
        ]

    swap = swap_partition(disk_plan)
    swap_path = swap.path if (spec.resume and swap is not None) else None
    what = "Write /boot/limine.conf"
    if swap_path is not None:
        what += " (with hibernation resume) and rebuild the initramfs"
    steps.append(RunCommand(
        argv=["artix-chroot", t, "sh", "-c", _writer_script(spec, swap_path)],
        description=what,
    ))
    return steps
