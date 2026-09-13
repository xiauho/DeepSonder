# Phase 14: structured world and character-card reconstruction

Phase 14 widens schema-v2 reconstruction beyond character identities and
relationships. World concepts and bounded character fields now travel through
the same evidence, review, invalidation, and provenance boundary before they
can appear in durable knowledge.

## Proposal vocabulary

The proposal model now supports four kinds:

- `entity`: a character identity;
- `relation`: a directed relationship between two accepted characters;
- `world`: `name`, `category`, and one evidence-backed description;
- `character_field`: `character_name`, a field name, and one value.

Character fields are intentionally limited to identity, appearance,
personality, goal, ability, faction, and state (`身份`, `外貌`, `性格`, `目标`,
`能力`, `阵营`, `状态`). This prevents a remote producer from inventing an
unbounded card schema.

The conservative local extractor recognizes explicit
`[[世界:名称|类型|描述]]` and `[[角色字段:人物|字段|内容]]` markers. Structured
DSH extraction may identify the same information in natural prose, but must
return the strict four-collection JSON contract and reference only evidence IDs
that were sent for the current chunk.

## Review invariants

All new candidates remain pending by default. A character field requires its
character to be accepted already or in the same batch decision. Only one value
may be accepted for a normalized character/field key. Likewise, only one
description may be accepted for a normalized world-name/category key.

Alternatives remain separate proposals. Electron groups conflicting field and
world alternatives in the same conflict filter used for relationship variants;
no confidence threshold resolves them automatically.

Saving source正文 makes its proposal batch stale. Stale world and card fields
are removed from the active knowledge projection exactly like characters and
relationships.

## Generated cards and author-owned content

Accepted character fields are projected into `knowledge/entities.json` and
accepted world entries into `knowledge/world_rules.json`. Deterministic Markdown
views are generated under:

- `knowledge/generated/characters/<entity-id>.md`;
- `knowledge/generated/world/<world-id>.md`.

These files begin with an explicit generated-projection warning and may be
replaced or removed during reconciliation. They are never treated as input to
extraction. Separate `knowledge/author/characters/` and
`knowledge/author/world/` directories are created for author-owned additions and
are never rewritten by reconstruction.

Generated paths use stable hashed IDs rather than extracted names, so renamed
characters cannot escape the project or create platform-specific filenames.

## Electron surface

The evidence workbench renders all four candidate kinds with producer badges,
confidence guidance, evidence navigation, conflict grouping, dependency
preflight, and single or batch review. The curated-knowledge drawer displays
accepted character fields, world entries, evidence counts, and their generated
card paths. World/card projections remain read-only in this phase; existing
entity and relationship curation operations are unchanged.

## Quality baseline

`tests/fixtures/reconstruction_quality/corpus-v2.json` extends the privacy-safe
synthetic corpus to eight cases and adds independent precision, recall, F1, and
calibration output for `worlds` and `character_fields`.

Current recorded baseline:

| Mode | Subject | Precision | Recall | F1 |
|---|---|---:|---:|---:|
| Local | World concepts | 1.0000 | 0.5000 | 0.6667 |
| Local | Character fields | 1.0000 | 0.5000 | 0.6667 |
| Recorded combined | World concepts | 1.0000 | 1.0000 | 1.0000 |
| Recorded combined | Character fields | 1.0000 | 1.0000 | 1.0000 |

The combined figures use checked-in recorded candidates and are not a live DSH
performance claim. Existing entity and relation gates remain active in the same
report so vocabulary expansion cannot silently regress them.

## Compatibility and recovery

This is a compatible schema-2 extension. Existing required collections already
include `world_rules.json`; entity records allow additive projection fields, and
generated/author directories are derived or optional. Opening an earlier
schema-2 project reconciles these projections without importing any legacy
world or character-card data.

## Exit criteria

- [x] World and character-field candidates retain source evidence and producer
  provenance.
- [x] Local explicit extraction and strict DSH structured extraction support the
  widened vocabulary.
- [x] Character dependencies and conflicting accepted values are validated in
  Python before writes.
- [x] Only accepted, non-stale proposals enter knowledge and generated cards.
- [x] Generated projection files and author-owned directories have separate
  ownership and rewrite rules.
- [x] Electron review and knowledge surfaces render the new types.
- [x] Corpus-v2 and CI quality gates cover all four proposal subjects.
- [x] Python, Node/Sidecar, production build, and real Electron interaction
  tests cover the new projection loop.

## Phase 15 continuation

Phase 15 implements this curation boundary in
[`phase-15-knowledge-curation-and-cards.md`](phase-15-knowledge-curation-and-cards.md):
world entries and character fields can be edited, hidden, and restored through
audited overlays, while generated cards remain read-only and author cards use
independent revision-safe saves.
