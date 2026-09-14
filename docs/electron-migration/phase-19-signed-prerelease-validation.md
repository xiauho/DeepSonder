# Phase 19: signed prerelease and clean-client validation

Phase 19 establishes the release-acceptance path between a local Phase 18
rehearsal and the first stable Electron-only distribution. Repository work is
complete; the final release decision remains blocked until real signing
credentials and clean Windows 10/11 client runners produce passing reports.

## Embedded release trust

Signed builds derive an Ed25519 SPKI fingerprint from the independently managed
release public key and generate `generated/release-trust.json` before Electron
packaging. The policy and public key are placed inside `app.asar`; Electron's
ASAR-integrity record is embedded in the Authenticode-signed executable.

On startup, packaged Electron rejects a missing policy, malformed key, or a key
whose fingerprint differs from `key_id`. Candidate validation also launches the
application with `--expected-release-key-id=<fingerprint>`, binding the external
protected-environment key to the policy inside the application. Local unsigned
packages contain only a `local-rehearsal` policy and cannot claim a production
key.

This prepares a trust root for a later in-app update client without enabling
automatic updates in Phase 19.

## Protected prerelease workflow

`.github/workflows/package-windows.yml` now uses the
`electron-prerelease` GitHub environment. Configure that environment with
required reviewers, disallow self-approval, restrict deployment branches/tags,
and provide:

- `NOVALIST_RELEASE_PRIVATE_KEY_B64`: Base64 PKCS#8 Ed25519 private PEM;
- `NOVALIST_RELEASE_PUBLIC_KEY_B64`: Base64 SPKI Ed25519 public PEM;
- `NOVALIST_WINDOWS_CERTIFICATE_B64`: Base64 PFX signing certificate;
- `NOVALIST_WINDOWS_CERTIFICATE_PASSWORD`: PFX password.

The private key and PFX must not enter the repository or uploaded artifacts.
The public-key fingerprint must be recorded in the protected release procedure
through a separate channel before approving a candidate.

## Clean Windows client matrix

`.github/workflows/validate-electron-candidate.yml` consumes the artifact from a
specific packaging run. It deliberately requires self-hosted runners labelled:

- `self-hosted`, `windows`, `x64`, `novalist-clean`, `windows-10`;
- `self-hosted`, `windows`, `x64`, `novalist-clean`, `windows-11`.

Windows Server runners are not accepted as substitutes for the client matrix.
Each runner must start from a maintained clean snapshot, have Node.js bootstrap
support for the action runner, and contain no pre-existing Novalist install.
Revert the runner snapshot after every validation job.

## Full-cycle validator

`scripts/validate_electron_candidate.ps1` needs no Python runtime. It:

1. validates the signed schema-3 manifest against the protected public key;
2. verifies Authenticode on the installer and both executables in the ZIP;
3. builds an offline synthetic schema-v2 manuscript project;
4. backs up the project and records a complete tree fingerprint;
5. installs NSIS, verifies the embedded key ID, opens the project, and uninstalls;
6. starts the portable recovery application against the unchanged project;
7. restores the project backup to a separate directory and opens it again;
8. emits an OS/version, artifact-digest, signature, install, and recovery report.

Phase 20D extends the packaged self-test with schema-v2 chapter/import/export and
AI-review surface checks. After both matrix jobs finish,
`electron/scripts/candidate-report-gate.mjs` now fails unless the Windows 10 and
Windows 11 reports identify the same version, embedded release key, installer,
and portable artifact and every required gate passed.

The validator exposed and fixed one additional data invariant: reconstruction
projection files are no longer rewritten merely because a valid external tool
used different JSON whitespace or line endings. Writes occur only when parsed
project data actually changes.

## Local evidence

On the development Windows 11 machine, the unsigned local route passed:

- embedded local trust-policy loading;
- NSIS install/open/uninstall;
- schema-v2 open with identical before/after file hashes;
- project ZIP backup and exact restore;
- portable recovery opening both original and restored projects.

This evidence validates the workflow mechanics, not production signatures.

## Exit criteria

- [x] Production public key and fingerprint can be embedded inside protected
  `app.asar` integrity and checked at startup.
- [x] Local rehearsal policy cannot impersonate a production trust policy.
- [x] A Python-free full-cycle candidate validator produces a machine report.
- [x] Windows 10/11 clean-client jobs and protected release environment are
  encoded in workflows.
- [x] Windows 10/11 reports are automatically bound to one exact signed
  candidate before the release-gate artifact is emitted.
- [x] User-facing manuscript-only transition and backup guidance exists.
- [x] Local Windows 11 full-cycle install/backup/recovery rehearsal passes.
- [ ] Repository administrators configure and protect `electron-prerelease`.
- [ ] The official Ed25519 and Authenticode credentials are provisioned.
- [ ] One signed prerelease packaging run passes its fail-closed gates.
- [ ] Clean Windows 10 and Windows 11 reports both pass for that exact run ID.
- [ ] A release reviewer verifies the bound report and approves public
  distribution.

## Recommended next step

Provision the release identities and clean-client runners, run the protected
prerelease workflow, and attach both validation reports to the release review.
Do not enable automatic updates or call Electron stable until every unchecked
criterion above is satisfied.
