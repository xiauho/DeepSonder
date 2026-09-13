# Phase 9: evidence-backed reconstruction and reviewed graph

Phase 9 establishes the first complete reconstruction loop for schema-v2
projects. It is deliberately review-first: deterministic local rules may create
candidate people and relationships, but no candidate becomes project knowledge
or graph truth without an explicit user decision.

## Data contract

- Manuscript sentences are segmented into evidence objects with a stable ID,
  chapter ID, opaque chapter revision, character offsets, line anchor, and an
  inspectable excerpt.
- A proposal batch records the aggregate manuscript revision and every chapter
  revision. Proposal IDs are batch-scoped; accepted entity IDs are stable hashes
  of normalized identity keys rather than display order or file paths.
- Candidate decisions are `pending`, `accepted`, or `rejected`. Batches are
  `pending`, `reviewed`, or `stale`.
- `proposals/<batch-id>.json` keeps the durable decision history.
  `knowledge/entities.json`, `knowledge/relations.json`, and
  `provenance/evidence.json` are deterministic reviewed projections rebuilt from
  non-stale accepted decisions.
- Graph layout remains frontend cache. Only the reviewed knowledge projection
  supplies v2 Graph View nodes and edges.

The evidence collection is a compatible schema-2 addition. Existing Phase 8
projects remain valid; opening one reconciles the rebuildable projection without
copying or trusting schema-v1 story data.

## Recognition boundary

The initial extractor runs locally and synchronously. It recognizes explicit
`[[人物:名称]]` and `[[关系:人物A|关系|人物B]]` annotations, conservative Chinese
dialogue/action patterns, and limited explicit natural-language relationships.
It does not call DSH or another model and cannot consume credentials or quota.

These rules provide a deterministic baseline and test oracle, not production
quality Chinese NER. The review UI exposes confidence and every evidence excerpt
so false positives can be rejected. Obvious pronouns, adverbs, and speech
modifiers are filtered before they reach the queue.

## Review and invalidation

- A relationship cannot be accepted until both endpoint people are accepted.
- Accept/reject decisions are atomic at the proposal-file boundary. Reviewed
  knowledge is reconciled after each decision and again when a v2 project opens,
  making an interrupted projection write recoverable.
- Saving a chapter marks every batch that depended on that chapter stale and
  immediately excludes all of its accepted results from knowledge and Graph
  View. This first version invalidates the whole batch rather than attempting an
  unsafe partial carry-forward.
- Evidence entries in the drawer navigate back to the corresponding chapter.

## Electron surface

- The v2-only “识别” rail action opens a review drawer.
- The drawer shows reviewed counts, pending count, stale-batch warnings,
  confidence, evidence, and explicit accept/reject controls.
- `reconstruction.snapshot`, `generate`, `batch`, and `review` cross named,
  validated preload operations; renderer JavaScript receives no arbitrary file
  access.
- V2 Graph View is enabled and projects only accepted knowledge. Accepted v2
  people are not presented as editable character cards yet.

## Exit criteria

- [x] Evidence anchors are tied to immutable manuscript revision tokens.
- [x] Candidate batches and review decisions are durable and inspectable.
- [x] Relationship acceptance enforces reviewed endpoint dependencies.
- [x] Rejected candidates never enter knowledge or Graph View.
- [x] Manuscript changes invalidate dependent reviewed knowledge.
- [x] Graph nodes and edges use stable identifiers and retain evidence links.
- [x] Electron exposes a typed review queue and evidence-to-chapter navigation.
- [x] Python, RPC, Node-to-Sidecar, production-build, and real Electron render
  gates cover the reconstruction loop.

## Recommended Phase 10 (background foundation completed)

1. Move large-manuscript extraction to cancellable background jobs with
   progress and resumable per-chapter checkpoints.
2. Add structured DSH extraction behind the same proposal contract, preserving
   the local deterministic extractor as fallback and test oracle.
3. Add entity merge/split, alias management, relation editing, and partial
   chapter-level invalidation.
4. Add editable v2 character cards and world/timeline proposals without making
   generated content authoritative before review.
5. Add v2 recent-project restore, release packaging gates, and then remove
   legacy mutation APIs from distributed builds.

The first item is implemented by Phase 10. Structured DSH extraction and
knowledge-curation operations move to Phase 11 so they can reuse the background
task and checkpoint contract rather than introducing a second execution path.
