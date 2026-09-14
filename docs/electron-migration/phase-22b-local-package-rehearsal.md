# Phase 22B: local Electron package rehearsal

Phase 22B rebuilt the Windows artifacts after the source-entry cutover and
executed both the build-time and independent candidate validators. This was the
first local unsigned rehearsal and has since been superseded by the source-bound
Phase 22C artifacts; its hashes remain recorded as historical evidence.

## Build input and environment

- Application version: `2.1.0-beta`
- Host: Windows 11, build `26200`
- Python: `3.12.4`
- Node.js: `25.9.0`
- PyInstaller: `6.22.2`
- Electron: `44.3.0`
- Electron Builder: `26.15.3`

The rehearsal was built from a working tree containing the Phase 22A launcher
changes. Because those changes were not yet committed and no production keys
were supplied, `source_commit` and `release_key_id` are intentionally null and
the manifest is marked `local-rehearsal`.

## Local artifacts

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| `Novalist-v2.1.0-beta-windows-x64-setup.exe` | 125,634,887 bytes | `cae2efdd1c47a9a8b957a8b5f346c0a160150b3fca472828d6a2397e9d30c44e` |
| `Novalist-v2.1.0-beta-windows-x64.zip` | 168,757,948 bytes | `b92081bdcb991287a8fd621be83ac826a67d5cac322afc0479681ca038797434` |

The generated files are under `electron/release/publish/` and remain ignored
local build output. `SHA256SUMS.txt` and the schema-3 release manifest accompany
them.

## Verification evidence

The build pipeline completed:

- 551 Python tests passed, with 3 environment-dependent tests skipped;
- all 14 Electron/Node tests passed;
- PyInstaller produced the standalone Sidecar;
- unpacked Electron clean-profile/schema-v2 self-test passed;
- portable recovery self-test passed;
- silent per-user install, installed self-test, and uninstall passed;
- release-manifest artifact digest verification passed.

The separate candidate validator then passed:

- exact project-tree hash preservation during packaged open;
- schema-v2 workflow and AI-review surface checks;
- NSIS install and uninstall;
- portable recovery;
- ZIP backup and exact restore.

Its machine-readable report is stored locally at
`build/phase-22b-candidate-validation.json`.

## Exit criteria

- [x] Rebuild Sidecar, unpacked Electron, NSIS, and portable ZIP from the current
  Electron-only source entry.
- [x] Pass full Python and Electron regression suites.
- [x] Pass unpacked, installed, and portable schema-v2 self-tests.
- [x] Pass independent backup/restore and zero-write candidate validation.
- [x] Record artifact sizes and SHA-256 digests.
- [x] Commit the Phase 22A/22B source and documentation so a signed candidate can
  bind an exact source identity (`f20808020d6637bb6152318161811c5a879e0925`).
- [ ] Produce an officially signed candidate from that commit.
- [ ] Pass protected Windows 10/11 and live synthetic DSH gates.
