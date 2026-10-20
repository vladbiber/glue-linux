"""Encrypted root (LUKS) for Calamares installs.

Pure module: no subprocess, no filesystem access. Reads the globalstorage
`partitions` list Calamares' FillGlobalStorageJob writes (an encrypted
partition carries luksMapperName + luksUuid; `fs` is the inner filesystem)
and produces what boots it: the root is unlocked by mkinitcpio's busybox
`encrypt` hook from `cryptdevice=UUID=<luks>:<name> root=/dev/mapper/<name>`.
/boot stays a plain partition (the ESP on UEFI) because Limine cannot open
LUKS. Artix's dinit-rc/runit-rc never read /etc/crypttab, so only the root
can be unlocked at boot: an encrypted swap partition is left unused (Glue
makes a swapfile inside the encrypted root instead), any other encrypted
mount is refused before anything is installed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from glue_installer.executor import RunCommand, Step

MAPPER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,126}$")
_SWAP_FS = ("linuxswap", "swap")
MKINITCPIO_CONF = "/etc/mkinitcpio.conf"


class CryptError(ValueError):
    """A partition layout Glue cannot boot; the message is shown as is."""


@dataclass(frozen=True)
class CryptRoot:
    device: str   # the LUKS partition, e.g. /dev/nvme0n1p2
    name: str     # mapper name, e.g. luks-<uuid>


@dataclass
class CryptLayout:
    root: Optional[CryptRoot] = None
    # encrypted swap partitions Glue leaves unused (raw device paths)
    dropped_swap: List[str] = field(default_factory=list)
    # their fstab fs_spec (/dev/mapper/<name>), removed by gluefstab
    dropped_fstab: List[str] = field(default_factory=list)


def _device(part) -> str:
    dev = part.get("device")
    if not isinstance(dev, str) or not dev.startswith("/dev/") or ".." in dev \
            or any(c.isspace() for c in dev):
        raise CryptError(f"encrypted partition has no usable device path: {dev!r}")
    return dev


def crypt_layout(partitions) -> CryptLayout:
    """Classify the encrypted partitions of a Calamares partitions list.
    Raises CryptError for layouts the installed system could not boot."""
    layout = CryptLayout()
    boot_plain = False
    for part in partitions or []:
        if not isinstance(part, dict):
            continue
        mp = part.get("mountPoint") or ""
        name = part.get("luksMapperName") or ""
        fs = (part.get("fs") or "").lower()
        if mp == "/boot":
            if name:
                raise CryptError(
                    "The /boot partition must not be encrypted: the boot menu reads the "
                    "kernel from it. Go back to Partitions and turn off encryption for /boot.")
            boot_plain = True
            continue
        if not name:
            continue
        if not isinstance(name, str) or not MAPPER_RE.match(name):
            raise CryptError(f"unusable LUKS mapper name {name!r}")
        if mp == "/":
            layout.root = CryptRoot(_device(part), name)
        elif fs in _SWAP_FS and not mp:
            if part.get("claimed"):
                layout.dropped_swap.append(_device(part))
                layout.dropped_fstab.append(f"/dev/mapper/{name}")
        elif mp:
            raise CryptError(
                f"{mp} is on an encrypted partition. Glue Linux can unlock only the "
                "root partition (/) when it starts. Go back to Partitions and turn off "
                f"encryption for {mp}, or leave it without a mount point.")
    if layout.root is not None and not boot_plain:
        raise CryptError(
            "An encrypted root needs a separate, unencrypted /boot partition (on UEFI "
            "the EFI system partition mounted at /boot). Go back to Partitions and add "
            "one, or pick Erase disk, which creates it for you.")
    if layout.root is None:
        layout.dropped_swap.clear()
        layout.dropped_fstab.clear()
    return layout


def crypt_warnings(layout: CryptLayout) -> List[str]:
    if not layout.dropped_swap:
        return []
    return [f"Encrypted swap partition {', '.join(layout.dropped_swap)} left unused: "
            "Glue Linux unlocks only the root at boot and uses a swapfile inside the "
            "encrypted root instead (no hibernation)"]


_HOOK_RE = r"[ (]{hook}[ )]"


def ensure_encrypt_hook(hooks: str) -> str:
    """`encrypt` right before `filesystems`, `keyboard` and `keymap` before it
    when missing (the passphrase prompt needs both). Idempotent; mirror of
    ENCRYPT_HOOK_SCRIPT's sed. A line without `filesystems` is unchanged."""
    if not hooks.startswith("HOOKS="):
        return hooks
    if not re.search(_HOOK_RE.format(hook="encrypt"), hooks):
        hooks = re.sub(r"^(HOOKS=.*[ (])(filesystems[ )])", r"\1encrypt \2", hooks, count=1)
    if not re.search(_HOOK_RE.format(hook="encrypt"), hooks):
        return hooks
    for hook in ("keyboard", "keymap"):
        if not re.search(_HOOK_RE.format(hook=hook), hooks):
            hooks = re.sub(r"^(HOOKS=.*[ (])(encrypt[ )])", rf"\1{hook} \2", hooks, count=1)
    return hooks


ENCRYPT_HOOK_SCRIPT = (
    f"f={MKINITCPIO_CONF}\n"
    "grep -Eq '^HOOKS=.*[ (]encrypt[ )]' $f || "
    "sed -i -E 's/^(HOOKS=.*[ (])(filesystems[ )])/\\1encrypt \\2/' $f\n"
    "grep -Eq '^HOOKS=.*[ (]encrypt[ )]' $f || "
    "{ echo \"no filesystems hook in $f, cannot add encrypt\" >&2; exit 1; }\n"
    "for h in keyboard keymap; do\n"
    "  grep -Eq \"^HOOKS=.*[ (]$h[ )]\" $f || "
    "sed -i -E \"s/^(HOOKS=.*[ (])(encrypt[ )])/\\\\1$h \\\\2/\" $f\n"
    "done\n")


def rebuild_steps(target: str) -> List[Step]:
    """After Calamares' keyboard job wrote /etc/vconsole.conf: rebuild the
    initramfs so the passphrase prompt uses that keymap, then rewrite
    limine.conf (with Secure Boot the initramfs hash is in it)."""
    t = target.rstrip("/")
    return [RunCommand(
        argv=["artix-chroot", t, "sh", "-c", "set -e\nmkinitcpio -P\nglue-boot-update\n"],
        description="Rebuild the initramfs with the console keymap for the disk passphrase")]


def drop_fstab_specs(text: str, specs) -> str:
    """fstab text with the lines whose fs_spec is in specs commented out."""
    wanted = set(specs)
    out = []
    for line in text.splitlines(keepends=True):
        fields = line.split()
        if fields and not fields[0].startswith("#") and fields[0] in wanted:
            line = "# unused (Glue unlocks only the root at boot): " + line.lstrip()
        out.append(line)
    return "".join(out)
