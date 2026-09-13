# Phase 18: packaged Electron-only cutover rehearsal

Phase 18 turns the source-tree Electron vertical slice into a reproducible
Windows distribution candidate. It does not delete the legacy PySide source;
it changes the release gate so a future tagged build has exactly one user-facing
entry point: `Novalist.exe` from Electron.

## Package layout

`electron-builder` produces a per-user NSIS installer and a portable recovery
ZIP. Both contain the same Electron application and this private runtime:

```text
Novalist.exe
resources/
  app.asar
  sidecar/NovalistSidecar.exe
  assets/app_icon.ico
  legal/...
```

`NovalistSidecar.exe` is a console-mode PyInstaller one-file executable because
stdout is the strict NDJSON RPC transport. PySide6 and the legacy `ui` package
are excluded. Electron resolves this executable only from `process.resourcesPath`
in packaged mode and rejects an application-version mismatch during handshake.

## Rehearsal gates

`scripts/build_electron_windows.ps1`:

1. requires Python 3.12, Node.js 24+, and matching root/Electron versions;
2. optionally runs the complete Python and Electron regression suites;
3. builds the Sidecar, Electron unpacked directory, NSIS installer, and recovery
   ZIP from clean output directories;
4. checks the packaged legal files and fixed Sidecar location;
5. starts the unpacked build with a new isolated Electron and Sidecar profile;
6. opens a newly created schema-v2 project and compares every project file hash
   before and after the check;
7. extracts the portable ZIP to a separate recovery directory and repeats the
   same read-only project-opening check;
8. optionally installs NSIS into an isolated per-user directory, repeats the
   check, and invokes the generated uninstaller;
9. emits a schema-3 `electron-only` release manifest and SHA-256 checksums.

The project creator now writes the current empty world/timeline collection
shape, so a first open does not perform an incidental compatibility rewrite.
A Python regression test protects this invariant.

## Release trust boundary

Local rehearsals are explicitly marked with
`signature.algorithm = "none"` and `reason = "local-rehearsal"`; they are not
publishable releases. A tagged CI build fails unless both of these independent
trust gates are available:

- an Ed25519 key pair signs the exact release-manifest bytes, with the trusted
  public-key SPKI fingerprint recorded as `key_id`;
- a Windows code-signing certificate allows Authenticode validation of the
  Electron executable, embedded Sidecar, and NSIS installer.

The CI job decodes its certificate into a temporary PFX. The build signs the
one-file Sidecar before Electron packaging, then Electron Builder signs the main
executable and NSIS artifacts. A final gate checks all three signatures, which
prevents an unsigned `extraResources` executable from slipping into the ZIP.

The public Ed25519 key must be pinned outside the downloadable release assets.
Shipping a public key beside a manifest without an independently trusted
fingerprint would not prevent manifest substitution.

## Update and rollback boundary

This phase deliberately does not reuse the legacy in-place Python updater. The
NSIS installer is the primary installation route; the versioned portable ZIP is
the recovery route and can be extracted beside an existing installation. User
projects are external data and are never included in, adopted by, or replaced
by either artifact. A future automatic Electron updater must consume the signed
schema-3 manifest and preserve this side-by-side recovery property before it may
be enabled.

## Local evidence

The Phase 18 implementation was exercised on Windows with:

- unpacked packaged Electron + embedded Sidecar: passed;
- portable recovery ZIP extraction and startup: passed;
- NSIS silent install, startup, and uninstall: passed;
- clean-profile schema-v2 open with identical before/after hashes: passed;
- local unsigned manifest digest validation: passed;
- Ed25519 exact-byte signing and tamper rejection tests: passed.

The local artifacts remain explicitly unsigned rehearsal outputs. Only tagged
CI builds with configured signing secrets are eligible for publication.

## Exit criteria

- [x] Packaged Electron does not depend on repository Python or source paths.
- [x] Installer and portable recovery artifacts share one Electron-only layout.
- [x] Clean-profile startup opens schema v2 without modifying project files.
- [x] NSIS install/uninstall and portable recovery rehearsals pass.
- [x] Release manifests bind both artifacts by size and SHA-256.
- [x] Ed25519 signing and tamper rejection are automated.
- [x] Tagged builds fail closed when metadata or Authenticode keys are absent.
- [x] Legacy PySide packaging remains available only as an explicit rollback
  build script during the cutover; it is no longer the tagged-package workflow.

## Recommended Phase 19

Run the signed workflow on a protected prerelease tag and test the resulting
artifacts on clean Windows 10 and Windows 11 machines. Pin the release public-key
fingerprint in the application/update policy, publish a user-facing backup and
manuscript-only transition guide, and collect one full-cycle install/recovery
report before declaring Electron the stable public entry.
