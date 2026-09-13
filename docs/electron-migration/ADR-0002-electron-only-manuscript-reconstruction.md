# ADR-0002: Electron-only entry and manuscript reconstruction projects

- Status: Accepted
- Date: 2026-09-12
- Supersedes: ADR-0001 migration/cutover and project-compatibility decisions
- Decision owners: Novalist maintainers

## Context

The initial Electron plan treated PySide6 feature parity and lossless reuse of
all schema-v1 story data as prerequisites for cutover. The product direction has
changed: Electron will be the only distributed user entry, and old projects are
accepted only as manuscript sources. Character cards, worldbuilding, power
systems, timelines, relationships, and story memory must be recognized again
from the manuscript and explicitly reviewed before becoming durable knowledge.

The existing Electron security boundary, Python Sidecar, CodeMirror editor, AI
task lifecycle, and graph renderer remain useful. Pixel-level Qt parity and
automatic migration of v1 structured story data no longer provide product value.

## Decision

1. Electron is the sole target user entry. Python remains a packaged, headless
   Sidecar and is not a separately launched product UI.
2. Schema v1 is frozen as a read-only import source. It is never upgraded in
   place to schema v2.
3. Legacy import reads only `outline/chapters/*.md` and only the chapter `正文`
   section when present. It does not read or copy v1 canon, outline, planning,
   author-note, memory, relationship, or registry data.
4. External manuscript import initially accepts Markdown and text. Import is a
   previewable plan followed by creation of a separate schema-v2 project.
5. Schema v2 separates manuscript, reviewed knowledge, pending proposals,
   provenance, and rebuildable cache. Manuscript is the primary source. Model
   output is never reviewed knowledge until the user accepts it.
6. Entities and relationships will use stable identifiers and evidence anchors.
   Display names are not identifiers. Graph layout is non-authoritative cache.
7. The current v1 runtime stays available only as an implementation-time safety
   net. It will not ship as a user entry after Electron packaging gates pass.

## Process boundary

```text
Native import selection
    -> read-only manuscript scan
    -> deterministic chapter plan and hashes
    -> user review/ordering
    -> new schema-v2 project transaction
    -> evidence segmentation and AI extraction proposals
    -> entity resolution and conflict review
    -> explicit commit to reviewed knowledge
    -> graph, timeline, cards, and reports
```

Electron main owns native dialogs and Sidecar lifecycle. Renderer access remains
typed and allowlisted. Python owns source validation, decoding, import limits,
hashes, transactional writes, proposal validation, and commits.

## Schema compatibility

Schema v2 writes both root metadata and `.novalist/project.json` with
`schema_version: 2`. A v1 runtime therefore rejects it as a future format before
any migration can occur. The v2 creator uses a sibling staging directory and
renames it into place only after complete validation.

The first schema-v2 implementation uses inspectable Markdown and JSON. SQLite
may later serve as a rebuildable index, but is not the initial source of truth.

## Consequences

Positive:

- the new UI and data model can optimize for evidence-backed reconstruction;
- stale or low-quality legacy structured data cannot silently contaminate the
  new knowledge base;
- manuscript content remains portable and reviewable;
- entity identity, provenance, and graph semantics can be designed correctly.

Costs and risks:

- users must review regenerated characters and settings;
- extraction, disambiguation, invalidation, and re-review become core product
  workflows rather than optional enhancements;
- import quality and evidence navigation now gate the Electron release;
- schema-v2 editing and packaging must be complete before the v1 UI is removed.

## Non-goals for the foundation slice

- Switching the current Electron create/open buttons to schema v2 before the v2
  editor and import review UI exist.
- Copying old character cards or worldbuilding as trusted reference material.
- Running AI during source scanning.
- Mutating, repairing, or adding files to an import source.
