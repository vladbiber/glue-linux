"""
Identity layer for Wheatley Linux installer.

ADR-7: pure IdentitySpec dataclass + pure identity_steps() function.
No subprocess, curses, or filesystem access at import time or inside identity_steps.
Passwords flow ONLY via RunCommand.stdin — never in argv.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from wheatley_installer.executor import RunCommand, Step, WriteTargetFile


class IdentityError(Exception):
    """Raised for any identity validation failure. Never SystemExit."""


# ---------------------------------------------------------------------------
# Validation patterns
# ---------------------------------------------------------------------------

# RFC-952-ish: starts with [a-z0-9], rest [a-z0-9-], 1-63 chars total, no trailing '-'
_RE_HOSTNAME = re.compile(r'^[a-z0-9][a-z0-9\-]{0,62}$')

# POSIX username: starts with [a-z_], then [a-z0-9_-], max 32 chars
_RE_USERNAME = re.compile(r'^[a-z_][a-z0-9_\-]{0,31}$')

# Locale: ll_CC.UTF-8 (2-3 lowercase language code, 2-letter country, .UTF-8)
_RE_LOCALE = re.compile(r'^[a-z]{2,3}_[A-Z]{2}\.UTF-8$')

# Timezone: Area or Area/City or Area/Region/City (UTC and similar single-part names allowed)
_RE_TIMEZONE = re.compile(r'^[A-Za-z_+\-]+(/[A-Za-z_+\-]+){0,2}$')


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IdentitySpec:
    """Fully-validated identity configuration for a new Wheatley install.

    Raises IdentityError in __post_init__ if any field fails validation.
    """
    hostname: str
    username: str
    password: str
    locale: str = "en_US.UTF-8"
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        # hostname: match pattern AND must not end with '-'
        if not _RE_HOSTNAME.match(self.hostname) or self.hostname.endswith("-"):
            raise IdentityError(
                f"Invalid hostname '{self.hostname}': must be 1-63 chars, "
                "start with [a-z0-9], contain only [a-z0-9-], no trailing hyphen"
            )
        if not _RE_USERNAME.match(self.username):
            raise IdentityError(
                f"Invalid username '{self.username}': must start with [a-z_], "
                "contain only [a-z0-9_-], max 32 chars"
            )
        if not self.password:
            raise IdentityError("Password must not be empty")
        if not _RE_LOCALE.match(self.locale):
            raise IdentityError(
                f"Invalid locale '{self.locale}': expected form ll_CC.UTF-8 "
                "(e.g. en_US.UTF-8)"
            )
        if not _RE_TIMEZONE.match(self.timezone):
            raise IdentityError(
                f"Invalid timezone '{self.timezone}': expected Area/City form "
                "(e.g. Europe/London, UTC, America/New_York)"
            )


# ---------------------------------------------------------------------------
# Pure step compiler
# ---------------------------------------------------------------------------

def identity_steps(spec: IdentitySpec, *, target: str = "/mnt") -> List[Step]:
    """Compile identity configuration Steps for the target system.

    Steps are produced in a fixed, deterministic order:
      1. /etc/hostname
      2. /etc/hosts
      3. timezone symlink (arch-chroot ln -sf)
      4. hwclock --systohc
      5. /etc/locale.gen write
      6. locale-gen (arch-chroot)
      7. /etc/locale.conf write
      8. useradd -m -G wheel -s /bin/bash <username>
      9. chpasswd for the new user (password in stdin only)
     10. chpasswd for root (password in stdin only)
     11. /etc/sudoers.d/10-wheel (mode 0o440)

    Pure function: no I/O, no subprocess calls. Password appears ONLY in
    RunCommand.stdin, never in RunCommand.argv.
    """
    t = target.rstrip("/")
    steps: List[Step] = []

    # 1. /etc/hostname
    steps.append(WriteTargetFile(
        path=f"{t}/etc/hostname",
        content=f"{spec.hostname}\n",
        mode=0o644,
        description=f"Write /etc/hostname ({spec.hostname})",
    ))

    # 2. /etc/hosts
    hosts = (
        f"127.0.0.1\tlocalhost\n"
        f"::1\t\tlocalhost\n"
        f"127.0.1.1\t{spec.hostname}\n"
    )
    steps.append(WriteTargetFile(
        path=f"{t}/etc/hosts",
        content=hosts,
        mode=0o644,
        description="Write /etc/hosts",
    ))

    # 3. Timezone symlink
    steps.append(RunCommand(
        argv=[
            "arch-chroot", t,
            "ln", "-sf",
            f"/usr/share/zoneinfo/{spec.timezone}",
            "/etc/localtime",
        ],
        description=f"Set timezone to {spec.timezone}",
    ))

    # 4. Hardware clock
    steps.append(RunCommand(
        argv=["arch-chroot", t, "hwclock", "--systohc"],
        description="Sync hardware clock from system clock",
    ))

    # 5. /etc/locale.gen
    steps.append(WriteTargetFile(
        path=f"{t}/etc/locale.gen",
        content=f"{spec.locale} UTF-8\n",
        mode=0o644,
        description=f"Write /etc/locale.gen ({spec.locale})",
    ))

    # 6. locale-gen
    steps.append(RunCommand(
        argv=["arch-chroot", t, "locale-gen"],
        description="Generate locales",
    ))

    # 7. /etc/locale.conf
    steps.append(WriteTargetFile(
        path=f"{t}/etc/locale.conf",
        content=f"LANG={spec.locale}\n",
        mode=0o644,
        description="Write /etc/locale.conf",
    ))

    # 8. Create user
    steps.append(RunCommand(
        argv=[
            "arch-chroot", t,
            "useradd", "-m", "-G", "wheel", "-s", "/bin/bash",
            spec.username,
        ],
        description=f"Create user {spec.username}",
    ))

    # 9. Set user password via chpasswd stdin (password NEVER in argv)
    steps.append(RunCommand(
        argv=["arch-chroot", t, "chpasswd"],
        stdin=f"{spec.username}:{spec.password}\n",
        description=f"Set password for {spec.username}",
    ))

    # 10. Set root password via chpasswd stdin (password NEVER in argv)
    steps.append(RunCommand(
        argv=["arch-chroot", t, "chpasswd"],
        stdin=f"root:{spec.password}\n",
        description="Set root password",
    ))

    # 11. Wheel sudoers drop-in (mode 0o440 — required by sudo)
    steps.append(WriteTargetFile(
        path=f"{t}/etc/sudoers.d/10-wheel",
        content="%wheel ALL=(ALL:ALL) ALL\n",
        mode=0o440,
        description="Write wheel sudoers drop-in",
    ))

    return steps
