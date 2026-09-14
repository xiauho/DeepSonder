# Phase 25: tiered Windows release security

Phase 25 separates update authenticity from Windows publisher reputation. A
small open-source prerelease can be distributed without buying an Authenticode
certificate, but an automatically consumable candidate must still prove who
published its metadata and exactly which bytes belong to the release.

## Release tiers

| Gate | Prerelease (`*-beta`, `*-alpha`, or other suffix) | Stable |
| --- | --- | --- |
| Ed25519 manifest signature | required | required |
| Artifact size and SHA-256 | required | required |
| Embedded public-key fingerprint | required | required |
| Windows Authenticode | optional; absent is recorded | required |
| Invalid or partial Authenticode | rejected | rejected |
| Windows 10/11 install and recovery | required | required |
| Human publication review | required | required |

The tier is derived from `VERSION` and is also written to the schema-4 release
manifest. A version containing a prerelease suffix must declare `prerelease`;
a version without one must declare `stable`. Callers cannot downgrade a stable
version by supplying a different tier.

The clean-client validation report and aggregate release-readiness report use
schema 2. This prevents older schema-1 reports, whose `signatures` gate always
meant both Ed25519 and Authenticode, from being reinterpreted under the tiered
policy.

## Protected packaging

`NOVALIST_RELEASE_PRIVATE_KEY_B64` and
`NOVALIST_RELEASE_PUBLIC_KEY_B64` remain mandatory in the
`electron-prerelease` environment. The optional Authenticode pair is:

- `NOVALIST_WINDOWS_CERTIFICATE_B64`;
- `NOVALIST_WINDOWS_CERTIFICATE_PASSWORD`.

Both Windows values must be present together. If neither is configured, a
prerelease is built with signed metadata and reports Authenticode as
`not_present`. If either value is incomplete, signing fails, or only part of the
candidate is signed, packaging or clean-client validation fails. A stable
version fails unless every required executable and installer has a valid
signature.

## Security boundary

`not_present` is not equivalent to an unsigned update manifest. The public-key
fingerprint is embedded in the application, the exact manifest bytes are signed
with Ed25519, and the manifest binds the installer and portable ZIP by size and
SHA-256. This is the minimum release identity for a future user-confirmed update
flow.

Authenticode remains valuable for publisher display, SmartScreen reputation,
and managed Windows environments. Its absence must be disclosed on prerelease
download pages, and prerelease updates must not be silent. Automatic updating
remains disabled until the Electron update client independently verifies the
Ed25519 signature before downloading or applying a package.

## Exit criteria

- [x] Release tier is derived from and bound to the semantic version.
- [x] Protected prereleases require Ed25519 but can omit Authenticode.
- [x] Invalid, partial, and mismatched Authenticode evidence fails closed.
- [x] Stable candidates require valid Authenticode.
- [x] Windows 10/11 reports and the aggregate readiness report preserve the tier
  and Authenticode state while binding the separate live-AI evidence.
- [ ] Provision the protected Ed25519 key pair.
- [ ] Run one metadata-signed prerelease through both clean-client validators.
- [ ] Add the user-confirmed Electron update client with pinned-key verification.
