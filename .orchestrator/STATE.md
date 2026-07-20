# STATE

## Repo layout (verified)
- ISO build works: `build.sh` + `Dockerfile` (artools), profile at `iso-profile/wheatley/` (profile.yaml, root-overlay, live-overlay), output ISO in `out/`.
- Custom packages in `packages/`: apeturewm, atomwm, st-wheatley, wheatley-branding, and `wheatley-installer` (old `wheatley-install` script still present; being replaced).

## New installer (packages/wheatley-installer/)
- `wheatley_installer/catalog.py` — catalog data model + stdlib validator (task-001, done). Load via `load_catalog(Path)`; raises `CatalogError`.
- `wheatley_installer/plan.py` — task-002, DONE. `Selection{kernel_id, init_id, session_ids, shell_choice: Dict[session_id, shell_id], support_ids, gaming, minimal}`, `PlannedFile{path, content, mode}`, `InstallPlan{packages (sorted/deduped), services (sorted/deduped), files (sorted by path), warnings}`, `resolve_plan(catalog, selection) -> InstallPlan`, raises `PlanError`. Baseline files: /etc/skel/.bashrc and .zshrc with fastfetch autorun.
- `catalog/catalog.json` — real data: kernels (linux-cachyos primary, linux-zen), inits (dinit recommended, runit), sessions (apeturewm, nvwm, mangowc, niri, sway, plasma, xfce...), shells (noctalia, ilyamiro-quickshell), support (bluetooth), gaming, minimal. `catalog/screenshots/` exists.
- `tests/` — 74 tests total (38 catalog + 36 plan), all pass, no root needed.

## Architecture decisions
- ADR-1: Python 3, stdlib only (curses TUI later); lives inside `packages/wheatley-installer/` so existing PKGBUILD/ISO pipeline ships it unchanged.
- ADR-2: Catalog-driven: `catalog.json` is the single source of truth; UI, package-install, and service-enable steps are thin consumers.
- ADR-3: Strict layering: catalog (data) → Selection + plan resolver (pure logic) → executor (pacstrap/chroot side effects) → TUI (curses).
- ADR-4 (this cycle): Executor is split in two: a PURE step compiler `compile_steps(plan, target, init_id) -> List[Step]` (deterministic, unit-testable, no I/O) and a thin runner `execute(steps, dry_run, log)` that shells out. All init-specific service enabling (dinit vs runit) is encoded in the compiler, never in the runner.

## Progress
- Cycle 1: catalog data model + validator + tests (done).
- Cycle 2: plan.py — Selection + resolver → deterministic InstallPlan (done, 74 tests).
- Cycle 3: building executor.py — step compiler + dry-run-capable runner; after this, only the curses TUI remains between the logic stack and a usable installer.