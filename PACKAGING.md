# Novalist Windows packaging

`v2.0.6-beta` is the first packaged release. The baseline build uses
Python 3.12 and is a
PyInstaller one-folder Windows x64 application compressed as a portable ZIP.
It includes its Python and PySide6 runtime, so the target computer does not need
Python installed.

## Local build

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\scripts\build_windows.ps1
```

The secure-download baseline defaults `minimum_updater_version` to
`2.0.6-beta`, allowing the synced development workspace to exercise the first
`v2.0.7-beta` download. Later releases must pass the oldest updater version that
actually supports their package and manifest contract. For example, when
building a later target version such as `v2.0.8-beta`:

```powershell
.\scripts\build_windows.ps1 -MinimumUpdaterVersion 2.0.7-beta
```

The build script runs the full unit-test suite, creates the frozen application,
runs `Novalist.exe --self-test`, and writes these files to `dist/release/`:

- `Novalist-v<version>-windows-x64.zip`
- `release-manifest.json`
- `SHA256SUMS.txt`

The ZIP root also contains the project notices and a `licenses/` directory with
the complete third-party license texts required by the redistributed Python,
Qt/PySide6, PyInstaller and font components. The build fails if any required
license file is missing.

The package contains application code, Qt/Python runtime files, public assets,
the version file, and license/privacy documentation. It intentionally excludes
personal `config.json` files, writing projects, virtual environments, and update
caches.

The release manifest uses schema v2 and records the exact target platform,
archive name, byte size and SHA-256 digest. The download client cross-checks
those values with the GitHub Release asset metadata before accepting a file.

## Release boundary

This baseline is not yet an unattended updater. Before publishing an automatic
installation path, add signed manifests, Windows code signing, a separate
updater helper, transactional replacement, and rollback tests. Because the
published `v2.0.6-beta` only checks for releases, `v2.0.7-beta` is the secure
download baseline and the first complete automatic-update exercise should be
`v2.0.7-beta` to `v2.0.8-beta`.
