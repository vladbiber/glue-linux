# STATE

## Repo layout (verified)
- ISO build works: `build.sh` + `Dockerfile` (artools), profile at `iso-profile/wheatley/` (profile.yaml pulls `wheatley-installer`; `root-overlay/etc/profile.d/wheatley-live.sh` auto-runs `sudo wheatley-install` on the live TTY).
- Custom packages in `packages/`: apeturewm, atomwm, st-wheatley, wheatley-branding, wheatley-installer.
- `packages/wheatley-installer/PKGBUILD` still ships the OLD 584-line whiptail script (`wheatley-install`, bash+libnewt) — the new Python installer is NOT yet packaged/on the ISO. Old script also stages `pacman.conf` → /usr/share/wheatley/pacman.conf (repos: artix+cachyos+wheatley); the new executor must eventually use that same pacman.conf for pacstrap.

## New installer (packages/wheatley-installer/)
- `wheatley_installer/catalog.py` (464 ln) — catalog model + validator (task-001). `load_catalog(Path)`, raises `CatalogError`.
- `wheatley_installer/plan.py` (200 ln) — task-002. `Selection`, `InstallPlan`, `resolve_plan(catalog, selection)`, raises `PlanError`. Writes /etc/skel/.bashrc+.zshrc with fastfetch autorun.
- `wheatley_installer/executor.py` (164 ln) — task-003. Pure `compile_steps(plan, *, target='/mnt', init_id) -> List[Step]` (Step = RunCommand | WriteTargetFile), thin `execute(steps, dry_run, log)`. Init-specific service enabling in compiler. Generates fstab via fstabgen.
- `wheatley_installer/ui_model.py` (439 ln) — task-004. Pure wizard state machine (screens: mode→kernel→init→sessions→per-session shell→support→gaming→summary; dinit 'recommended'; multi-select login notice; minimal skips sessions). Produces `Selection`.
- `wheatley_installer/render.py` + `tui.py` + `__main__.py` (cycle 5, ADR-5) — pure line renderer, thin curses driver (amber palette), wired main with `--dry-run` and `--target` flags. Installer runs end-to-end: `python -m wheatley_installer --dry-run`.
- `catalog/catalog.json` — real data (kernels linux-cachyos/linux-zen, inits dinit-recommended/runit, sessions incl. apeturewm/nvwm/mangowc/niri/sway/plasma/xfce, shells noctalia/ilyamiro-quickshell, bluetooth, gaming, minimal). `catalog/screenshots/` exists.
- `tests/` — 186 tests, all pass, no root/TTY needed.

## KNOWN GAP (blocks 'installs & boots')
The new installer has NO disk layer: no device discovery, no partitioning/mkfs/mount (it assumes /mnt is prepared), and NO bootloader (grub) steps. Also missing: hostname/user/password/locale/timezone. The old whiptail script handled all of this. Cycle 6 builds the disk+boot layer (pure); UI screens for disk/identity wire in next.

## Architecture decisions
- ADR-1: Python 3, stdlib only (curses for TUI); lives in `packages/wheatley-installer/`.
- ADR-2: Catalog-driven: `catalog.json` single source of truth.
- ADR-3: Strict layering: catalog → Selection+resolver → executor → ui_model → view.
- ADR-4: Executor split: pure step compiler + thin runner; init-specific logic only in compiler.
- ADR-5: View split: pure `render.py` + thin curses `tui.py`; `__main__.py` is the only wiring point.
- ADR-6 (this cycle): Disk layer mirrors the executor pattern — pure `parse_lsblk(json) -> devices` + pure `plan_disk(device, firmware) -> DiskPlan` + step compilation into the existing Step types; the only subprocess call is a thin `discover()` wrapper. UEFI vs BIOS decided by /sys/firmware/efi presence, passed in as a value (never probed inside pure code).

## Progress
- Cycle 1: catalog (38 tests). Cycle 2: plan resolver. Cycle 3: executor. Cycle 4: ui_model wizard brain (154 tests). Cycle 5: render/tui/__main__ — runnable end-to-end dry-run (186 tests).
- Cycle 6: disk & bootloader layer (pure discovery/layout/steps + grub), the biggest missing piece for a system that actually boots.