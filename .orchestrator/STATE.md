# STATE

## Repo layout (verified this cycle)
- ISO build works: `build.sh` + `Dockerfile` (artools), profile at `iso-profile/wheatley/` (profile.yaml, root-overlay, live-overlay), output ISO in `out/`.
- Custom packages in `packages/`: apeturewm, atomwm, st-wheatley, wheatley-branding, and `wheatley-installer` (old `wheatley-install` script still present; being replaced).

## New installer (packages/wheatley-installer/)
- `wheatley_installer/catalog.py` — catalog data model + stdlib validator (task-001, done, 38 tests pass). Dataclasses: Catalog{version, kernels[Kernel], inits[Init], sessions[Session], shells[Shell], support[SupportToggle], gaming[Gaming], minimal[Minimal]}. Session has kind ('wm'|'de'), ease, lightness, keybindings, screenshot, packages, services, shell_choices (ids into shells). Load via `load_catalog(Path)`; raises `CatalogError`.
- `catalog/catalog.json` — real data: kernels (linux-cachyos primary, linux-zen), inits (dinit recommended, runit), sessions (apeturewm, nvwm, mangowc, niri, sway, plasma, xfce...), shells (noctalia, ilyamiro-quickshell), support (bluetooth), gaming, minimal. `catalog/screenshots/` exists.
- `tests/test_catalog.py` — 38 unit tests.

## Architecture decisions
- ADR-1: Python 3, stdlib only (curses TUI later); lives inside `packages/wheatley-installer/` so existing PKGBUILD/ISO pipeline ships it unchanged.
- ADR-2: Catalog-driven: `catalog.json` is the single source of truth; UI, package-install, and service-enable steps are thin consumers.
- ADR-3 (this cycle): Strict layering: catalog (data) → **Selection + plan resolver (pure logic, no I/O)** → executor (pacstrap/chroot side effects) → TUI (curses). Each layer only imports the one below and is unit-testable without root.

## Progress
- Cycle 1: catalog data model + validator + tests (done).
- Cycle 2: building `plan.py` — Selection model + resolver that turns Catalog + Selection into a deterministic InstallPlan (packages, services, post-install files like fastfetch rc). This is the installer's brain; TUI and executor become thin consumers.