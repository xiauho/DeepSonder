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

The automatic-install baseline defaults `minimum_updater_version` to
`2.0.7-beta`. This allows `v2.0.7-beta` to securely download and validate the
first package that contains the new helper, while installation of that first
package remains manual. Later releases must pass the oldest updater version
that actually supports their package and manifest contract. For example:

```powershell
.\scripts\build_windows.ps1 -MinimumUpdaterVersion 2.0.7-beta
```

The build script runs the full unit-test suite, creates the one-folder main
application and a separate one-file updater helper, runs
`Novalist.exe --self-test` and `NovalistUpdater.exe --self-test`, and writes
these files to `dist/release/`:

- `Novalist-v<version>-windows-x64.zip`
- `release-manifest.json`
- `SHA256SUMS.txt`

The ZIP root contains `Novalist.exe`, `NovalistUpdater.exe`, and a generated
`package-files.json` allowlist with the size and SHA-256 of every managed
program file. It also contains the project notices and a `licenses/` directory
with the complete third-party license texts required by the redistributed
Python, Qt/PySide6, PyInstaller and font components. The build fails if any
required license file is missing.

The package contains application code, Qt/Python runtime files, public assets,
the version file, and license/privacy documentation. It intentionally excludes
personal `config.json` files, writing projects, virtual environments, and update
caches.

The release manifest uses schema v2 and records the exact target platform,
archive name, byte size and SHA-256 digest. The download client cross-checks
those values with the GitHub Release asset metadata before accepting a file.

## Automatic-install boundary

The main process never replaces its own loaded files. It copies the updater
helper from the current installed package into the per-user update transaction,
then exits. The helper waits for that process, repeats the verified-state,
archive and per-file checks, backs up only paths listed in the current package
manifest, replaces only managed paths, and runs the new main executable's
`--self-test`. A failed replacement or health check restores the backup and
relaunches the old executable. Unknown files and writing projects are never
adopted into the managed set; a collision with a new managed path stops the
installation.

This is an opt-in beta updater, not an unattended system service. It does not
request elevation, replace a read-only installation, or overwrite an install
that no longer matches its recorded hashes. Release-manifest signing and
Windows Authenticode signing remain future hardening work, so the current trust
boundary still depends on GitHub HTTPS, repository Release permissions, GitHub
asset digests, and the local verified-state record.

The published `v2.0.7-beta` has no updater helper and therefore must be upgraded
to `v2.0.8-beta` manually. The first complete automatic-install release exercise
is `v2.0.8-beta` to `v2.1.0-beta`.
