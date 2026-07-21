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



## task-004 — Build the pure TUI wizard state machine (ui_model.py): catalog-driven screens, navigation, and validation that produce a Selection — plus split the oversized test_executor.py
- Capability: 1. Installer shell — friendly, composable
- Complexity: complex
- Rationale: The full logic stack (catalog → plan → executor) is done and tested; the only missing layer is the TUI. Writing curses code directly would make the installer's flow logic untestable, so the deepest next step is the wizard's brain as pure code: which screens exist for a given catalog, what each shows (ease/lightness/keybinds/screenshot path, dinit 'recommended' tag, the multi-select login notice), how choices constrain each other (minimal skips sessions, shell-choice screens appear only for mangowc/niri), and how the final Selection is assembled. This also clears the one piece of known debt (530-line test file) so the codebase is fully clean before the last layer.
- Reviewer: All 154 tests pass (115 pre-existing preserved, 39 new ui_model tests well above the ≥20 threshold); every acceptance criterion is concretely verified by a passing test; ui_model.py has no curses/I/O imports; all files are ≤500 lines; executor split is lossless at 28+13=41 tests



## task-004 — Build the curses view layer and `python -m wheatley_installer` entry point: pure line renderer (render.py) + thin curses driver (tui.py) + wired dry-run-capable main
- Capability: 1. Installer shell — friendly, composable
- Complexity: complex
- Rationale: Every logic layer is done and tested (catalog → plan → executor → ui_model wizard brain, 154 tests). The single missing piece between this stack and a usable installer is the view: something must paint Screens, translate keystrokes into Wizard events, and wire Selection → resolve_plan → compile_steps → execute. Nothing else on the roadmap can be meaningfully verified end-to-end until the program is actually runnable, so this is the unambiguous depth-first next step — it completes capability 1 rather than starting anything new.
- Reviewer: All 186 tests pass, architecture is clean, files are within limits, and all acceptance criteria are provably met.



## task-005 — Build the disk & bootloader layer: pure lsblk parsing, UEFI/BIOS partition planning, and compile_steps integration (sgdisk/mkfs/mount → pacstrap → grub) with dry-run support
- Capability: 9. Disk, boot & identity (required for 'installs & boots')
- Complexity: complex
- Rationale: Capability 1 is complete: the installer runs end-to-end in dry-run (186 tests). But it cannot actually install a bootable system — it assumes /mnt is already partitioned/mounted and never installs a bootloader. The old whiptail script did sgdisk/mkfs/mount/grub; the new one does not. No roadmap item (gaming, DEs, greeter) matters until an install boots, so the disk+boot layer is the unambiguous depth-first next step: it deepens the existing executor rather than adding a new surface.
- Reviewer: no diff to review

