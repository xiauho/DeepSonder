# Phase 17: semantic-link curation and timeline diagnostics

Phase 17 closes the correction loop for the reviewed timeline introduced in
Phase 16. Authors can now change event participants and world references by
stable knowledge IDs, explicitly order active events, inspect deterministic
time/link diagnostics, and navigate every reported issue back to manuscript
evidence.

## Durable curation model

`knowledge/curation.json` remains schema 1 and gains one additive field:

- `event_order`: stable event IDs in author-defined order.

An event override may additionally contain `participant_entity_ids` and
`world_ids`. These are curated references, not copied display names. The Python
service validates every submitted ID against the current active knowledge
projection, rejects duplicates and incomplete reorder lists, and records
`event.links.update` or `event.order.update` operations before rebuilding.

Manual order does not modify `source_order`, which remains the deterministic
manuscript-evidence fallback. Hidden events retain their slot in the complete
curated order; new or newly reconstructed events are appended by source order.

## Reconciliation and recovery

Entity merges resolve curated participants to the retained entity. Hiding a
referenced world removes that semantic edge from the active projection but does
not erase the event override. Restoring the world recreates the link on the next
rebuild. Stale or unknown IDs never become graph nodes or edges.

Projects created before Phase 17 omit `event_order`; the reader normalizes that
to an empty list and falls back to evidence order without a schema migration.

## Diagnostics

`knowledge/timeline.json.diagnostics` is a derived collection. It currently
reports:

- `temporal_overlap`: multiple active events use the same normalized time label;
- `missing_character_link`: a persisted participant can no longer resolve;
- `inactive_world_link`: a referenced world is hidden or no longer active.

Diagnostics carry stable event IDs and evidence IDs. They are advisory and
never silently edit the timeline. They are also projected into Graph View's
warning list. Editing knowledge or restoring a dependency rebuilds and clears
obsolete diagnostics automatically.

## Electron surface

The knowledge drawer provides checkbox-based stable-ID link editing and
per-event up/down ordering controls. Event cards and diagnostic notices resolve
evidence IDs through the read-only knowledge snapshot and open the owning
manuscript chapter through the existing guarded document workflow.

Graph semantic-edge evidence is now an explicit chapter-navigation control.
The event lane consumes the Python-owned order and remains a layout-only
frontend projection.

## RPC additions

- `knowledge.updateEventLinks(eventId, participantEntityIds, worldIds)`;
- `knowledge.reorderEvents(eventIds)`.

The knowledge snapshot adds bounded evidence references and diagnostics. The
renderer still has no generic path, file, or query capability.

## Exit criteria

- [x] Event participants and world references are edited only by validated
  stable IDs and survive reconstruction.
- [x] Manual order is audited and does not overwrite manuscript source order.
- [x] Hidden dependencies produce diagnostics without deleting curated links,
  and restoration recreates valid links.
- [x] Same-time events produce deterministic advisory diagnostics.
- [x] Knowledge diagnostics and graph evidence navigate to owning chapters.
- [x] Python service tests, Node-to-Sidecar integration, type checking,
  production build, and real Electron capture cover the workflow.

## Recommended Phase 18

Begin the packaged Electron-only cutover rehearsal: bundle the Python Sidecar,
build the Windows installer and portable recovery artifact, verify first-run and
existing schema-v2 project opening on a clean machine profile, exercise backup
and rollback, and validate signed update metadata before removing the PySide6
entry from distributed builds.
