# DeepSonder-PySide6 workspace instructions

This repository is the standalone source workspace for **DeepSonder-PySide6**.
The displayed product brand is **DeepSonder**. PySide6 and Electron are independently maintained series with separate roadmaps and releases.

## Independent development and delivery

- Scope implementation, tests, documentation, packaging, and release checks to this repository unless the user explicitly requests work on another series.
- Do not require an Electron inspection, counterpart change, equivalent test, or synchronized release before completing a PySide6 task.
- UI, capabilities, AI workflows, project formats, and implementation details may evolve independently. Another series may be used as a design reference when relevant or requested; it is not a compatibility or delivery contract.
- Build, test, and package from this repository root. Never reach into another workspace at runtime or while packaging.
- Keep `docs/feature-status.json` synchronized with this series' user-facing capabilities. `docs/feature-parity.json` is a historical archive, not a release gate or a file requiring continued synchronization.
- Before releasing PySide6, require its own required features to be supported and its own applicable tests and build checks to pass. Another series' status never blocks this release.
- Add or update meaningful tests for behavior changes, including UI state, navigation, AI review, and data safety. Validate layout changes at narrow desktop widths, light/dark themes, and increased display scaling.

## Product and compatibility boundaries

- Use **DeepSonder-PySide6** for executable names, packages, configuration paths, diagnostics, and release artifacts.
- Versions, installers, configuration, caches, release manifests, and update channels are independent. Never install or consume an Electron release package in PySide6.
- Do not assume cross-series project-format or protocol compatibility. Add any explicit importer/exporter only within the user's requested scope and document its boundaries.
- Do not add in-place upgrades or protocol compatibility for old Novalist test releases. Legacy compatibility is limited to a read-only importer that creates a new project from project name, author, and ordered chapter Markdown.
- Never modify an imported legacy source project. Do not import old memory, recognized characters, canon, relationship graphs, AI results, caches, proposals, update state, or trash. Rebuild derived information after explicit user action.
- Keep AI result review and explicit write confirmation, stale-context checks, cancellation, and user-data protections intact when reorganizing UI.

## Workspace and artifact hygiene

- Treat the current repository root as authoritative; do not depend on historical absolute workspace paths or relocate repositories without an explicit request.
- Preserve pre-existing changes and user project data. Use temporary/copied projects for tests and UI previews; never initialize, migrate, or mutate the checked-in demo or personal projects just to capture a screenshot.
- Keep generated folders and dependencies out of repository copies and release inputs unless explicitly required: `.venv`, `node_modules`, `build`, `dist`, caches, and local release output.
- Shared reference fixtures may be copied intentionally, but this repository and its releases must remain self-contained.
