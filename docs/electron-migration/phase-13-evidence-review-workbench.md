# Phase 13: evidence-centered review workbench

Phase 13 turns reconstruction review from a linear accept/reject list into a
bounded workbench for comparing evidence and making explicit grouped decisions.
It does not widen extraction authority: local rules and DSH still create only
proposals, and confidence never changes project knowledge by itself.

## Review model

The latest non-stale proposal batch can be filtered by pending, conflicting,
duplicate, reviewed, or all candidates. Conflict groups contain relationships
for the same unordered pair whose direction or label differs. Exact candidates
reported by both producers remain one proposal with both producer badges and
all deduplicated evidence.

Within each filter the renderer orders conflicts first, then producer overlaps,
then confidence. Confidence is shown as a high/medium/low band and percentage
only to guide inspection. There is deliberately no threshold, default decision,
or automatic acceptance path.

Every pending card retains its evidence excerpts and chapter navigation. A
conflicting relationship expands its evidence by default so alternatives can be
compared before a decision.

## Batch-decision boundary

Users explicitly select proposals and confirm one shared accept or reject
decision. `reconstruction.reviewMany` accepts 1 to 200 unique pending proposal
IDs from one non-stale batch. Python validates the complete decision set before
changing any proposal:

- every ID must exist, be unique, and still be pending;
- every decision must be `accepted` or `rejected`;
- an accepted relationship requires both endpoint characters to be accepted
  already or accepted in the same request.

An invalid item rejects the complete request without changing the proposal
batch. Once validation succeeds, all selected statuses receive one review time
and `review_mode: batch`; the batch and index are each written atomically and
the knowledge projection is rebuilt once. Single-card review uses the same
service path and records `review_mode: single`.

The renderer preflights relationship dependencies using both the durable
knowledge snapshot and selected character proposals. Python remains
authoritative and repeats the check at commit time.

## Audit and recovery

Reviewed cards expose whether the decision was single or batch review and its
timestamp. Existing proposal reopening remains available: it removes the
decision timestamp/mode, records a `proposal.reopen` curation operation, and
rebuilds knowledge. Stale batches cannot be reviewed or reopened and remain
excluded from knowledge and Graph View.

The batch API makes a set of validation decisions all-or-none, but it is not a
general multi-file database transaction. Existing atomic JSON replacement and
deterministic index/knowledge reconciliation remain the crash-recovery model.

## Security and stability

- The renderer receives no filesystem or generic IPC capability.
- Electron main validates batch IDs, decision counts, IDs, and decision values
  before calling the allowlisted Sidecar method.
- Python repeats all semantic validation and owns durable writes.
- Bulk review is disabled while extraction is active and always requires a
  visible confirmation.
- Rejection is a review decision, not deletion; evidence and proposal history
  stay inspectable.

## Exit criteria

- [x] Pending, conflict, duplicate, reviewed, and all filters are available.
- [x] Conflict and duplicate signals retain producer and evidence context.
- [x] Selection is explicit and bulk actions require confirmation.
- [x] Invalid or stale batch input cannot partially apply a decision set.
- [x] Relationship dependencies may be accepted in the same valid batch.
- [x] Confidence remains guidance only and cannot auto-adopt knowledge.
- [x] Review mode and timestamp are visible, and reopening remains supported.
- [x] Python, RPC, Node/Sidecar, typecheck, build, and real Electron rendering
  cover the widened review boundary.

## Phase 14 continuation (completed)

[`phase-14-structured-knowledge-projection.md`](phase-14-structured-knowledge-projection.md)
widens the schema-v2 vocabulary to evidence-backed world concepts and character
fields, projects only accepted values into generated cards, separates author
content, and expands the quality corpus without relaxing character or relation
gates.
