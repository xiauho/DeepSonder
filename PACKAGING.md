# Novalist Windows packaging

## Electron-only candidate (Phase 18)

Future tagged distributions use Electron as the only user-facing entry. The
package contains `Novalist.exe`, the React renderer, and a private
`resources/sidecar/NovalistSidecar.exe`; it does not expose the PySide shell.

Install Python 3.12 and Node.js 24+ dependencies, then run:

```powershell
python -m pip install -r requirements.txt -r requirements-build.txt
cd electron
npm ci
cd ..
.\scripts\build_electron_windows.ps1 -PythonExecutable python
```

The script creates these local-rehearsal outputs under
`electron/release/publish/`:

- `Novalist-v<version>-windows-x64-setup.exe` (per-user NSIS installer);
- `Novalist-v<version>-windows-x64.zip` (portable recovery package);
- `release-manifest.json` (Electron-only schema 3);
- `SHA256SUMS.txt` and `rehearsal-report.json`.

It verifies the unpacked application and recovered ZIP against a clean profile,
opens a disposable schema-v2 project, and rejects any opening-time project
rewrite. Pass `-ExerciseInstaller` to add an install/start/uninstall rehearsal.
See
[`docs/electron-migration/phase-18-packaged-cutover-rehearsal.md`](docs/electron-migration/phase-18-packaged-cutover-rehearsal.md)
for the signing contract and release gates.

Phase 19 adds an embedded release-key policy and a clean-client validator. After
a signed package run, invoke the `Validate Electron Candidate` workflow with its
run ID; protected self-hosted Windows 10 and Windows 11 runners independently
produce install/backup/recovery reports. Configuration and reviewer gates are
documented in
[`phase-19-signed-prerelease-validation.md`](docs/electron-migration/phase-19-signed-prerelease-validation.md).

Unsigned manifests are marked `local-rehearsal` and must not be published. A
tagged GitHub workflow requires Ed25519 manifest-signing secrets and a Windows
Authenticode certificate, then verifies all executable signatures before it
uploads artifacts.

## Legacy PySide portable build

The following path describes the current public-release/maintainer rollback
package. It remains available during cutover but is no longer used by the tagged
Windows packaging workflow.

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

The automatic-install baseline defaults `minimum_updater_version` to the target
package version, which deliberately makes a newly built compatibility baseline
manual-install only. A maintainer may pass an older version only after that
exact installed updater has passed the package contract, failure-recovery, and
cross-volume installation suite. For example:

```powershell
.\scripts\build_windows.ps1 -MinimumUpdaterVersion 2.1.1-beta
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
archive and per-file checks, and creates its staging, verified backup, and
recovery journal inside the installation directory. Keeping all replacement
sources on the installation volume allows atomic replacement even when the
download cache and portable installation use different Windows drives. A failed
replacement or health check restores old files from the preserved backup
without deleting destinations first, verifies the restored installation, and
relaunches the old executable. An interrupted rollback can be retried from the
same backup. Unknown files and writing projects are never adopted into the
managed set; a collision with a new managed path stops the installation.

This is an opt-in beta updater, not an unattended system service. It does not
request elevation, replace a read-only installation, or overwrite an install
that no longer matches its recorded hashes. Release-manifest signing and
Windows Authenticode signing remain future hardening work, so the current trust
boundary still depends on GitHub HTTPS, repository Release permissions, GitHub
asset digests, and the local verified-state record.

The published `v2.0.7-beta` has no updater helper and therefore must be upgraded
manually. The updater shipped in `v2.0.8-beta` cannot safely replace an
installation on a different drive from `%LOCALAPPDATA%`: its cross-volume
replacement and rollback both fail. The first release containing the corrected
target-volume transaction implementation must therefore be installed manually
and must set `minimum_updater_version` to its own version. Only a later release
may opt back into automatic installation from that corrected baseline.
