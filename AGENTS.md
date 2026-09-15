# DeepSonder dual-series workspace instructions

This repository is the source workspace for **DeepSonder-Electron**. Its sibling
workspace is `D:\GitHub-store\novalist`, which contains
**DeepSonder-PySide6**.

## Mandatory feature-parity workflow

- Treat user-facing capabilities as a two-series product contract. Before
  changing behavior in either workspace, inspect the corresponding behavior in
  the sibling workspace.
- For every feature addition, removal, behavior change, bug fix, project-data
  change, AI workflow change, or import rule change, update both series in the
  same task whenever the counterpart applies.
- Add or update equivalent tests in both workspaces. The UI implementation and
  interaction design may differ, but observable behavior, safety boundaries,
  and user-visible capability must remain equivalent.
- Update `docs/feature-parity.json` in both workspaces. A feature may be marked
  `platform_specific` only when it depends on PySide6/Electron packaging or
  operating-system integration and does not change product capability.
- If the counterpart cannot be completed, record it as `pending` with a reason
  in both parity files, report the gap to the user, and do not describe the
  feature as complete across both series.
- Before a release, require every `required` feature to be `supported` in both
  workspaces and require each workspace's tests and build checks to pass.

## Product and compatibility boundaries

- The displayed brand is **DeepSonder**. Use the series names
  **DeepSonder-PySide6** and **DeepSonder-Electron** for executable names,
  packages, configuration paths, diagnostics, and release artifacts.
- The two series have independent versions, installers, configuration,
  caches, release manifests, and update channels. Never allow one series to
  install or consume the other series' release package.
- Do not add in-place upgrade or protocol compatibility for old Novalist test
  releases. Compatibility is limited to a read-only legacy-project importer
  that creates a new project from project name, author, and ordered chapter
  Markdown.
- Never modify an imported legacy source project. Do not import old memory,
  recognized characters, canon, relationship graphs, AI results, caches,
  proposals, update state, or trash. Rebuild derived information from the
  imported manuscript after explicit user action.
- Keep generated folders and dependencies out of repository copies and release
  inputs unless explicitly required: `.venv`, `node_modules`, `build`, `dist`,
  caches, and local release output.

## Cross-workspace coordination

- The PySide6 workspace remains at `D:\GitHub-store\novalist`; do not rename or
  relocate this directory.
- The Electron workspace remains at `D:\GitHub-store\DeepSonder`.
- Each workspace must build and test from its own root without reaching into the
  sibling workspace at runtime or during release packaging.
- Shared test fixtures may be copied between repositories, but releases must be
  self-contained. Record intentional fixture or behavior divergence in both
  parity files.
