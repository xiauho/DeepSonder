# Phase 24: latest candidate and reconstruction quality expansion

## Phase 24A source-bound local candidate

The Electron-only Windows rehearsal was rebuilt from the complete Phase 23B
commit `de97a1f34cfd1a8664188e6775363c2537fa675f`.

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| `Novalist-v2.1.0-beta-windows-x64-setup.exe` | 125,568,995 | `2d87833597d4d061a1176675232a5c3c0ab2229d7a7b0d9bf03858fbc968ae1b` |
| `Novalist-v2.1.0-beta-windows-x64.zip` | 168,713,445 | `cd872994274e5e6508511d9aafecd47bb2a0876d6ad80bff3db153643a1d482d` |

Python tests (551 passed, 3 skipped), Electron tests (18 of 18), renderer
budgets, unpacked/portable/installed self-tests, backup restore, and zero-write
schema-v2 opening passed. The independent report is stored locally at
`build/phase-24a-source-bound-validation.json` and carries the same complete
source commit.

This remains unsigned local-rehearsal output. The report explicitly marks
metadata and Authenticode signatures as not required and therefore cannot be
used as public-release evidence.

## Phase 24B broader offline reconstruction baseline

The checked-in schema-v3 corpus now contains 14 synthetic cases instead of 8.
New coverage includes historical naming, science-fiction world concepts,
multi-chapter state changes, ensemble directed relationships, pronoun
continuity, and a place/organization negative control.

Cases may declare `local_expected` separately from the complete `expected`
knowledge. This makes the contract explicit: deterministic local rules are a
high-precision fallback, while natural semantic reconstruction belongs to the
combined local-plus-enhanced mode. Omitting `local_expected` preserves the
historical behavior.

The expansion exposed and fixed one local false positive. The action heuristic
could classify a four-character subject/object fragment such as “许棠把伞” as a
person before a following action verb. The fallback now limits this heuristic
to ordinary two- or three-character names; four-character names remain
available through explicit evidence or enhanced extraction.

The expanded offline gate passes without lowering any threshold:

| Mode | Subject | Precision | Recall | F1 |
| --- | --- | ---: | ---: | ---: |
| Local | Entities | 1.0000 | 0.7857 | 0.8800 |
| Local | Relations | 1.0000 | 0.7500 | 0.8571 |
| Local | Worlds | 1.0000 | 0.5000 | 0.6667 |
| Local | Character fields | 1.0000 | 0.5000 | 0.6667 |
| Local | Events | 1.0000 | 0.5000 | 0.6667 |
| Combined fixture | Entities | 1.0000 | 1.0000 | 1.0000 |
| Combined fixture | Relations | 0.9000 | 1.0000 | 0.9474 |
| Combined fixture | Worlds | 1.0000 | 1.0000 | 1.0000 |
| Combined fixture | Character fields | 1.0000 | 1.0000 | 1.0000 |
| Combined fixture | Events | 1.0000 | 1.0000 | 1.0000 |

The recorded enhanced candidates remain deterministic fixtures, not claims
about a current live DSH model. Capturing a real model baseline requires the
release owner's explicit remote-processing and cost consent.

## Remaining external gates

- configure and protect the `electron-prerelease` environment;
- provision Ed25519 metadata and Authenticode credentials outside the repository;
- run live synthetic DSH validation and signed packaging from one chosen commit;
- pass clean Windows 10 and Windows 11 validation for that exact candidate;
- complete independent human release review.
