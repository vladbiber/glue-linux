# Architectural Decisions (ADRs)

_None yet._


## task-001 — Build the installer's catalog data model: catalog.json + stdlib validator + unit tests
- Capability: 1. Installer shell — friendly, composable
- Complexity: standard
- Rationale: Nothing is built yet. Every requirement (multi-WM select, ease/lightness/keybinds display, screenshots, kernel choice, dinit-recommended, bluetooth toggle, gaming mode, minimal path, per-WM bar/shell choice) is fundamentally DATA the installer renders and acts on. Building the single source-of-truth catalog first means the TUI, the package-install engine, and the service-enable engine are all thin consumers of one validated contract — the deepest possible foundation, not a shallow feature.
- Reviewer: All 38 tests pass (18 negative), CLI prints correct summary, catalog.json meets every structural requirement, no stray files created, all files under 500 lines.

