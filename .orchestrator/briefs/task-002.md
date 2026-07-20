# task-002 — Build the Selection model and plan resolver: pure logic that turns Catalog + user choices into a validated, deterministic InstallPlan

- **Capability:** 1. Installer shell — friendly, composable
- **Complexity:** standard
- **Rationale:** The catalog (data layer) is done and tested. Before any TUI or executor can exist, something must convert 'what the user picked' into 'exactly which packages to install, which services to enable, which files to write' — with all cross-cutting rules enforced (exactly one kernel/init, minimal excludes sessions, per-session bar/shell choice resolved, bluetooth/gaming expansion, fastfetch rc, greeter included when any session is chosen). Building this as pure, side-effect-free logic makes it fully unit-testable without root or a live ISO, and makes the upcoming TUI and executor trivial thin layers. This is the single deepest next step.
- **Depth note:** Deepens the existing catalog capability into an end-to-end decision engine rather than starting a new surface (no TUI, no new features). Every rule it encodes is an existing roadmap requirement; nothing new is invented.

## Files
- `packages/wheatley-installer/wheatley_installer/plan.py`
- `packages/wheatley-installer/tests/test_plan.py`

## Contracts
Import ONLY from wheatley_installer.catalog (Catalog, Session, Shell, CatalogError, etc.) and Python stdlib — no other project imports, no I/O, no subprocess, nothing that touches the filesystem (plan.py is pure logic). Respect the existing catalog dataclasses exactly as defined in wheatley_installer/catalog.py (read it first). New public API in plan.py: (1) @dataclass Selection: kernel_id: str; init_id: str; session_ids: list[str]; shell_choice: dict[str, str] (session_id -> shell_id, required for every selected session whose shell_choices is non-empty); support_ids: list[str]; gaming: bool; minimal: bool. (2) @dataclass InstallPlan: packages: list[str] (deduplicated, sorted, deterministic); services: list[str] (deduplicated, sorted; service names to enable under the chosen init); files: list[PlannedFile] where @dataclass PlannedFile has path: str (absolute path inside the target system), content: str, mode: int (e.g. 0o644); warnings: list[str]. (3) class PlanError(Exception) with a human-readable message naming the offending field/id. (4) def resolve_plan(catalog: Catalog, selection: Selection) -> InstallPlan. Keep both files under 500 lines each.

## Brief
Read packages/wheatley-installer/wheatley_installer/catalog.py and catalog/catalog.json first — they are the source of truth for ids and dataclass shapes.

Implement wheatley_installer/plan.py exactly per the contract above. resolve_plan must enforce these rules, raising PlanError with a message naming the bad id/field for each violation:
1. kernel_id must match exactly one catalog kernel; its packages go in the plan. Same for init_id (init packages included).
2. Every id in session_ids must exist in catalog.sessions; duplicates in session_ids are a PlanError. Multiple sessions are ALLOWED and expected (multi-WM select).
3. If selection.minimal is True: session_ids, shell_choice, and gaming must be empty/False, otherwise PlanError ('minimal install cannot include sessions/gaming'). Support toggles ARE allowed with minimal.
4. For each selected session with non-empty shell_choices: shell_choice MUST contain an entry for that session, and its value must be one of that session's shell_choices AND exist in catalog.shells — the shell's packages are added. A shell_choice entry for a session with empty shell_choices, for an unselected session, or with an unknown shell id is a PlanError.
5. Every id in support_ids must match a catalog support toggle (unknown id -> PlanError); its packages and services are added.
6. If gaming is True: catalog.gaming.packages and .services are added; if catalog.gaming.gpu_autodetect is true, append warning 'GPU driver auto-detection will run on the target system during install' (executor handles actual detection later — do NOT probe hardware here).
7. Session packages and services are added for every selected session. If at least one session is selected, add the greeter: package 'greetd' plus service 'greetd', and append warning 'Multiple sessions installed — pick your session at the login screen' only when 2+ sessions are selected.
8. Always include baseline files in the plan: PlannedFile('/etc/skel/.bashrc', content containing a line that runs fastfetch for interactive shells guarded like: case $- in *i*) command -v fastfetch >/dev/null && fastfetch;; esac, mode 0o644) and the same guard in PlannedFile('/etc/skel/.zshrc', using [[ -o interactive ]] && command -v fastfetch >/dev/null && fastfetch, mode 0o644). Include 'fastfetch' in packages always.
9. Output determinism: packages and services are deduplicated and sorted alphabetically; files sorted by path; warnings in a fixed documented order. Calling resolve_plan twice with equal inputs yields equal output.

Write tests/test_plan.py using unittest (match test_catalog.py's style — read it first). Build a small in-test Catalog via the real dataclasses (or by loading the real catalog/catalog.json where convenient). Cover at minimum: happy path with 2 sessions + shell choice + bluetooth + gaming (assert exact sorted package/service lists); minimal path (no sessions, fastfetch files still present); each PlanError rule above (unknown kernel/init/session/support/shell ids, duplicate session_ids, missing shell_choice for mangowc-style session, shell_choice for session without choices, shell not in that session's shell_choices, minimal+sessions, minimal+gaming); determinism (two calls -> equal plans; shuffled input session_ids/support_ids -> identical sorted output); greeter added only when sessions selected; multi-session warning only at 2+ sessions. Also add a realistic integration test: load the real catalog/catalog.json with load_catalog, select linux-cachyos + dinit + ['mangowc','niri'] with a valid shell choice for each from the real catalog data, and assert the plan contains greetd, fastfetch, both sessions' packages, and the chosen shell's packages.

Run: cd packages/wheatley-installer && python -m unittest discover -s tests -v — ALL tests (old catalog tests included) must pass. Do not modify catalog.py or catalog.json. Do not create any other files.

## Acceptance criteria
1. `cd packages/wheatley-installer && python -m unittest discover -s tests -v` exits 0, running the existing 38 catalog tests plus >= 15 new plan tests, including at least 8 negative tests that assert PlanError is raised for the rule violations listed in the brief.
2. plan.py defines Selection, PlannedFile, InstallPlan, PlanError, and resolve_plan with the exact contract fields; `grep -E 'import|from' plan.py` shows only stdlib imports and wheatley_installer.catalog (no I/O/subprocess/os.system usage anywhere in plan.py).
3. An integration test loads the real catalog/catalog.json and asserts a plan for linux-cachyos + dinit + ['mangowc','niri'] (with valid shell choices) contains 'greetd' and 'fastfetch' in packages, 'greetd' in services, and that packages/services lists are sorted and duplicate-free.
4. resolve_plan is deterministic: a test asserts two calls with equal Selections produce equal InstallPlans, and that permuting the order of session_ids/support_ids in the Selection yields an identical plan.
5. Both new files are under 500 lines; no files other than plan.py and test_plan.py are created or modified.
