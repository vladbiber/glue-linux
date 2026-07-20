# Architectural Decisions (ADRs)

_None yet._


## task-001 — Build the installer's catalog data model: catalog.json + stdlib validator + unit tests
- Capability: 1. Installer shell — friendly, composable
- Complexity: standard
- Rationale: Nothing is built yet. Every requirement (multi-WM select, ease/lightness/keybinds display, screenshots, kernel choice, dinit-recommended, bluetooth toggle, gaming mode, minimal path, per-WM bar/shell choice) is fundamentally DATA the installer renders and acts on. Building the single source-of-truth catalog first means the TUI, the package-install engine, and the service-enable engine are all thin consumers of one validated contract — the deepest possible foundation, not a shallow feature.
- Reviewer: All 38 tests pass (18 negative), CLI prints correct summary, catalog.json meets every structural requirement, no stray files created, all files under 500 lines.



## task-002 — Build the Selection model and plan resolver: pure logic that turns Catalog + user choices into a validated, deterministic InstallPlan
- Capability: 1. Installer shell — friendly, composable
- Complexity: standard
- Rationale: The catalog (data layer) is done and tested. Before any TUI or executor can exist, something must convert 'what the user picked' into 'exactly which packages to install, which services to enable, which files to write' — with all cross-cutting rules enforced (exactly one kernel/init, minimal excludes sessions, per-session bar/shell choice resolved, bluetooth/gaming expansion, fastfetch rc, greeter included when any session is chosen). Building this as pure, side-effect-free logic makes it fully unit-testable without root or a live ISO, and makes the upcoming TUI and executor trivial thin layers. This is the single deepest next step.
- Reviewer: 74 tests pass (38 catalog + 36 plan), 13 negative tests, clean stdlib-only imports, integration test covers exact acceptance-criteria scenario, determinism proven via permutation tests, both files under 500 lines

