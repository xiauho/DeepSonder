# Phase 21: release-readiness evidence binding

Phase 21 turns the separate signed-package and live-AI checks into one fail-closed
release-review input. It does not publish a release and does not enable automatic
updates.

## Source-bound candidate

Every protected packaging run now requires both Ed25519 release-metadata keys
and the Windows Authenticode credential, including manually dispatched runs.
The complete Git object ID is embedded in the schema-3 manifest before its exact
bytes are signed. Unsigned local rehearsals may remain unbound, but they cannot
pass the clean-client or final readiness gates.

The Windows 10 and Windows 11 reports carry that source identity forward. Their
aggregate gate rejects different versions, source commits, release keys, or
artifact hashes.

## Source-bound live AI

The protected synthetic DSH workflow records the application version and the
complete Git object ID. It still requires explicit remote-processing consent,
uses only a disposable synthetic manuscript, and emits no prompts or generated
prose.

`release-readiness-gate.mjs` requires exactly one passing connection, check,
expansion, continuation, and memory case. Writing and memory generation must
remain review-first, and the report must match the signed candidate's version
and source commit.

## Human authority boundary

The final report says `eligible_for_manual_release_review`; it never authorizes
publication. A protected-environment reviewer must still verify the expected
release key through an independent channel, inspect the evidence artifacts, and
approve distribution.

## Exit criteria

- [x] Protected manual and tagged packaging runs fail closed without both
  metadata-signing and Authenticode credentials.
- [x] Signed manifests bind version, artifacts, release key, and source commit.
- [x] Clean Windows reports preserve and compare the source commit.
- [x] Live synthetic DSH reports preserve app version and source commit.
- [x] One automated gate binds both evidence families to the same source.
- [x] Deterministic tests reject mismatched sources and bypassed AI review.
- [ ] Run the protected live DSH workflow for the chosen release commit.
- [ ] Package that same commit and pass clean Windows 10/11 validation.
- [ ] A release reviewer verifies the bound report and approves publication.

## Operator sequence

1. Dispatch `Validate schema-v2 AI with DSH` on the chosen commit and retain its
   run ID.
2. Dispatch `Package Electron Windows` on the same commit and retain its run ID.
3. Dispatch `Validate Electron Candidate`, supplying both run IDs.
4. Confirm that `novalist-release-readiness-review` reports the expected version,
   source commit, release key fingerprint, and artifact hashes.
5. Complete the independent human review before creating a public release.
