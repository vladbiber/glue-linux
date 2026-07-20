# STATE

## Repo layout (verified this cycle)
- ISO build works: `build.sh` + `Dockerfile` (artools), profile at `iso-profile/wheatley/` (profile.yaml, root-overlay, live-overlay), output ISO in `out/`.
- Custom packages in `packages/`: apeturewm, atomwm, st-wheatley, wheatley-branding, and `wheatley-installer` (currently just a `PKGBUILD` + a single `wheatley-install` script — this is the OLD installer we are replacing).

## Architecture decisions (this cycle)
- ADR-1: New installer is written in **Python 3, stdlib only** (curses TUI later) so it runs on the live ISO with zero pip dependencies. It lives inside `packages/wheatley-installer/` so the existing PKGBUILD/ISO pipeline can ship it unchanged.
- ADR-2: The installer is **catalog-driven**: a single `catalog.json` is the source of truth for every composable choice (kernels, init, WMs, DEs, bar/shell choices, support toggles, gaming mode, minimal mode). Every UI screen, package-install step, and service-enable step reads from it. All later capabilities (bluetooth, gaming, minimal) are catalog entries, not code branches.
- Catalog contract v1: see `packages/wheatley-installer/wheatley_installer/catalog.py` docstring once built.

## Progress
- Cycle 1: building the catalog data model + validator + tests (foundation for the whole installer).