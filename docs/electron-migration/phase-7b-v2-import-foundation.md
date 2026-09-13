# Phase 7B: schema-v2 and manuscript-import foundation

This slice implements the first executable part of ADR-0002. At its completion
it deliberately did not switch the Electron project buttons; Phase 8 has since
completed that v2 editor/import-review vertical slice and activated the new
entry path.

## Delivered

- `core/project_v2_schema.py` defines schema 2, the manuscript/review/proposal/
  provenance/cache separation, review-first content policy, and read-only
  validation.
- `application/manuscript_import_service.py` creates deterministic, immutable
  import plans from legacy v1 projects or external Markdown/TXT sources.
- Legacy scans select only chapter files and extract only `## 正文`; planning,
  author notes, canon, memory, and all other old structured data are excluded.
- UTF-8/BOM and GB18030 decoding, natural file order, binary rejection, per-file
  and aggregate limits, consistent reads, stable chapter IDs, warnings, and
  SHA-256 provenance are enforced before project creation.
- `application/project_v2_service.py` creates a complete project in a sibling
  staging directory, validates it, and only then renames it into its final path.
  Import source paths are not persisted; only relative source names and hashes
  are recorded.
- Schema-v2 validation checks required collections, continuous chapter IDs,
  confined chapter paths, and manuscript content hashes. It never repairs a
  damaged project while opening it.

## Safety gate at completion

Schema v1 remained the active Electron editing runtime until Phase 8 exposed
native source selection, an import-plan preview, schema-v2 create, and v2
manuscript open/save together. Schema v2 carries a future-format manifest, so
attempting to open it through the legacy service fails without writes.

## Successor slice (completed in Phase 8)

1. Add `manuscript.scanImport`, `project.createV2`, `project.openV2`, and v2
   manuscript snapshot/open/save RPC operations.
2. Make source paths originate only from Electron main native dialogs.
3. Build the import review screen and re-scan the plan server-side before
   commit. Editable inclusion/title/order controls remain a later signed-plan
   extension.
4. Switch Electron create/open routing to v2 only after the complete vertical
   slice passes conflict, interruption, and packaged self-tests.
