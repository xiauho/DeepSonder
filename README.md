# DeepSonder-Electron

DeepSonder-Electron is the Electron series of the DeepSonder test-stage novel
analysis and writing tool. It shares the same user-facing capability contract
as DeepSonder-PySide6 while keeping its own source tree, version, configuration,
build, installer, and release artifacts.

## Development

Requirements:

- Windows
- Node.js 24 or newer
- Python 3.12

Install JavaScript dependencies and start the development build:

```powershell
npm ci
npm start
```

The local Python sidecar uses only the Python standard library. Packaging also
requires the pinned dependency in `requirements-build.txt`.

Run the regression suites from this repository root:

```powershell
python -m unittest discover -s tests
npm test
```

Build the Windows installer and portable ZIP:

```powershell
python -m pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1
```

Generated dependencies, builds, and releases are ignored by Git. The Electron
release is self-contained and must not read files from the sibling PySide6
workspace.

## Legacy projects

Old Novalist test projects are imported read-only and manuscript-only. The
importer creates a new DeepSonder project from the project name, author, and
ordered chapter Markdown. It does not migrate configuration, update state,
memory, recognized characters, canon, graphs, AI results, caches, proposals, or
trash, and it never modifies the source project.

## Dual-series changes

Read `AGENTS.md` before making changes. Every applicable user-facing feature or
bug fix must be implemented and tested in both this repository and the sibling
DeepSonder-PySide6 workspace, then recorded in `docs/feature-parity.json`.
