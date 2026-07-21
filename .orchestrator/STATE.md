# STATE

## Repo layout (verified)
- ISO build works: `build.sh` + `Dockerfile` (artools), profile at `iso-profile/wheatley/` (profile.yaml pulls `wheatley-installer`; `root-overlay/etc/profile.d/wheatley-live.sh` auto-runs `sudo wheatley-install` on the live TTY).
- Custom packages in `packages/`: apeturewm, atomwm, st-wheatley, wheatley-branding, wheatley-installer.
- `packages/wheatley-installer/PKGBUILD` (pkgrel=5) still ships the OLD 584-line whiptail script (`wheatley-install`, bash+libnewt) — the new Python installer is NOT yet packaged/on the ISO. PKGBUILD also stages `pacman.conf` → /usr/share/wheatley/pacman.conf (repos: artix+cachyos+wheatley; the file is copied next to the PKGBUILD by scripts/make-iso.sh). The new executor does NOT yet pass this pacman.conf to pacstrap.

## New installer (packages/wheatley-installer/)
- `wheatley_installer/catalog.py` (464 ln) — catalog model + validator (task-001). `load_catalog(Path)`, raises `CatalogError`.
- `wheatley_installer/plan.py` (200 ln) — task-002. `Selection`, `InstallPlan`, `resolve_plan(catalog, selection)`, raises `PlanError`. Writes /etc/skel/.bashrc+.zshrc with fastfetch autorun.
- `wheatley_installer/executor.py` (192 ln) — task-003. Pure `compile_steps(plan, *, target='/mnt', init_id) -> List[Step]` (Step = RunCommand | WriteTargetFile), thin `execute(steps, dry_run, log)`. RunCommand has optional `stdin` (ADR-7). Init-specific service enabling in compiler. fstab via fstabgen.
- `wheatley_installer/disks.py` (321 ln) — task-005 (ADR-6). Pure `parse_lsblk`, `plan_disk(device, firmware)`, `disk_steps`, `bootloader_steps`, `bootloader_packages`; thin `discover()`.
- `wheatley_installer/identity.py` (199 ln) — task-006 (ADR-7). Pure `IdentitySpec` + `identity_steps()`; passwords via chpasswd stdin, never argv.
- `wheatley_installer/ui_model.py` (500 ln, at the limit — do NOT grow it) + `ui_forms.py` (210 ln) — task-007 COMPLETE and verified: wizard flow mode→kernel→init→sessions→shell→support→gaming→disk→form:hostname..form:timezone→summary. Disk screen appears when Wizard(disks=...) injected; identity forms when ask_identity=True; password-confirm mismatch clears both fields; masking is render-side. Yields WizardResult (selection, device_path, identity).
- `wheatley_installer/render.py` (204) + `tui.py` (196) + `__main__.py` (268) — pure renderer, thin curses driver (amber palette), wired main with `--dry-run`, `--target`, `--disk` flags. Runs end-to-end: `python -m wheatley_installer --dry-run`. Catalog path is currently resolved repo-relative — NOT install-aware yet.
- `catalog/catalog.json` — real data (2 kernels, 2 inits, 7 sessions incl. apeturewm/nvwm/mangowc/niri/sway/plasma/xfce, 2 shells noctalia/ilyamiro-quickshell, bluetooth, gaming, minimal). `catalog/screenshots/` exists.
- `tests/` — 351 tests, ALL PASS (`python -m unittest discover -s tests`; pytest not installed). Includes test_ui_forms.py (52) and test_render.py (28). No root/TTY needed.

## KNOWN GAPS (depth-first order)
1. Capability 10 (ONLY remaining roadmap item): PKGBUILD still ships the old whiptail script. Needed: package the Python module + catalog + screenshots, install-aware catalog path resolution in __main__, a thin /usr/bin/wheatley-install launcher, and pacstrap must use /usr/share/wheatley/pacman.conf (-C) so the chroot sees artix+cachyos+wheatley repos. Cycle 9 does this.

## Architecture decisions
- ADR-1..ADR-8 unchanged (stdlib-only Python, catalog-driven, strict layering catalog→plan→executor→ui_model→view, pure step compilers + thin runners, secrets via stdin, pure form screens in ui_forms.py, files ≤500 ln).
- ADR-9 (this cycle): Install-aware resource resolution — catalog/screenshots looked up in order: $WHEATLEY_CATALOG override → /usr/share/wheatley-installer/catalog/catalog.json (packaged) → repo-relative (dev checkout). pacman.conf for pacstrap: pass `-C /usr/share/wheatley/pacman.conf` only when the file exists (value passed INTO the pure compiler, keeping it pure).

## Progress
- Cycle 1: catalog. Cycle 2: plan resolver. Cycle 3: executor. Cycle 4: ui_model wizard brain. Cycle 5: render/tui/__main__. Cycle 6: disks.py. Cycle 7: identity.py. Cycle 8: task-007 disk+identity wizard screens — initially flagged by reviewer, since FIXED and verified this cycle (351 tests pass, ui_model.py exactly 500 ln, ui_forms/render tests present).
- NOTE: stale open task-003 in the tracker is long done (commit df08bef etc.) — ignore it.
- Cycle 9: capability 10 — ship the Python installer via PKGBUILD, replacing the whiptail script.