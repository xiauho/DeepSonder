# Phase 11A: durable knowledge curation

Phase 11A establishes the human-owned layer between reviewed extraction and
Graph View. Extraction still creates proposals, review still decides what may
enter the knowledge base, and curation now controls how accepted knowledge is
presented and connected.

## Persistence model

`knowledge/curation.json` stores schema-1 entity overrides, entity merge
mappings, relationship overrides, and an append-only operation history. The
same operations are mirrored to `provenance/curation_log.json` whenever
knowledge is rebuilt. Both files use atomic project-local writes.

The accepted proposal batch remains the evidence authority. The curation file
is a projection layer, so reconstruction never rewrites a user's name, alias,
merge, or relationship decision. If an accepted proposal becomes stale or is
reopened, its override becomes dormant; accepting the same stable identity
again restores that override.

## Supported operations

- Rename an accepted person after checking names and aliases for collisions.
- Replace up to 20 aliases for a person.
- Merge one accepted identity into another. Evidence and aliases are combined,
  relationships are redirected, and self-relationships are suppressed.
- Split a merged identity by removing its merge mapping. The original accepted
  identity and its own curated fields reappear; this is not synthetic entity
  creation.
- Change a relationship's source, target, direction, or label.
- Delete a relationship from the knowledge projection without erasing the
  underlying reviewed proposal.
- Reopen an accepted or rejected proposal so it returns to the pending review
  queue.

Every operation records its kind, subjects, timestamp, and before/after payload.
The current UI displays the operation count; the full log remains a structured
project artifact for later history and undo interfaces.

## Electron behavior

The v2-only **知识** drawer presents editable entity and relationship cards,
active merge mappings, and split controls. Destructive-looking merge and
relationship-delete actions require confirmation. Successful changes emit a
validated `knowledge.updated` event and refresh both the drawer and Graph View.

The **识别** drawer can show reviewed proposals and return either accepted or
rejected items to pending. Curation and review mutations are unavailable while
a background reconstruction task is active, preventing mixed revisions.

## Safety properties

- Renderer code receives named operations only; it cannot write project JSON.
- Electron main validates IDs, labels, alias bounds, result DTOs, and events.
- Python revalidates every identity and relationship against the active v2
  project and rejects merge cycles, self-relations, or identity collisions.
- Merge is reversible and never deletes accepted proposal evidence.
- Rebuild and reopen reconcile the projection from accepted evidence plus the
  durable curation layer.

## Exit criteria

- [x] Entity name and aliases can be curated and survive reopen/rebuild.
- [x] Accepted identities can be merged and split without losing evidence.
- [x] Directed relations can be relabeled, reversed, or removed.
- [x] Accepted/rejected proposals can return to pending review.
- [x] All manual operations are written to a provenance audit log.
- [x] Graph View refreshes from the curated projection.
- [x] Python, Sidecar, Node-to-Sidecar, TypeScript, production-build, and real
  Electron interaction checks cover the new boundary.

## Intentional limits and next step

This phase does not introduce free-form entities unrelated to accepted text or
arbitrary operation rollback. DSH-generated structured candidates, strict
token/privacy budgets, local fallback, and the unchanged review boundary are
implemented by Phase 11B.
