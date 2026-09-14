# Electron migration baseline

This directory records the decisions and parity gates for replacing the PySide6
shell with Electron without rewriting the Python domain core.

## Current phase

Phase 19 binds the protected release key inside packaged Electron and establishes
a signed prerelease plus clean Windows 10/11 install, backup, and recovery
acceptance workflow. Local mechanics pass; official signed-client evidence is
still required before stable cutover.

Phase 0 deliverables:

- [`ADR-0001-electron-migration.md`](ADR-0001-electron-migration.md): accepted
  architecture and migration constraints.
- [`feature-parity.md`](feature-parity.md): user-visible capability inventory and
  release gates.
- [`schema-baseline.md`](schema-baseline.md): application, project, cache, and
  update format versions that the Electron implementation must preserve.
- `tests/fixtures/electron_migration/golden_project`: deterministic, synthetic
  project used to compare the PySide6 and Electron implementations.
- `tests/test_electron_migration_fixture.py`: guard against accidental fixture or
  schema drift.

Phase 1 deliverables:

- [`phase-1-application-services.md`](phase-1-application-services.md): service
  responsibilities, invariants, and the boundary intended for the sidecar RPC.
- `application/project_service.py`: project validation, migration, creation, and
  recent-project normalization.
- `application/document_service.py`: validated document snapshots, opaque
  revision tokens, conflict-safe atomic saves, and local document mutations.
- The existing PySide6 project/session and document controllers now consume the
  application services, keeping the stable UI on the same execution path that
  the future sidecar will expose.
- Pure service tests plus controller regression tests cover the extracted
  behavior without introducing Electron or Node.js dependencies.

Phase 2 deliverables:

- [`rpc-v1.md`](rpc-v1.md): normative NDJSON envelope, method, lifecycle, error,
  cancellation, and security contract.
- `sidecar/protocol.py`: strict UTF-8 message validation and serialization.
- `sidecar/application.py`: allowlisted transport DTO adapter over Phase 1
  services.
- `sidecar/server.py`: concurrent request reader, serialized project operations,
  immediate queued-request cancellation, and protocol-only stdout writer.
- `python -m sidecar` and `sidecar_main.py`: source and packaging-friendly
  Sidecar entry points.
- Protocol, application-adapter, scheduler, subprocess, and golden-project
  contract tests.

Phase 3 deliverables:

- [`phase-3-electron-preview.md`](phase-3-electron-preview.md): implemented
  vertical slice, security boundary, verification, and intentional limits.
- `electron/`: pinned Electron, React, TypeScript, and Vite project with a lock
  file and isolated build output.
- Electron main owns Sidecar lifecycle, native dialogs, single-instance behavior,
  navigation policy, and renderer argument validation.
- A sandboxed, context-isolated preload exposes named operations rather than raw
  Electron IPC.
- The React Preview supports project open/close, Markdown selection/open/edit,
  revision-safe save, explicit overwrite confirmation, and unsaved-change guards.
- Node-to-Python golden-project integration tests and a hidden full Electron
  startup self-test are available.

Phase 4 deliverables:

- [`phase-4-local-data.md`](phase-4-local-data.md): project-library scope,
  ownership rules, destructive-action safety, and verification gates.
- `application/project_content_service.py`: grouped project snapshots and typed
  recycle-bin coordination over the existing data store.
- RPC v1 methods for chapter/canon/timeline creation, native-selected Markdown
  import, recoverable deletion, restore conflicts, permanent deletion, and power
  importance.
- A searchable React project library, creation dialog, native-confirmed deletion,
  and combined project recycle-bin interface.
- Python transaction tests and a real Node-to-Sidecar create-delete-restore
  integration test using only temporary project copies.

Phase 5 deliverables:

- [`phase-5-ai-workflows.md`](phase-5-ai-workflows.md): task lifecycle, privacy,
  review/commit boundaries, and intentional remaining scope.
- `application/ai_task_service.py`: framework-independent background execution,
  bounded session results, cancellation, stale-context guards, and explicit
  writing/memory adoption.
- RPC v1 AI task/status/result/apply methods plus asynchronous task and redacted
  context-report events.
- A React AI drawer for expansion, continuation, consistency, memory proposals,
  DSH connection tests, progress, cancellation, and result review.

Phase 6 deliverables:

- [`phase-6-relationship-graph.md`](phase-6-relationship-graph.md): projection
  precedence, read-only constraints, interaction scope, and remaining limits.
- `application/relationship_graph_service.py`: deterministic nodes, independent
  directed edges, alias resolution, unresolved-node warnings, accepted evidence,
  and a source fingerprint without project writes.
- `graph.snapshot` RPC plus strict Electron runtime validation and a named preload
  operation.
- A lazy-loaded Cytoscape.js feature with search, filtering, zoom, selection,
  inspectors, and guarded character-card navigation.

Phase 7A deliverables:

- [`phase-7-editor-preferences.md`](phase-7-editor-preferences.md): implemented
  writing-loop scope, ownership boundaries, verification, and remaining parity.
- A lazy-loaded CodeMirror Markdown editor with undo/redo, search/replace,
  configurable line numbers and font size, plus an escaped React preview.
- Revision-safe timed autosave using existing schema-6 preference keys.
- A frontend-safe preferences application service, typed RPC/preload operations,
  and React settings drawer.
- Native-location project creation and safe last-project restoration, with
  isolated config storage during desktop self-tests.

Phase 7B foundation deliverables:

- [`ADR-0002-electron-only-manuscript-reconstruction.md`](ADR-0002-electron-only-manuscript-reconstruction.md):
  accepted Electron-only entry, manuscript-only legacy import, and review-first
  reconstruction decision.
- [`phase-7b-v2-import-foundation.md`](phase-7b-v2-import-foundation.md): schema,
  import boundaries, safety gates, and next vertical slice.
- `core/project_v2_schema.py`: independent schema-2 contract and non-repairing
  validator.
- `application/manuscript_import_service.py`: deterministic, read-only v1 or
  external Markdown/TXT scan plans with encoding, size, ordering, and hash
  checks.
- `application/project_v2_service.py`: validated sibling-staging creation that
  persists normalized manuscript and provenance but no legacy story knowledge.

Phase 8 deliverables:

- [`phase-8-v2-manuscript-vertical-slice.md`](phase-8-v2-manuscript-vertical-slice.md):
  active entry routing, safety properties, verification, and Phase 9 boundary.
- Sidecar-held import plans with server-side source re-scan before v2 creation.
- Native Electron source/destination selection and a renderer-safe import review
  dialog that never receives absolute source paths.
- Revision-safe v2 manuscript snapshot/open/save with recoverable chapter/index
  transactions.
- A v2-first React library and editor path plus real Node/Sidecar and hidden
  Electron v2 integration coverage.

Phase 9 deliverables:

- [`phase-9-evidence-reconstruction.md`](phase-9-evidence-reconstruction.md):
  evidence, proposal, decision, invalidation, and reviewed-graph contract.
- `application/reconstruction_service.py`: deterministic segmentation,
  conservative local candidate extraction, durable review decisions, stable
  knowledge IDs, reconciliation, and manuscript-revision invalidation.
- Typed reconstruction RPC/preload operations and an Electron evidence-review
  drawer with accept/reject and chapter navigation.
- A schema-v2 Graph View backed only by accepted entities, relations, and
  evidence.

Phase 10 deliverables:

- [`phase-10-background-reconstruction.md`](phase-10-background-reconstruction.md):
  task lifecycle, concurrency gates, checkpoint safety, and next extraction
  boundary.
- `application/reconstruction_task_service.py`: one-worker background lifecycle,
  progress events, cancellation, status recovery, and graceful shutdown.
- Per-chapter, source-revision-bound reconstruction checkpoints in rebuildable
  project cache.
- Electron progress and cancellation UI backed by typed start/status/cancel
  preload operations.

Phase 11A deliverables:

- [`phase-11a-knowledge-curation.md`](phase-11a-knowledge-curation.md): durable
  override semantics, audit trail, recovery behavior, and limits.
- Persistent entity and relationship overrides in `knowledge/curation.json`,
  mirrored to `provenance/curation_log.json` for inspection.
- Typed Sidecar/preload operations for rename, aliases, merge/split, relation
  direction/label/delete, and proposal reopening.
- A v2-only Electron knowledge drawer whose changes immediately refresh Graph
  View while remaining subordinate to accepted manuscript evidence.

Phase 11B deliverables:

- [`phase-11b-structured-dsh-extraction.md`](phase-11b-structured-dsh-extraction.md):
  consent, privacy scope, schema validation, budgets, fallback, and review gates.
- `application/structured_extraction_service.py`: isolated DSH invocation,
  bounded evidence chunking, prompt-injection boundary, and all-or-nothing JSON
  validation.
- Extraction provenance on every proposal batch, including requested mode,
  effective producer, fallback state, chunk count, and redacted token estimate.
- An opt-in-per-run Electron control; local extraction remains the default and
  automated tests never invoke credentials or consume model quota.

Phase 12 deliverables:

- [`phase-12-reconstruction-quality.md`](phase-12-reconstruction-quality.md):
  corpus policy, metrics, baseline, diagnostics, CI gate, and interpretation.
- `tests/fixtures/reconstruction_quality/corpus-v1.json`: versioned synthetic
  cases with expected entities/relations and recorded enhanced candidates.
- `application/reconstruction_evaluation_service.py` and
  `scripts/evaluate_reconstruction.py`: strict offline evaluation and a
  machine-readable regression exit code.
- Per-proposal `local`/`dsh` provenance plus batch duplicate and relationship
  conflict counts surfaced in the Electron review drawer.

Phase 13 deliverables:

- [`phase-13-evidence-review-workbench.md`](phase-13-evidence-review-workbench.md):
  comparison, decision, audit, recovery, and stability contract.
- Pending/conflict/duplicate/reviewed filters, expanded conflicting evidence,
  confidence bands, and explicit checkbox selection in Electron.
- `reconstruction.reviewMany`: bounded full-set validation with relationship
  dependency checks, one batch update, and one knowledge rebuild.
- Single/batch review mode and timestamp DTOs with retained proposal reopening.

Phase 14 deliverables:

- [`phase-14-structured-knowledge-projection.md`](phase-14-structured-knowledge-projection.md):
  proposal vocabulary, review invariants, projection ownership, compatibility,
  and next curation boundary.
- Evidence-backed `world` and `character_field` candidates in local and strict
  DSH extraction, including character dependencies and conflicting-value gates.
- Accepted world/character-field knowledge plus stable generated Markdown cards
  isolated from untouched author-owned directories.
- Electron review/knowledge rendering and a corpus-v2 quality gate covering
  entities, relationships, world concepts, and character fields.

Phase 15 deliverables:

- [`phase-15-knowledge-curation-and-cards.md`](phase-15-knowledge-curation-and-cards.md):
  override semantics, audit/recovery behavior, card ownership, security boundary,
  and Phase 16 recommendation.
- Durable character-field and world edit/hide/restore overlays in the Phase 11A
  curation log, including separate active and hidden projections.
- Python-owned generated/author card resolution with atomic revision-safe author
  saves and no renderer-supplied filesystem paths.
- Inline Electron knowledge editors, hidden-world recovery, generated-card
  viewer, and author-card editor.

Phase 16 deliverables:

- [`phase-16-timeline-semantic-graph.md`](phase-16-timeline-semantic-graph.md):
  event proposal, dependency, timeline, curation, semantic graph, and
  compatibility contract.
- Local explicit and strict DSH event extraction with character/world dependency
  validation and conflicting-version gates.
- Auditable active/hidden event projections in the existing schema-v2 timeline.
- Typed character/world/event graph nodes, relationship/participation/setting
  edges, node filters, semantic inspectors, and an event sequence lane.
- `corpus-v3` quality gates for events without removing earlier subjects.

Phase 17 deliverables:

- [`phase-17-semantic-link-curation.md`](phase-17-semantic-link-curation.md):
  link ownership, ordering, diagnostic, navigation, and compatibility contract.
- Audited event participant/world overrides and full-list manual ordering using
  stable knowledge IDs.
- Derived temporal and inactive-link diagnostics with manuscript evidence
  navigation in Knowledge and Graph views.

Phase 18 deliverables:

- [`phase-18-packaged-cutover-rehearsal.md`](phase-18-packaged-cutover-rehearsal.md):
  package layout, clean-profile, recovery, signing, and cutover contract.
- PyInstaller Sidecar plus `electron-builder` NSIS and portable ZIP packaging.
- Read-only schema-v2 opening checks across unpacked, recovered, and installed
  packaged applications.
- Schema-3 Electron-only release metadata with SHA-256 and Ed25519 validation.

Phase 19 deliverables:

- [`phase-19-signed-prerelease-validation.md`](phase-19-signed-prerelease-validation.md):
  embedded trust, protected environment, clean-client matrix, and release gates.
- [`../ELECTRON_TRANSITION_GUIDE.md`](../ELECTRON_TRANSITION_GUIDE.md):
  manuscript-only migration, backup, recovery, and rollback instructions.
- An offline, Python-free full-cycle candidate validator and machine-readable
  report.

## Phase 0 exit criteria

- [x] The current PySide6 capability surface is inventoried.
- [x] Every capability has a proposed target owner and parity gate.
- [x] Persistent schema versions are recorded independently from the app version.
- [x] A privacy-safe current-schema project fixture exists.
- [x] The fixture is validated by an automated test.
- [x] Electron is recorded as the target shell while PySide6 remains the stable
  entry point during migration.

## Phase 1 exit criteria

- [x] Project lifecycle rules are callable without importing PySide6.
- [x] Editable project paths are validated at the application boundary.
- [x] Document open returns content and one opaque revision token together.
- [x] Stale saves fail unless the caller explicitly requests a forced save.
- [x] Successful document writes remain atomic.
- [x] Existing PySide6 controllers use the extracted services.
- [x] The full Python test suite passes with the PySide6 entry point unchanged.

## Phase 2 exit criteria

- [x] RPC protocol version 1 is independent from app and persistent schemas.
- [x] Startup handshake reports app/build versions, supported methods, and schema
  ranges.
- [x] Request, response, error, and event envelopes are strict NDJSON.
- [x] Only allowlisted project/document methods are callable.
- [x] Golden-project open and document read pass through the real stdio transport.
- [x] Malformed input and version mismatch return structured errors without
  crashing the Sidecar.
- [x] Queued requests can be cancelled; running synchronous mutations are not
  falsely reported as cancelled.
- [x] EOF and `system.shutdown` terminate cleanly.
- [x] stdout contains protocol messages only; diagnostics are reserved for
  stderr.

## Phase 3 exit criteria

- [x] Electron, React, TypeScript, and Vite build from a reproducible lock file.
- [x] Electron main validates the RPC v1 handshake before opening the window.
- [x] Renderer Node integration is disabled; context isolation and sandboxing are
  enabled.
- [x] preload exposes only typed, named Novalist operations.
- [x] Project and document selection use native main-process dialogs.
- [x] The first document can be opened, edited, conflict-checked, and atomically
  saved through the real Python Sidecar.
- [x] Dirty documents are guarded during switching, close, and app exit.
- [x] Typecheck, production renderer build, Node-to-Python integration tests, and
  hidden Electron startup self-test pass.
- [x] `main.py` remains the stable production entry point.

## Phase 4 exit criteria

- [x] The renderer navigation comes from a Python-owned project snapshot rather
  than hard-coded paths.
- [x] Chapters, characters, world entries, power systems, and the singleton
  timeline can be created through typed application operations.
- [x] Markdown chapter import paths originate from Electron's native dialog.
- [x] Deletable story documents use the existing recoverable project trash.
- [x] Restore conflicts require an explicit error-or-rename decision.
- [x] Core rules stay protected and power importance remains registry-backed.
- [x] Permanent deletion requires a separate main-process confirmation.
- [x] Python and Node-to-Sidecar mutation tests, typecheck, production build, and
  hidden Electron startup verification pass.
- [x] The PySide6 application and project format remain unchanged.

## Phase 5 exit criteria

- [x] Long-running DSH work executes outside both the renderer and serialized
  project-request worker.
- [x] Only one foreground AI task can run and project switching is blocked until
  it reaches a terminal state.
- [x] Task progress, cancellation, failure, success, apply, and discard states
  cross typed events without exposing prompt bodies.
- [x] Expansion, continuation, consistency, memory proposal, and connection-test
  workflows reuse the existing Python AI core.
- [x] No generated writing or memory is durable before an explicit second action.
- [x] Apply/commit revalidates project identity, source revision, and task context.
- [x] Automated tests use fake executors and never consume model credentials or
  quota.
- [x] Typecheck, production renderer build, Node-to-Sidecar tests, Python
  regression tests, and hidden Electron startup verification pass.

## Phase 6 exit criteria

- [x] Graph data is projected by a UI-independent Python service.
- [x] Opening, filtering, laying out, and dragging the graph cannot modify project
  or configuration files.
- [x] Current story state wins over card fallback relations, accepted memory adds
  evidence only, and reverse edges are never inferred.
- [x] Missing character cards appear as unresolved nodes with explicit warnings.
- [x] Renderer graph data crosses one typed read-only RPC and named preload API.
- [x] Search, exact relationship filtering, pan, zoom, fit, selection, and detail
  inspection work in the real Graph View.
- [x] Resolved nodes can open their character cards through the existing dirty
  document guard.
- [x] The graph engine is lazy-loaded outside the initial renderer bundle.
- [x] Python projection/RPC tests, Node-to-Sidecar tests, typecheck, production
  build, and canvas-aware Electron capture verification pass.

## Phase 7A exit criteria

- [x] CodeMirror replaces the temporary textarea and remains outside the initial
  renderer bundle.
- [x] Undo/redo and search/replace use CodeMirror's standard command set.
- [x] Read-only preview renders escaped React content and never triggers
  autosave while active.
- [x] Timed autosave reuses normalized schema-6 settings and revision-safe saves.
- [x] Project create/open actions remember the project and a valid last project
  restores on startup.
- [x] Renderer preference access is allowlisted, typed, normalized, and excludes
  sensitive application configuration.
- [x] Typecheck, Node/Sidecar integration, complete Python regression, hidden
  Electron startup, and preview capture verification pass.

## Phase 7B foundation exit criteria

- [x] Electron-only entry and manuscript-only legacy import are accepted in a
  superseding ADR.
- [x] Schema v1 and schema v2 have independent authorities and cannot be
  mistaken for one another.
- [x] A v1 import plan excludes outline, notes, canon, memory, and relationship
  data and leaves its source byte-for-byte unchanged.
- [x] External Markdown/TXT scans have deterministic order, bounded size,
  supported Chinese decoding, stable IDs, and content hashes.
- [x] Schema-v2 creation is sibling-staged, fully validated, and records no
  absolute import-source path.
- [x] A v1 runtime rejects schema v2 as a future project without modifying it.

## Phase 8 exit criteria

- [x] The renderer exposes only manuscript import and schema-v2 open as normal
  project-entry actions.
- [x] Import plans stay authoritative in the Sidecar and are re-scanned before
  project creation.
- [x] Native paths remain behind the main/preload security boundary.
- [x] V2 chapters can be listed, opened, edited, conflict-checked, and saved
  without using legacy project services.
- [x] Interrupted v2 chapter/index saves recover deterministically.
- [x] The packaged Electron path renders a real imported v2 project.

## Phase 9 exit criteria

- [x] Candidate evidence is anchored to chapter revisions and inspectable text.
- [x] No candidate becomes reviewed knowledge without an explicit acceptance.
- [x] Relations require reviewed endpoint entities and use stable IDs.
- [x] Saving正文 invalidates dependent batches and graph projections.
- [x] The Electron review queue supports evidence inspection, chapter navigation,
  acceptance, and rejection.
- [x] V2 Graph View reads only reviewed knowledge and evidence.

## Phase 10 exit criteria

- [x] Reconstruction runs outside the serialized Sidecar request worker.
- [x] Progress, cancellation, completion, and failure have typed desktop state.
- [x] Cancellation cannot create a partial proposal batch.
- [x] Same-revision runs resume from per-chapter cache checkpoints.
- [x] Conflicting saves, reviews, project switches, and AI starts are blocked.

## Phase 11A exit criteria

- [x] Accepted knowledge can be renamed, aliased, merged/unmerged, and have
  relationships corrected or hidden without deleting proposal evidence.
- [x] Curation operations are durable, auditable, and immediately reflected in
  knowledge and Graph View.
- [x] Reviewed proposals can be reopened without bypassing manuscript revision
  invalidation.

## Phase 11B exit criteria

- [x] DSH extraction is opt-in per run and sends only bounded manuscript
  evidence segments after explicit consent.
- [x] Invalid, failed, or cancelled remote output cannot create partial remote
  proposals and safely falls back to local extraction where applicable.
- [x] Producer provenance and redacted extraction diagnostics cross typed RPC.

## Phase 12 exit criteria

- [x] A synthetic privacy-safe corpus measures local and recorded-combined
  entity/relation precision, recall, F1, calibration, duplicates, and conflicts.
- [x] CI rejects quality regression below versioned thresholds without live DSH.

## Phase 13 exit criteria

- [x] Reviewers can filter pending, conflict, duplicate, reviewed, and all
  proposals while retaining evidence and producer context.
- [x] Batch review is explicitly selected, confirmed, bounded, and fully
  validated before any selected proposal changes.
- [x] Relationship endpoint dependencies can be satisfied in the same batch.
- [x] Confidence is advisory only; review mode/timestamp and reopen semantics
  remain visible and durable.

## Phase 14 exit criteria

- [x] World concepts and bounded character fields use the existing evidence,
  review, provenance, and manuscript-invalidation lifecycle.
- [x] Python rejects missing character dependencies and competing accepted
  values before changing a decision set.
- [x] Only accepted knowledge generates stable-ID Markdown projections; author
  directories are never rewritten by reconstruction.
- [x] Electron displays the new proposal and knowledge types without widening
  renderer filesystem authority.
- [x] Corpus-v2 retains existing entity/relation gates and adds independent
  world/character-field metrics and gates.

## Phase 15 exit criteria

- [x] Character fields and world entries can be edited, hidden, and restored as
  auditable overlays over reviewed evidence.
- [x] Hidden values survive reconciliation without entering active generated
  projections.
- [x] Generated cards are read-only and author cards survive reconstruction with
  revision-conflict protection.
- [x] Card paths are derived and validated in Python; Electron exposes only
  typed owner/mode operations.
- [x] The Electron knowledge drawer provides editing, recovery, and card viewing
  without becoming a second source of truth.

## Phase 16 exit criteria

- [x] Events require explicit review and retain manuscript evidence and producer
  provenance.
- [x] Event acceptance validates referenced characters and uniquely named world
  concepts atomically.
- [x] Timeline edits, hiding, and restoration are auditable overlays.
- [x] Graph semantics are Python-owned while layout and the event lane remain
  frontend-only state.
- [x] Corpus-v3 gates event quality alongside all earlier proposal subjects.

## Phase 17 exit criteria

- [x] Event semantic links can be corrected using active stable IDs.
- [x] Manual order survives reconstruction without changing source evidence
  order.
- [x] Temporal and inactive-link diagnostics rebuild deterministically.
- [x] Diagnostic and graph evidence opens the owning manuscript chapter.
- [x] The additions remain compatible with older schema-v2 projects and RPC v1.

## Phase 18 exit criteria

- [x] Packaged Electron launches with its embedded, version-matched Sidecar.
- [x] A newly created schema-v2 project opens without compatibility writes.
- [x] Unpacked, portable recovery, and NSIS install/uninstall rehearsals pass.
- [x] Both release artifacts are bound to a schema-3 manifest by SHA-256.
- [x] Tagged CI builds require Ed25519 metadata and Authenticode signatures.
- [x] Tagged packaging exposes Electron as the only application entry.

## Phase 19 exit criteria

- [x] Packaged production builds pin and validate the release public key.
- [x] A clean-client validator covers install, zero-write open, backup restore,
  portable recovery, and uninstall.
- [x] Protected Windows 10/11 workflow definitions are present.
- [ ] Official metadata/Authenticode credentials are provisioned.
- [ ] One signed packaging run and both clean-client reports pass.

## Phase 20 exit criteria

- [x] Schema-v2 chapters can be created, renamed, reordered, deleted, restored,
  and permanently removed without changing stable chapter IDs.
- [x] Chapter structure changes invalidate stale reconstruction output and
  remain behind typed IPC operations.
- [x] Open projects support provenance-preserving, duplicate-guarded append
  import and ordered Markdown/plain-text whole-book export.
- [x] Review-first AI writing, consistency, and memory actions use schema-v2
  manuscript and reviewed knowledge instead of the legacy project store.

See [Phase 20](phase-20-v2-writing-closure.md) for the slice plan and current
status.

Phase 20D adds packaged checks for the complete v2 workflow surface, an
explicit-consent synthetic live-DSH verifier, and an automated gate that binds
the Windows 10/11 clean-client reports to one exact signed candidate. Real DSH,
signing credentials, and clean-client runs remain release-environment gates.
The live verifier is also available as the protected, manually dispatched
`validate-v2-ai-dsh.yml` workflow on a `novalist-dsh` Windows runner.

## Working rules for later phases

1. Do not add Electron-specific imports to `core/`.
2. Do not change a persistent project format merely to make rendering easier.
3. Extract UI-independent application services before porting a feature.
4. Keep the current runtime operational only until the schema-v2 Electron
   vertical slice and packaged recovery gates pass; PySide6 is not a distributed
   user entry in the target release.
5. Treat stdout from the future Python sidecar as protocol-only; diagnostics go
   to stderr.
6. Keep app version, RPC protocol version, project schema version, and config
   schema version independent.
7. Never copy v1 structured story data into v2 reviewed knowledge; reconstruct
   it from manuscript evidence and explicit user review.
