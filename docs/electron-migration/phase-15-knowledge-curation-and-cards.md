# Phase 15: knowledge curation and card workspace

Phase 15 turns the Phase 14 world and character-field projections into a
curatable Electron workspace. Accepted evidence remains the source of the base
knowledge; edits are durable overlays, generated cards remain derived output,
and author cards are the only directly editable Markdown knowledge files.

## Implemented scope

The curated-knowledge drawer now supports:

- editing, hiding, and restoring each allowlisted accepted character field;
- editing the name, category, and description of an accepted world entry;
- hiding and restoring a world entry without deleting its accepted proposal or
  evidence;
- opening generated character/world cards in a read-only viewer;
- opening and saving separate author character/world cards;
- retaining entity, alias, merge, and relationship operations from Phase 11A.

These operations are available only for schema-v2 projects. Structured
curation mutations and author-card saves are blocked while reconstruction is
active; card reads remain available. A hidden character field stays attached to
its active character. A hidden world is removed from active world projection
and its generated card, but remains visible in a recovery section and may keep
an author card.

## Persistence and audit model

`knowledge/curation.json` adds two compatible overlay collections:

- `character_field_overrides[entity_id][field]` stores the curated value and
  `hidden` state;
- `world_overrides[world_id]` stores the curated name/category/description and
  `hidden` state.

Every edit, hide, and restore appends a before/after operation to the existing
curation log. Reconciliation reapplies valid overlays to accepted, non-stale
base knowledge. Orphaned overlays do not recreate knowledge after its source
proposal becomes stale; they may apply again only if the same stable knowledge
ID returns through reviewed manuscript evidence.

Active worlds remain in `knowledge/world_rules.json.rules`; hidden worlds are
retained in the additive `hidden_rules` collection. Character records expose
visible `profile_fields` separately from `hidden_profile_fields`. This keeps
recovery state inspectable without sending hidden values into generated cards.

## Generated and author card ownership

Card locations are derived exclusively in Python from a validated owner kind
and stable accepted-knowledge ID:

- `knowledge/generated/characters/<entity-id>.md`;
- `knowledge/generated/world/<world-id>.md`;
- `knowledge/author/characters/<entity-id>.md`;
- `knowledge/author/world/<world-id>.md`.

The renderer never submits a path and receives no generic filesystem method.
Generated cards are read-only and are replaced or removed during
reconciliation. Author cards are created on first open and never rewritten by
reconstruction.

Author saves use an opaque `card-v1` content revision. A stale revision fails
instead of overwriting a newer edit. Card contents are bounded to 200,000
characters, and owner IDs must match the stable hashed ID formats before path
construction.

## Electron and RPC surface

The Sidecar adds typed operations for character-field and world curation plus
card open/save. Electron main validates arguments and response DTOs, and preload
exposes only named methods. The React drawer contains inline field/world editors,
explicit destructive confirmations, a hidden-world recovery list, and a modal
generated/author card workspace.

All knowledge mutations emit `knowledge.updated`. The application refreshes the
knowledge drawer and Graph View from Python projections; renderer state never
becomes authoritative.

## Compatibility and recovery

This remains project schema 2 and RPC protocol 1. Older schema-v2 curation files
receive empty overlay collections in memory and are written in the widened form
only after a curation mutation. `hidden_rules`, `hidden_profile_fields`, and
author-card directories are additive. No legacy world, character-card, or
relationship files are imported.

If generated files are missing, reconciliation recreates active cards. It does
not recreate hidden-world generated cards and cannot delete author content.

## Exit criteria

- [x] Accepted character fields can be edited, hidden, and restored without
  changing their proposals.
- [x] Accepted world entries can be edited, hidden, and restored with collision
  checks.
- [x] Every structured curation mutation has a durable before/after audit entry.
- [x] Hidden values survive reconciliation but do not enter active projections
  or generated cards.
- [x] Generated cards are read-only; author cards are independent and use
  revision-safe atomic saves.
- [x] Python derives all card paths from validated stable IDs; renderer code has
  no arbitrary filesystem capability.
- [x] Python service tests, real Node-to-Sidecar tests, TypeScript validation,
  production build, and Electron interaction/capture tests cover the workflow.

## Phase 16 continuation

Phase 16 implements reviewed timeline events and typed semantic links in
[`phase-16-timeline-semantic-graph.md`](phase-16-timeline-semantic-graph.md).
Python owns semantic nodes, links, evidence, order, and stable IDs; layout,
selection, filtering, and the event lane remain frontend state.
