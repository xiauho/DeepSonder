# Phase 22C: source-bound local candidate

Phase 22C repeats the complete Windows rehearsal from the committed Phase 22A/B
baseline and carries the exact Git identity through the manifest and independent
candidate report. It validates release provenance mechanics without pretending
that local unsigned artifacts are publishable.

## Source identity

- Version: `2.1.0-beta`
- Commit: `f20808020d6637bb6152318161811c5a879e0925`
- Working tree before build: clean
- Manifest signature: `none` (`local-rehearsal`)
- Authenticode requirement: false

Both `release-manifest.json` and
`build/phase-22c-source-bound-validation.json` contain the same complete source
commit. The independent validator rejects artifact or project-tree drift but is
invoked with `AllowUnsignedLocalRehearsal`, so this evidence cannot pass the
protected production gate.

## Current local artifacts

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| `Novalist-v2.1.0-beta-windows-x64-setup.exe` | 125,636,492 bytes | `73ff714abcf6622d6bfb8b415d491921f45be1dedefeec74a9757203fe1f1860` |
| `Novalist-v2.1.0-beta-windows-x64.zip` | 168,760,402 bytes | `03b43967982c347b995e1a36c60d3024bf83a80a9e933428b2350220a2be59ee` |

These files replace the earlier Phase 22B local outputs under
`electron/release/publish/`.

## Verification

- 551 Python tests passed; 3 were skipped.
- All 14 Electron/Node tests passed.
- Standalone Sidecar, unpacked Electron, portable ZIP, and NSIS were rebuilt.
- Unpacked, portable, and installed clean-profile/schema-v2 checks passed.
- Silent install and uninstall passed.
- Independent project zero-write, backup, exact restore, workflow-surface, and
  AI-review-surface gates passed.
- No repository Electron process remained after validation.

## Exit criteria

- [x] Start from a committed clean source baseline.
- [x] Embed the complete Git object ID in local release metadata.
- [x] Preserve that identity in the independent candidate report.
- [x] Bind the recorded hashes to the same version and source.
- [x] Pass full source, package, install, recovery, and project-integrity checks.
- [ ] Provision protected Ed25519 and Authenticode credentials.
- [ ] Rebuild the same chosen release commit as an officially signed candidate.
- [ ] Pass Windows 10/11 and live synthetic DSH release-readiness gates.
