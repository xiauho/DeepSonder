# Phase 10: cancellable background reconstruction

Phase 10 removes synchronous manuscript analysis from the serialized Sidecar
request path. Reconstruction now runs as a local background task with progress,
cooperative cancellation, per-chapter checkpoints, and an unchanged review
contract.

## Task lifecycle

- `reconstruction.start` creates one project-bound task and returns immediately.
- Task states are `queued`, `running`, `cancel_requested`, `succeeded`, `failed`,
  and `cancelled`.
- `reconstruction.taskStatus` provides reconnect-safe active/recent state, while
  `reconstruction.taskUpdated` streams progress through the existing Sidecar
  event channel.
- `reconstruction.cancel` is cooperative. Cancellation never commits a partial
  proposal batch.
- Sidecar shutdown requests cancellation and waits for the worker to finish.

Only one reconstruction job may run. Project create/open/close, manuscript
save, synchronous reconstruction, and proposal review are rejected while it is
active, preventing a task from mixing manuscript revisions or racing reviewed
knowledge writes. AI and reconstruction jobs cannot start over one another.

## Checkpoint and recovery

After each chapter, normalized evidence segments are atomically written to
`cache/reconstruction-checkpoint.json`. The checkpoint records its aggregate
source revision and completed chapter IDs. A subsequent run resumes only when
that revision still matches; malformed or stale cache is deleted because cache
is never authoritative.

The checkpoint is retained on cancellation or failure and removed after a
proposal batch commits successfully. Proposal and knowledge semantics remain
those defined in Phase 9.

## Electron behavior

- The review drawer starts a background task rather than awaiting synchronous
  extraction.
- It shows task stage, percentage, and a cancellation action.
- Unsaved正文 disables task start, and review controls are disabled while a task
  is active.
- Completion reloads the proposal batch and reviewed counts through typed,
  named preload operations.

## Exit criteria

- [x] Reconstruction no longer blocks serialized RPC execution.
- [x] Progress and terminal state reach Electron as validated typed events.
- [x] Cancellation commits no partial proposal batch.
- [x] Completed chapters can resume from a same-revision cache checkpoint.
- [x] Stale or malformed checkpoints cannot contaminate a new run.
- [x] Conflicting project, manuscript, review, and AI mutations are blocked.
- [x] Unit, Sidecar, Node-to-Sidecar, and real Electron interaction tests cover
  the background path.

## Recommended Phase 11

Phase 11 can now add structured DSH extraction as another producer of the same
proposal schema. It should include schema validation, local fallback, privacy
review, token budgets, entity merge/split, alias editing, and relation editing;
neither local nor remote output may bypass the Phase 9 review boundary.
