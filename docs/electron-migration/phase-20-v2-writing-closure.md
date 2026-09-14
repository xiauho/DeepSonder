# Phase 20: schema-v2 writing closure

Phase 20 closes the daily-writing gaps that remain after the Electron packaging
and trust-chain work. It is split into reviewable slices so the persistent
manuscript model is stabilized before AI workflows depend on it.

## 20A — Chapter lifecycle

Implemented in the first slice:

- create a chapter after the current selection or at the end;
- rename a chapter with revision-conflict protection;
- reorder chapters without changing their stable IDs or evidence anchors;
- move chapters to a project-local manuscript trash;
- restore chapters at their former position, or permanently delete them;
- invalidate reviewed reconstruction output after content or structural edits;
- expose only typed chapter operations through preload and IPC.

Chapter IDs are stable identities, not positions. `sequence` carries display
order, so moving a chapter never rewrites proposal, evidence, or graph links.
New IDs are monotonic across live and deleted chapters to keep restoration free
from accidental reuse.

## 20B — Import and export closure

- [x] Append a rescanned manuscript plan to an open v2 project.
- [x] Preserve import provenance and reject duplicate sources explicitly.
- [x] Recover an interrupted append transaction without retaining partial
  chapters or provenance.
- [x] Export the ordered manuscript as Markdown or plain text through a native
  destination dialog.
- [x] Add ordered-output, transport, duplicate, and failure-rollback tests.

## 20C — v2 AI adaptation

- [x] Replace the legacy project-store dependency in writing AI tasks with a
  schema-v2 context adapter.
- [x] Keep expansion, continuation, consistency, and memory proposals
  review-first and revision-bound.
- [x] Never reintroduce imported legacy character/world/memory data as context.

The v2 adapter reuses the existing asynchronous task lifecycle and isolated DSH
transport, but selects its own inputs. It includes the current chapter, at most
four preceding manuscript chapters, materialized reviewed knowledge, and only
explicitly adopted chapter-memory records whose source revision is still
current. Proposal batches, rejected/hidden knowledge, legacy directories, old
outlines, and old story memory are outside the adapter's read set.

Expansion and continuation still produce non-durable review results. Adoption
rechecks the project root, stable chapter ID, opaque source revision, chapter
order, selected history revisions, reviewed knowledge, and current reviewed
memory before saving through `DocumentV2Service`. Adopted writing invalidates
affected reconstruction output. Memory is stored lazily in
`knowledge/chapter_memory.json` only after explicit review; stale records remain
auditable on disk but are excluded from later AI context.

Session task history is scoped to the active project. Switching projects clears
the renderer review state, and the Sidecar rejects result, cancellation, or
discard requests for a task owned by another project.

## 20D — Release-candidate closure

- [x] Source and packaged self-tests verify the v2 chapter lifecycle,
  append/export controls, AI entry, reviewed-only notice, and enabled review
  actions without invoking remote AI.
- [x] `scripts/verify_v2_ai_workflows.py` provides an explicit-consent live DSH
  gate using only a disposable synthetic manuscript. Its JSON report excludes
  prompts and generated prose.
- [x] A protected, manually dispatched `novalist-dsh` runner workflow executes
  the synthetic gate and uploads its redacted report.
- [x] Clean Windows 10/11 reports are machine-compared for version, release key,
  artifact digests, signatures, install/recovery results, and v2 UI gates.
- [ ] Run the live synthetic DSH gate in the maintained release environment.
- [ ] Produce and bind Windows 10/11 reports from one officially signed build.

The live DSH gate is deliberately separate from deterministic CI. It requires
`--acknowledge-synthetic-remote`, may incur provider cost, and never opens a user
project. Official signing credentials and clean-client machines remain external
release authority and cannot be replaced by local test fixtures.

## Phase exit criteria

- [x] Daily chapter creation, naming, ordering, deletion, and recovery work in
  an Electron v2 project.
- [x] Structure changes preserve stable chapter IDs and invalidate stale
  reconstruction knowledge.
- [x] Python service tests, typed Electron builds, and transport integration
  tests cover the lifecycle.
- [x] Append import and whole-book export pass service, typed build, transport,
  and Electron self-test validation.
- [ ] Append import and whole-book export pass signed packaged UI validation.
- [x] All enabled AI writing actions operate on schema v2 without legacy data
  dependencies.
- [x] Packaged self-test and report-binding gates cover the Phase 20 workflow
  surface without weakening read-only project-open validation.
- [ ] One live synthetic DSH report and one bound signed Windows 10/11 candidate
  report are attached to release review.
