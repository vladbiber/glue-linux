# Plan


## DOING (1)

- **task-006** — Build the identity layer: pure IdentitySpec + identity_steps (hostname, locale, timezone, user + password via chpasswd stdin) with RunCommand stdin support, wired into __main__  _(9. Disk, boot & identity (required for 'installs & boots'))_

## TODO (1)

- **task-003** — Build the executor layer: pure InstallPlan→Step compiler plus a dry-run-capable runner (pacstrap, chroot file writes, init-specific service enabling)  _(1. Installer shell — friendly, composable)_

## DONE (4)

- **task-001** — Build the installer's catalog data model: catalog.json + stdlib validator + unit tests  _(1. Installer shell — friendly, composable)_
- **task-002** — Build the Selection model and plan resolver: pure logic that turns Catalog + user choices into a validated, deterministic InstallPlan  _(1. Installer shell — friendly, composable)_
- **task-004** — Build the curses view layer and `python -m wheatley_installer` entry point: pure line renderer (render.py) + thin curses driver (tui.py) + wired dry-run-capable main  _(1. Installer shell — friendly, composable)_
- **task-005** — Build the disk & bootloader layer: pure lsblk parsing, UEFI/BIOS partition planning, and compile_steps integration (sgdisk/mkfs/mount → pacstrap → grub) with dry-run support  _(9. Disk, boot & identity (required for 'installs & boots'))_
