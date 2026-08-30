# Novalist Windows packaging

`v2.0.6-beta` is the first planned packaged release. The baseline build is a
PyInstaller one-folder Windows x64 application compressed as a portable ZIP.
It includes its Python and PySide6 runtime, so the target computer does not need
Python installed.

## Local build

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\scripts\build_windows.ps1
```

The build script runs the full unit-test suite, creates the frozen application,
runs `Novalist.exe --self-test`, and writes these files to `dist/release/`:

- `Novalist-v<version>-windows-x64.zip`
- `release-manifest.json`
- `SHA256SUMS.txt`

The package contains application code, Qt/Python runtime files, public assets,
the version file, and license/privacy documentation. It intentionally excludes
personal `config.json` files, writing projects, virtual environments, and update
caches.

## Release boundary

This baseline is not yet an unattended updater. Before publishing an automatic
installation path, add signed manifests, Windows code signing, a separate
updater helper, transactional replacement, and rollback tests. The first
automatic update should therefore be exercised from an installed
`v2.0.6-beta` package to a later test version.
