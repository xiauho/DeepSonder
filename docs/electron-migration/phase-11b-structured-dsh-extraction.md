# Phase 11B: structured DSH extraction

Phase 11B adds DSH as an optional proposal producer without making it a source
of truth. Local rules always run. A successful DSH response is merged with
local candidates; an unavailable, over-budget, or invalid response is discarded
in full and the batch continues with local candidates only.

## Consent and privacy boundary

Local extraction is the default. Each DSH-enhanced run requires an explicit
`remoteConsent: true`; the Electron checkbox resets after the task starts so a
later run requires another authorization.

Only these fields cross the DSH boundary:

- evidence ID;
- v2 chapter ID;
- the corresponding manuscript evidence text.

Absolute paths, project metadata, old character/world files, accepted knowledge,
curation overrides, audit history, and unrelated configuration are excluded.
DSH runs in the existing isolated temporary workspace and its short-lived task
file is cleaned by the hardened client.

Manuscript text is explicitly treated as untrusted data in the system prompt;
instructions embedded in the novel are not executable task instructions.

## Budget and schema gates

- At most 240,000 source characters and 16 remote chunks may be sent in one
  reconstruction task.
- Each chunk stays below both a 32,000-character cap and the active DSH prompt
  budget. The DSH client independently enforces its configured token ceiling.
- Each remote output is capped at 500,000 characters; the combined task is
  capped at 500 distinct entities and 1,000 distinct relationships.
- The response must be one JSON object with exactly `entities` and `relations`.
- Candidate objects reject unknown fields, control characters, empty values,
  more than 12 evidence references, unknown evidence IDs, self-relations, and
  relationships whose endpoint entities were not declared.

No partial remote response is accepted. Validation failure produces the generic
redacted fallback reason `remote_unavailable_or_invalid`; raw model output and
manuscript text are not copied into task errors or diagnostic events.

## Producer and review behavior

Remote entity and relationship candidates are normalized into the Phase 9
proposal schema and deduplicated with local candidates by stable identity key.
Evidence lists are unioned, but DSH cannot set acceptance, change curated
identity, or write Graph View.

Every generated batch records:

- requested mode and effective producer (`local` or `local+dsh`);
- whether fallback occurred and its redacted reason;
- evidence segment and remote chunk counts;
- a diagnostic input-token estimate;
- the privacy scope used by the task.

All candidates start as `pending`. Entity-first relationship validation,
proposal reopening, Phase 11A curation, manuscript invalidation, and reviewed
Graph View projection are unchanged.

## Electron behavior

The reconstruction drawer explains the exact data scope and fallback behavior.
The DSH checkbox is disabled while a task runs and reset after a successful
start. Task progress identifies DSH chunks, while the completed batch reports
whether it used local-only, combined, or fallback production.

## Exit criteria

- [x] Local extraction remains the default and requires no DSH installation.
- [x] Every enhanced run carries explicit renderer-to-Python consent.
- [x] DSH sees only bounded manuscript evidence in an isolated workspace.
- [x] Output is strict, evidence-linked JSON with all-or-nothing validation.
- [x] Invalid, failed, or over-budget DSH output safely falls back to local rules.
- [x] Remote candidates cannot bypass pending review or human curation.
- [x] Batch metadata makes effective producer and fallback visible.
- [x] Unit and integration tests use fake clients and never call a live model.

## Phase 12 continuation

Phase 12 implements the privacy-safe annotated corpus, local/combined quality
metrics, duplicate/conflict diagnostics, confidence-calibration reporting, and
CI regression gates without weakening explicit review.
