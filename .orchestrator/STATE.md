# STATE

## Repo layout (verified)
- ISO build works: `build.sh` + `Dockerfile` (artools), profile at `iso-profile/wheatley/` (profile.yaml, root-overlay, live-overlay), output ISO in `out/`.
- Custom packages in `packages/`: apeturewm, atomwm, st-wheatley, wheatley-branding, and `wheatley-installer` (old `wheatley-install` script still present; being replaced).

## New installer (packages/wheatley-installer/)
- `wheatley_installer/catalog.py` (464 ln) — catalog model + validator (task-001). `load_catalog(Path)`, raises `CatalogError`.
- `wheatley_installer/plan.py` (200 ln) — task-002. `Selection`, `InstallPlan`, `resolve_plan(catalog, selection)`, raises `PlanError`. Writes /etc/skel/.bashrc+.zshrc with fastfetch autorun.
- `wheatley_installer/executor.py` (164 ln) — task-003. Pure `compile_steps(plan, *, target='/mnt', init_id) -> List[Step]` (Step = RunCommand | WriteTargetFile), thin `execute(steps, dry_run, log)`. Init-specific service enabling (dinit vs runit) lives in the compiler.
- `wheatley_installer/ui_model.py` (439 ln) — task-004. Pure wizard state machine, NO curses/I/O. `Wizard(catalog)` with `.current_screen() -> Screen`, `.apply(Toggle|Choose|SetFlag)`, `.next()/.back()`, `.is_finished()`, `.to_selection() -> Selection`; raises `ValidationError`. `Screen{key,title,kind:'info'|'radio'|'multi'|'toggle'|'summary',items,notice}`, `Item{id,label,description,ease,lightness,keybinds,screenshot,recommended,selected,section}`. Dynamic screens: mode→kernel→init→sessions→per-session shell (mangowc/niri only)→support→gaming→summary; minimal skips sessions; dinit tagged recommended; multi-select login notice on sessions screen.
- `catalog/catalog.json` — real data: kernels (linux-cachyos primary, linux-zen), inits (dinit recommended, runit), sessions (apeturewm, nvwm, mangowc, niri, sway, plasma, xfce...), shells (noctalia, ilyamiro-quickshell), support (bluetooth), gaming, minimal. `catalog/screenshots/` exists.
- `tests/` — 154 tests (38 catalog + 36 plan + 28 executor + 13 runner + 39 ui_model), all pass, no root/TTY needed.

## Architecture decisions
- ADR-1: Python 3, stdlib only (curses for TUI); lives in `packages/wheatley-installer/` so PKGBUILD/ISO pipeline ships it unchanged.
- ADR-2: Catalog-driven: `catalog.json` is the single source of truth.
- ADR-3: Strict layering: catalog → Selection+resolver → executor → ui_model (pure wizard brain) → curses view (thin).
- ADR-4: Executor split: pure step compiler + thin runner; init-specific logic only in compiler.
- ADR-5 (this cycle): TUI view split again: pure renderer `render.py` (Screen + cursor + terminal size → list of (text, style-tag) lines; unit-testable, no curses import) and thin curses driver `tui.py` that only maps keys→events and paints rendered lines with the amber palette. `__main__.py` is the only place that wires catalog→wizard→resolve_plan→compile_steps→execute.

## Progress
- Cycle 1: catalog + validator (done). Cycle 2: plan resolver (done, 74 tests). Cycle 3: executor (done). Cycle 4: ui_model wizard brain + executor-test split (done, 154 tests).
- Cycle 5: building the curses view + `python -m wheatley_installer` entry point — after this the installer is runnable end-to-end (dry-run on any machine, real run on the live ISO).