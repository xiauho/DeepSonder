# Phase 5: review-first AI workflows

Phase 5 moves long-running AI execution behind the Sidecar without moving DSH,
prompt construction, response validation, or durable commits into Electron.
The PySide6 entry point remains available and the existing configuration schema
is unchanged.

## Delivered slice

- `AITaskService` runs one foreground task on a dedicated worker and supports
  expansion, continuation, consistency checking, memory proposals, and DSH
  connection testing.
- Task start returns immediately. `ai.taskUpdated` reports queued, running,
  cancel-requested, succeeded, failed, cancelled, applied, or discarded state.
- Existing cooperative cancellation events reach DSH and all task paths end in
  one terminal state.
- The Electron AI drawer starts and cancels tasks, displays progress and errors,
  and reviews validated writing, consistency, memory, or connection results.
- Redacted context reports are emitted separately and contain no prompt or
  manuscript body.

## Review and commit boundary

AI generation never writes chapter prose or accepted memory. Starting a project
task requires the revision returned with the open document and an explicit data
processing acknowledgement. Before apply/commit, Python verifies the active
project, source revision, and the complete task-specific context fingerprint.

Expansion replaces only the chapter body and preserves later custom Markdown
sections. Continuation appends to the current body through the same canonical
serializer. Memory proposals use the existing blocker, evidence, source-hash,
and atomic commit checks. Electron main presents a second native confirmation
before either writing operation.

Project open, create, and close are rejected while a task is active. Results are
kept only in bounded Sidecar session memory and can be explicitly discarded.

## Verification

Automated tests use injected fake executors and never call a real model. They
cover non-mutating generation, explicit application, stale-context rejection,
single-task exclusion, required notice acknowledgement, cooperative cancellation,
RPC result review, and document-changed events. Existing AI core tests continue
to validate prompt, protocol, correction, memory, and DSH transport behavior.

The phase remains a source-run Preview. Consistency repair, issue-to-editor
navigation, detailed context-selection controls, character-card sync, and full
settings/report parity remain later parity work.
