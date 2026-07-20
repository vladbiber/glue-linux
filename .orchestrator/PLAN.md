# Plan


## DOING (1)

- **task-003** — Build the executor layer: pure InstallPlan→Step compiler plus a dry-run-capable runner (pacstrap, chroot file writes, init-specific service enabling)  _(1. Installer shell — friendly, composable)_

## DONE (2)

- **task-001** — Build the installer's catalog data model: catalog.json + stdlib validator + unit tests  _(1. Installer shell — friendly, composable)_
- **task-002** — Build the Selection model and plan resolver: pure logic that turns Catalog + user choices into a validated, deterministic InstallPlan  _(1. Installer shell — friendly, composable)_
