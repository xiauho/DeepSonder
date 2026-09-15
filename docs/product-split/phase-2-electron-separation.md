# Phase 2: DeepSonder-Electron separation

Date: 2026-09-15

## Scope

This phase separates the Electron series into `D:\GitHub-store\DeepSonder` as
an independently buildable repository. The PySide6 workspace remains at
`D:\GitHub-store\novalist`.

## Series identity

- Product brand: `DeepSonder`
- Series: `DeepSonder-Electron`
- Initial independent version: `0.1.0-beta`
- Windows executable: `DeepSonder-Electron.exe`
- Sidecar executable: `DeepSonderElectronSidecar.exe`
- Configuration/cache root: `DeepSonder\Electron`
- Installer/ZIP prefix: `DeepSonder-Electron-v`

## Independence boundaries

- Source, tests, build inputs, dependencies, and release outputs resolve from
  this repository root.
- No runtime or packaging path reaches into the sibling PySide6 workspace.
- JavaScript dependencies, Python environments, build folders, sourcemaps, and
  release outputs are not committed.
- There is no automatic migration of a legacy installation configuration.
- Legacy compatibility is limited to read-only, manuscript-only project import.

## Acceptance checks

- Python regression suite passes from this repository root.
- Electron typecheck, renderer build, bundle gate, and Node test suite pass.
- Release paths and integrity checks use only series-qualified artifact names.
- A standalone Git history is initialized only after the copied tree passes
  source-level verification.
