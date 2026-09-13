# Phase 8: Electron-only schema-v2 manuscript vertical slice

This phase turns the Phase 7B foundation into the first complete user-facing
schema-v2 workflow. Electron is now the only visible project entry: users may
import manuscript text into a new v2 project or open an existing v2 project.
The legacy schema-v1 runtime remains internal for regression coverage and as a
read-only import source; it is not offered as a normal renderer action.

## Delivered

- `manuscript.scanImport` returns a bounded, renderer-safe preview and retains
  the authoritative plan only inside the Sidecar process.
- `project.createV2` accepts the plan digest, re-scans the original source, and
  refuses creation if the source changed or the plan expired.
- Source paths originate in Electron main-process native dialogs and never
  cross into renderer JavaScript.
- The import review dialog shows detected order, title, source file, encoding,
  excerpt, size, and warnings before the user chooses a destination.
- `project.openV2`, `manuscript.snapshot`, `manuscript.open`, and
  `manuscript.save` provide a typed v2 editing path through preload and RPC.
- Schema-v2 saves use opaque revisions, reject stale writes, update the
  manuscript index, and recover or roll back an interrupted chapter/index
  transaction from a local journal.
- The renderer top bar, welcome screen, library, and first-document bootstrap
  now use the v2 path. Legacy create/open remains compiled only for temporary
  compatibility tests.
- The hidden Electron capture creates a real v2 project by importing the
  golden schema-v1 fixture and verifies the v2 library and editor render.

## Persistent-data boundary

Only chapter正文 enters schema v2. Legacy outlines, notes, character cards,
worldbuilding, power systems, timelines, story memory, and relationship data
are neither copied nor treated as trusted evidence. Future extraction writes
proposals first; reviewed knowledge is created only by an explicit acceptance
operation.

The import source itself is never modified. The new project stores normalized
UTF-8 manuscript, source-relative labels, hashes, and import provenance, but no
absolute source path.

## Current intentional limits

- Import review is preview-and-accept. Per-chapter exclusion, renaming, and
  reordering are deferred until the plan-edit protocol can re-sign those
  choices server-side.
- Schema-v2 recent-project restore is not enabled yet; startup remains neutral
  instead of accidentally restoring a legacy project.
- Graph, AI generation, recycle bin, and story-knowledge editing stay disabled
  for v2 projects until the reconstruction pipeline and v2 mutation contracts
  exist.
- The schema-v1 APIs remain in the Sidecar and renderer implementation solely
  to preserve regression coverage during the transition.

## Exit criteria

- [x] Renderer-visible project creation and opening route to schema v2.
- [x] Import selection uses native dialogs and renderer code receives no source
  path.
- [x] Creation revalidates the source against a Sidecar-held plan.
- [x] Imported v1 projects contribute chapter正文 only.
- [x] V2 manuscript open, edit, autosave, explicit save, forced conflict
  resolution, and index-title refresh share the existing editor shell.
- [x] Interrupted two-file saves are either finalized or rolled back.
- [x] Python service/RPC tests, Node-to-Sidecar tests, production build, hidden
  Electron self-test, and v2 render capture pass.

## Recommended next phase

Phase 9 should establish the reconstruction pipeline before re-enabling graph
or AI features for v2:

1. define evidence spans, entity candidates, relationship proposals, review
   decisions, and invalidation rules as versioned v2 schemas;
2. add deterministic local segmentation and proposal jobs over manuscript
   revisions, with cancellation and resumable checkpoints;
3. build a review queue for accepting, merging, rejecting, and tracing proposed
   characters and facts;
4. project Graph View only from accepted v2 knowledge, while retaining evidence
   links back to manuscript passages;
5. only after those gates pass, add v2 recent-project restoration and remove the
   legacy renderer/RPC mutation surface from release builds.
