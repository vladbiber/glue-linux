# STATE

## Repo layout (verified)
- ISO build works: `build.sh` + `Dockerfile` (artools), profile at `iso-profile/wheatley/` (profile.yaml pulls `wheatley-installer`; `root-overlay/etc/profile.d/wheatley-live.sh` auto-runs `sudo wheatley-install` on the live TTY).
- Custom packages in `packages/`: apeturewm, atomwm, st-wheatley, wheatley-branding, wheatley-installer.
- `packages/wheatley-installer/PKGBUILD` still ships the OLD 584-line whiptail script (`wheatley-install`, bash+libnewt) — the new Python installer is NOT yet packaged/on the ISO. Old script also stages `pacman.conf` → /usr/share/wheatley/pacman.conf (repos: artix+cachyos+wheatley); the new executor must eventually use that same pacman.conf for pacstrap.

## New installer (packages/wheatley-installer/)
- `wheatley_installer/catalog.py` (464 ln) — catalog model + validator (task-001). `load_catalog(Path)`, raises `CatalogError`.
- `wheatley_installer/plan.py` (200 ln) — task-002. `Selection`, `InstallPlan`, `resolve_plan(catalog, selection)`, raises `PlanError`. Writes /etc/skel/.bashrc+.zshrc with fastfetch autorun.
- `wheatley_installer/executor.py` (187 ln) — task-003. Pure `compile_steps(plan, *, target='/mnt', init_id) -> List[Step]` (Step = RunCommand | WriteTargetFile), thin `execute(steps, dry_run, log)`. RunCommand has optional `stdin` field (ADR-7). Init-specific service enabling in compiler. Generates fstab via fstabgen.
- `wheatley_installer/disks.py` (321 ln) — task-005 (ADR-6). Pure `parse_lsblk`, `plan_disk(device, firmware)` (UEFI/BIOS), `disk_steps`, `bootloader_steps`, `bootloader_packages`; thin `discover()`. Wired via `--disk` CLI flag with dry-run synthetic-disk fallback; confirmation prompt before erase.
- `wheatley_installer/identity.py` (200 ln) — task-006 (ADR-7). Pure `IdentitySpec` + `identity_steps()` (hostname, locale, timezone, useradd, chpasswd via stdin — secrets never in argv). Wired into `__main__.py` via CLI flags.
- `wheatley_installer/ui_model.py` (439 ln) — task-004. Pure wizard state machine (screens: mode→kernel→init→sessions→per-session shell→support→gaming→summary; dinit 'recommended'; multi-select login notice; minimal skips sessions). Produces `Selection`. NO disk or identity screens yet.
- `wheatley_installer/render.py` (187) + `tui.py` (180) + `__main__.py` (184) — pure line renderer, thin curses driver (amber palette), wired main with `--dry-run`, `--target`, `--disk` flags. Runs end-to-end: `python -m wheatley_installer --dry-run`.
- `catalog/catalog.json` — real data (kernels linux-cachyos/linux-zen, inits dinit-recommended/runit, sessions incl. apeturewm/nvwm/mangowc/niri/sway/plasma/xfce, shells noctalia/ilyamiro-quickshell, bluetooth, gaming, minimal). `catalog/screenshots/` exists.
- `tests/` — 295 tests, all pass (unittest; pytest not installed — use `python -m unittest discover -s tests`), no root/TTY needed.

## KNOWN GAPS (in depth-first order)
1. Disk choice and identity are CLI-flags only — the wizard has no disk-pick screen and no text-entry screens (hostname/user/password/locale/timezone). Cycle 8 builds these into ui_model + render/tui text-input support, replacing the CLI-flag-only path so the TUI collects everything.
2. Capability 10: PKGBUILD still ships the old whiptail script; new Python installer not packaged onto the ISO.

## Architecture decisions
- ADR-1: Python 3, stdlib only (curses for TUI); lives in `packages/wheatley-installer/`.
- ADR-2: Catalog-driven: `catalog.json` single source of truth.
- ADR-3: Strict layering: catalog → Selection+resolver → executor → ui_model → view.
- ADR-4: Executor split: pure step compiler + thin runner; init-specific logic only in compiler.
- ADR-5: View split: pure `render.py` + thin curses `tui.py`; `__main__.py` is the only wiring point.
- ADR-6: Disk layer mirrors executor pattern — pure parse/plan/steps; only subprocess is thin `discover()`. UEFI vs BIOS passed in as a value.
- ADR-7: Identity mirrors ADR-6 — pure `IdentitySpec` + `identity_steps()`; secrets never appear in argv: `RunCommand.stdin` carries passwords to `chpasswd`.
- ADR-8 (this cycle): Text/entry screens stay pure — ui_model gains form-field screens (typed events: char/backspace/submit) with validation; masking is a render concern (render shows `*`), the model stores the real value; curses driver stays thin. New form-screen logic may live in `ui_forms.py` to keep every file ≤500 lines.

## Progress
- Cycle 1: catalog. Cycle 2: plan resolver. Cycle 3: executor. Cycle 4: ui_model wizard brain. Cycle 5: render/tui/__main__ — runnable end-to-end dry-run. Cycle 6: disks.py + --disk wiring. Cycle 7: identity.py — hostname/user/password/locale/timezone as pure steps, chpasswd via stdin (295 tests).
- Cycle 8: wizard screens for disk selection + identity text-entry, wired so the TUI collects everything; then PKGBUILD packaging (capability 10).