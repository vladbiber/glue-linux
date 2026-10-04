"""Offline install = clone of the live system (roadmap 3.6, ADR-022).

Pure module: no subprocess, no filesystem access. Compiles the executor Steps
that copy the running live ISO to the target disk with rsync, strip the
live-only pieces, apply the Glue configuration (executor.config_steps) and
boot it with Limine through `glue-boot-update --deploy` — the same mechanism
the online install and the pacman hooks use. No basestrap, no pacman -S, no
grub, no network: everything the clone needs is already on the live medium.
"""

from __future__ import annotations

from typing import List, Tuple

from glue_installer.disks import DiskPlan, disk_steps
from glue_installer.disk_swap import swap_steps, swapfile_fstab_steps
from glue_installer.executor import RunCommand, Step, config_steps
from glue_installer.limine import BootSpec, bootloader_steps
from glue_installer.plan import InstallPlan

# The live medium runs the stock Artix kernel under runit (profile.yaml);
# the clone inherits both — a different kernel would need basestrap (online).
CLONE_KERNEL = "linux"
CLONE_INIT = "runit"
LIVE_USER = "glue"

# Packages that only make sense on the live medium; removed from the clone
# when (and only when) pacman knows them — nothing is downloaded.
LIVE_ONLY_PACKAGES: Tuple[str, ...] = (
    "glue-installer", "rsync", "os-prober", "grub",
    "artix-live-dinit", "artix-live-openrc", "artix-live-runit", "artix-live-s6",
    "artix-grub-live", "calamares", "kpmcore", "ckbcomp",
)

# root-overlay files of the ISO: installer autostart + passwordless sudo.
LIVE_ONLY_FILES: Tuple[str, ...] = (
    "/etc/profile.d/glue-live.sh",
    "/etc/sudoers.d/10-glue-live",
)

# Pseudo filesystems, the target mount itself, caches/logs, the live user and
# everything the clone regenerates (fstab, machine-id, /boot: the ESP or the
# ISO's loader files).
RSYNC_EXCLUDES: Tuple[str, ...] = (
    "/dev/*", "/proc/*", "/sys/*", "/run/*", "/tmp/*", "/mnt/*", "/media/*",
    "/lost+found", "/boot/*", "/etc/fstab", "/etc/machine-id",
    "/var/cache/pacman/pkg/*", "/var/log/*", "/var/tmp/*",
    f"/home/{LIVE_USER}", "/swapfile", "/run/artix",
)

# Hook list of a normal installed system (mkinitcpio.conf default + kms,
# keyboard, keymap, consolefont); the live initramfs hooks (artix-live,
# overlay, nfs…) must not survive on disk. limine.bootloader_steps inserts
# `resume` after `filesystems` when hibernation applies, so this rewrite
# runs BEFORE it.
MKINITCPIO_HOOKS = ("HOOKS=(base udev autodetect modconf kms keyboard keymap "
                    "consolefont block filesystems fsck)")

_AGETTY_CONF = "/etc/runit/sv/agetty-tty1/conf"
_FORBIDDEN_TOKENS = ("basestrap", "grub", "curl", "wget")


class CloneError(ValueError):
    """Raised when the clone request cannot be honoured (kernel, target)."""


def _check(boot: BootSpec, target: str) -> None:
    if boot.kernel != CLONE_KERNEL:
        raise CloneError(
            f"the offline clone boots the live kernel {CLONE_KERNEL!r}, "
            f"not {boot.kernel!r}")
    if target.rstrip("/") == "":
        raise CloneError(f"refusing to clone onto {target!r} (the live root itself)")


def rsync_step(target: str) -> Step:
    """Copy the live root to the target; -aAXH keeps ACLs/xattrs/hardlinks
    (pacman-tracked files rely on them), --numeric-ids keeps uid/gid."""
    t = target.rstrip("/")
    argv = ["rsync", "-aAXH", "--numeric-ids", "--info=progress2"]
    argv += [f"--exclude={pattern}" for pattern in RSYNC_EXCLUDES]
    argv += ["/", f"{t}/"]
    return RunCommand(argv=argv, description=f"Copy the live system to {t} (rsync)")


def cleanup_script() -> str:
    """Chrooted sh (set -e): drop the live user, autologin, live-only files and
    packages, give the clone its own machine-id, prepare /etc/glue/boot.d."""
    lines = [
        "set -e",
        # live user: autologin + passwordless sudo — the installed system gets
        # its own account from identity_steps. /home/glue was never copied, so
        # -r may complain about the missing home; the account must be gone.
        f"if id {LIVE_USER} >/dev/null 2>&1; then userdel -r {LIVE_USER} 2>/dev/null || true; fi",
        f"if id {LIVE_USER} >/dev/null 2>&1; then "
        f"echo 'could not remove the live user {LIVE_USER}' >&2; exit 1; fi",
        "rm -f " + " ".join(LIVE_ONLY_FILES),
        # tty1 logged root in without a password on the ISO; not on disk.
        f"[ ! -f {_AGETTY_CONF} ] || sed -i 's/ --autologin root//' {_AGETTY_CONF}",
        # live-only packages: only the ones pacman knows, built in the shell;
        # no network (the local db is enough for -R). Leaving them behind is
        # not fatal, so a refusal (dependency) only warns.
        'rm_pkgs=""',
        "for p in " + " ".join(LIVE_ONLY_PACKAGES) + "; do",
        '  if pacman -Qq "$p" >/dev/null 2>&1; then rm_pkgs="$rm_pkgs $p"; fi',
        "done",
        '[ -z "$rm_pkgs" ] || pacman -Rns --noconfirm $rm_pkgs || '
        'echo "warning: could not remove live-only packages:$rm_pkgs" >&2',
        # the ISO's machine-id was excluded: D-Bus and the greeter cache need a new one
        "rm -f /etc/machine-id && dbus-uuidgen --ensure=/etc/machine-id",
        "mkdir -p /etc/glue/boot.d",
    ]
    return "\n".join(lines) + "\n"


def kernel_script() -> str:
    """Chrooted sh (set -e): /boot was excluded from the copy, so put the
    kernel image(s) back exactly like mkinitcpio's 90-mkinitcpio-install hook
    does (from /usr/lib/modules/<ver>/vmlinuz + pkgbase), switch the hooks
    from the live ones to a disk install and build the initramfs."""
    lines = [
        "set -e",
        "for d in /usr/lib/modules/*; do",
        '  [ -f "$d/pkgbase" ] || continue',
        '  install -Dm644 "$d/vmlinuz" "/boot/vmlinuz-$(cat "$d/pkgbase")"',
        "done",
        f"[ -f /boot/vmlinuz-{CLONE_KERNEL} ] || "
        f"{{ echo 'kernel {CLONE_KERNEL} not found in /usr/lib/modules' >&2; exit 1; }}",
        f"sed -i 's|^HOOKS=.*|{MKINITCPIO_HOOKS}|' /etc/mkinitcpio.conf",
        "mkinitcpio -P",
    ]
    return "\n".join(lines) + "\n"


def _no_forbidden(steps: List[Step]) -> None:
    """Defensive self-check: a clone never installs packages or runs grub."""
    for step in steps:
        for arg in getattr(step, "argv", ()):
            for tok in _FORBIDDEN_TOKENS:
                if arg.startswith(tok):
                    raise CloneError(f"clone step uses forbidden tool {tok!r}: {arg!r}")
            if "pacman -S" in arg:
                raise CloneError(f"clone step installs packages: {arg!r}")


def clone_steps(plan: InstallPlan, disk_plan: DiskPlan, *, boot: BootSpec,
                target: str = "/mnt") -> List[Step]:
    """Compile the offline install. Order (fixed):
    disk_steps → swap_steps → rsync → fstabgen → swapfile fstab fix →
    cleanup chroot → config_steps(runit, only_if_present) → kernel chroot →
    bootloader_steps (last: it writes /etc/glue/boot.conf, may add the
    resume hook, runs `glue-boot-update --deploy`).
    Raises CloneError for a kernel other than CLONE_KERNEL or target '/'/''.
    """
    _check(boot, target)
    t = target.rstrip("/")
    steps: List[Step] = []
    steps.extend(disk_steps(disk_plan, target=target))
    steps.extend(swap_steps(disk_plan, target=target))
    steps.append(rsync_step(target))
    steps.append(RunCommand(
        argv=["sh", "-c", f"fstabgen -U {t} >> {t}/etc/fstab"],
        description=f"Generate fstab → {t}/etc/fstab",
    ))
    steps.extend(swapfile_fstab_steps(disk_plan, target=target))
    steps.append(RunCommand(
        argv=["artix-chroot", t, "sh", "-c", cleanup_script()],
        description=f"Remove the live user {LIVE_USER} (userdel), autologin and live-only packages",
    ))
    steps.extend(config_steps(plan, target=target, init_id=CLONE_INIT,
                              only_if_present=True))
    steps.append(RunCommand(
        argv=["artix-chroot", t, "sh", "-c", kernel_script()],
        description=f"Install vmlinuz-{CLONE_KERNEL} to /boot and rebuild the initramfs",
    ))
    steps.extend(bootloader_steps(disk_plan, boot, target=target))
    _no_forbidden(steps)
    return steps
