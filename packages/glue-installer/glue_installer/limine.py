"""Limine bootloader for the installed system.

Pure module: no subprocess, no filesystem access. Produces the text of
/boot/limine.conf, the text of /etc/glue/boot.conf and the executor Steps
that hand both to the target. The ESP is mounted at /boot, so kernel +
initramfs live next to limine.conf and `boot():/` resolves to that partition.

The installer does not copy Limine files or run efibootmgr from the
live side. It writes /etc/glue/boot.conf in the chroot and runs the target's
`glue-boot-update --deploy` (packages/glue-boot), the same script the pacman
hooks run on every kernel/limine update. limine_conf() here is the reference
text; glue-boot-update must reproduce it byte for byte (tests/test_boot_update.py).

Option names come from Limine v12.9.0 CONFIG.md
(https://raw.githubusercontent.com/limine-bootloader/limine/v12.9.0/CONFIG.md);
_CONFIG_KEYS lists every key this module may emit - nothing else is used.
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
    "wallpaper", "wallpaper_style",
    "term_background", "term_foreground", "term_palette",
    "term_palette_bright", "comment", "protocol", "kernel_path",
    "module_path", "cmdline",
    # chainload: EFI `path`/`image_path`; BIOS `partition`,
    # `mbr_id`, `gpt_uuid` (all in CONFIG.md v12.9.0 "Chainload protocol")
    "image_path", "partition", "mbr_id", "gpt_uuid",
})

# packages/glue-branding/palette.json boot_* colours (RRGGBB, no '#'): the
# menu sits on the Stillwater wallpaper that glue-boot-update copies to /boot.
_PALETTE_BG = "E9E9E7"
_PALETTE_LINES = "3A3F44"
_PALETTE_TEXT = "2B2F33"
WALLPAPER_SRC = "/usr/share/backgrounds/glue/wallpaper.png"
WALLPAPER_ESP = "glue-wallpaper.png"

BASE_CMDLINE: Tuple[str, ...] = (
    "rw", "quiet", "loglevel=3", "rd.udev.log_level=3", "nowatchdog",
    "zswap.enabled=0",
)

_VALID_FIRMWARE = frozenset({"uefi", "bios"})
_BOOT_CONF_PATH = "/etc/glue/boot.conf"
_ROOT_PLACEHOLDER = "@ROOT_UUID@"
_RESUME_PLACEHOLDER = "@RESUME_UUID@"
_HEREDOC_TAG = "GLUE_BOOT_CONF"
_TIMEOUT = 5
# Characters that would be interpreted by sh inside the double-quoted
# GLUE_BOOT_CMDLINE value (boot.conf is sourced with `.`).
_SHELL_UNSAFE = frozenset('"$`\\')


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
                firmware: str = "uefi",
                extra_kernels: Tuple[str, ...] = (),
                foreign_entries: Tuple[str, ...] = ()) -> str:
    """Full /boot/limine.conf: amber globals + Glue entry + fallback entry.

    firmware is validated only; the Linux entries are identical on UEFI and
    BIOS (boot():/ is the partition holding this file on both).
    extra_kernels: other kernel packages present in /boot (as glue-boot-update
    finds them, sorted); each gets a '(k)' and a '(k, fallback initramfs)'
    entry after the primary pair, same cmdline. Empty tuple: text unchanged.
    foreign_entries: texts of /etc/glue/boot.d/*.conf (osdetect.boot_d_files,
    LC_ALL=C order), each appended after ALL kernel entries preceded by one
    blank line, exactly as glue-boot-update concatenates them.
    """
    _check_firmware(firmware)
    cmdline = kernel_cmdline(spec, root_uuid, resume_uuid)
    k = spec.kernel
    lines = [
        "# Glue Linux boot menu (written by the installer; glue-boot-update regenerates it)",
        f"timeout: {_TIMEOUT}",
        "interface_branding: Glue Linux",
        f"interface_branding_colour: {_PALETTE_TEXT}",
        f"interface_help_colour: {_PALETTE_LINES}",
        f"backdrop: {_PALETTE_BG}",
        f"wallpaper: boot():/{WALLPAPER_ESP}",
        "wallpaper_style: stretched",
        f"term_background: 30{_PALETTE_BG}",
        f"term_foreground: {_PALETTE_TEXT}",
        "",
        *_entry("Glue Linux", k, f"initramfs-{k}.img", cmdline),
        "",
        *_entry("Glue Linux (fallback initramfs)", k,
                f"initramfs-{k}-fallback.img", cmdline),
    ]
    for extra in extra_kernels:
        _check_token(extra, "extra kernel")
        if "/" in extra or extra == k:
            raise ValueError(f"extra kernel must be another package name: {extra!r}")
        lines += [
            "",
            *_entry(f"Glue Linux ({extra})", extra, f"initramfs-{extra}.img", cmdline),
            "",
            *_entry(f"Glue Linux ({extra}, fallback initramfs)", extra,
                    f"initramfs-{extra}-fallback.img", cmdline),
        ]
    for fragment in foreign_entries:
        lines += ["", *_check_fragment(fragment).split("\n")]
    return "\n".join(lines) + "\n"


def _check_fragment(text: str) -> str:
    """A boot.d fragment: non-empty, first non-blank line '#…' or '/…', only
    CONFIG.md keys. Returns the text without trailing newlines (what the
    shell's $(cat file) yields)."""
    body = text.rstrip("\n")
    first = next((l for l in body.split("\n") if l.strip()), "")
    if not first or first[0] not in "#/":
        raise ValueError(f"boot.d fragment must start with '#' or '/': {text!r}")
    unknown = set(emitted_keys(body)) - _CONFIG_KEYS
    if unknown:
        raise ValueError(f"boot.d fragment uses unknown Limine keys: {sorted(unknown)}")
    return body


def boot_conf(spec: BootSpec, firmware: str, root_uuid: str,
              resume_uuid: Optional[str] = None) -> str:
    """Text of /etc/glue/boot.conf: POSIX-sh assignments read by
    glue-boot-update. GLUE_BOOT_CMDLINE holds BASE_CMDLINE + sorted extras
    (no root=, no resume=: the script adds those from the UUID keys)."""
    _check_firmware(firmware)
    _check_spec(spec)
    _check_token(root_uuid, "root_uuid")
    resume = resume_uuid if (spec.resume and resume_uuid) else ""
    if resume:
        _check_token(resume, "resume_uuid")
    extras = sorted(set(spec.cmdline_extra))
    for extra in extras:
        if _SHELL_UNSAFE & set(extra):
            raise ValueError(f"cmdline_extra entry has shell-special characters: {extra!r}")
    cmdline = " ".join([*BASE_CMDLINE, *extras])
    lines = [
        "# Glue Linux boot settings; glue-boot-update turns these into /boot/limine.conf",
        f"GLUE_BOOT_FIRMWARE={firmware}",
        f"GLUE_BOOT_KERNEL={spec.kernel}",
        f"GLUE_BOOT_ROOT_UUID={root_uuid}",
        f"GLUE_BOOT_RESUME_UUID={resume}",
        f'GLUE_BOOT_CMDLINE="{cmdline}"',
        f"GLUE_BOOT_TIMEOUT={_TIMEOUT}",
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
    need resume_offset - not supported here."""
    return bool(hibernate and disk_plan is not None
                and disk_plan.mode == "erase"
                and swap_partition(disk_plan) is not None)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def bootloader_packages(disk_plan: DiskPlan) -> List[str]:
    """Packages the bootloader needs in the basestrap set, sorted.
    glue-boot brings glue-boot-update + the pacman hooks."""
    packages = ["glue-boot", "limine"]
    if disk_plan.firmware == "uefi":
        packages.append("efibootmgr")
    return sorted(packages)


def _writer_script(spec: BootSpec, firmware: str, swap_path: Optional[str]) -> str:
    """Chrooted sh: resolve UUIDs, (resume hook + initramfs), write
    /etc/glue/boot.conf, then `glue-boot-update --deploy` on the target."""
    resume = spec.resume and swap_path is not None
    template = boot_conf(spec, firmware, _ROOT_PLACEHOLDER,
                         _RESUME_PLACEHOLDER if resume else None)
    if _HEREDOC_TAG in template:
        raise ValueError("boot.conf text collides with the heredoc delimiter")
    lines = [
        "set -e",
        'ROOT_UUID=$(findmnt -no UUID /)',
        '[ -n "$ROOT_UUID" ] || { echo "cannot resolve the UUID of /" >&2; exit 1; }',
    ]
    if resume:
        lines += [
            f'RESUME_UUID=$(blkid -s UUID -o value {swap_path})',
            '[ -n "$RESUME_UUID" ] || { echo "cannot resolve the swap UUID" >&2; exit 1; }',
            # resume hook right after filesystems (idempotent), then rebuild
            "grep -Eq '^HOOKS=.*[ (]resume[ )]' /etc/mkinitcpio.conf || "
            "sed -i -E 's/^(HOOKS=.*[ (]filesystems)([ )])/\\1 resume\\2/' /etc/mkinitcpio.conf",
            "mkinitcpio -P",
        ]
    lines += [
        "mkdir -p /etc/glue",
        f"cat > {_BOOT_CONF_PATH} <<'{_HEREDOC_TAG}'",
        template.rstrip("\n"),
        _HEREDOC_TAG,
        f'sed -i "s|{_ROOT_PLACEHOLDER}|$ROOT_UUID|g" {_BOOT_CONF_PATH}',
    ]
    if resume:
        lines.append(f'sed -i "s|{_RESUME_PLACEHOLDER}|$RESUME_UUID|g" {_BOOT_CONF_PATH}')
    lines.append("glue-boot-update --deploy")
    return "\n".join(lines) + "\n"


def bootloader_steps(disk_plan: DiskPlan, spec: BootSpec, *,
                     target: str = "/mnt") -> List[Step]:
    """Compile the bootloader Step(s). Runs LAST, after service enabling.

    Everything happens inside the chroot: one `artix-chroot sh -c`
    resolves the root (and swap) UUID, writes /etc/glue/boot.conf and runs
    `glue-boot-update --deploy`, which writes /boot/limine.conf, copies
    BOOTX64.EFI to EFI/BOOT + EFI/limine and registers 'Glue Linux' with
    efibootmgr (UEFI) or copies limine-bios.sys and runs `limine
    bios-install` (BIOS). Kernel/limine updates re-run the same script.
    """
    _check_firmware(disk_plan.firmware)
    _check_spec(spec)
    if not disk_plan.device_path:
        raise DiskError("bootloader_steps: disk plan has no device path")
    if disk_plan.firmware == "uefi" and not any(
            p.mountpoint == "/boot" for p in disk_plan.partitions):
        raise DiskError("UEFI disk plan has no EFI System Partition at /boot")
    t = target.rstrip("/")
    swap = swap_partition(disk_plan)
    swap_path = swap.path if (spec.resume and swap is not None) else None
    what = f"Write {_BOOT_CONF_PATH}"
    if swap_path is not None:
        what += " (with hibernation resume), rebuild the initramfs"
    what += " and deploy Limine with glue-boot-update (limine.conf)"
    return [RunCommand(
        argv=["artix-chroot", t, "sh", "-c",
              _writer_script(spec, disk_plan.firmware, swap_path)],
        description=what,
    )]
