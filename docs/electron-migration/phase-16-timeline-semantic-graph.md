# Phase 16: reviewed timeline and semantic graph

Phase 16 extends reconstruction from static knowledge to evidence-backed story
events. Events use the same pending-review boundary as characters, relations,
world concepts, and character fields. Accepted events become an ordered
timeline and typed graph links; no event or link is inferred into durable
knowledge without explicit review.

## Event proposal contract

`event` is the fifth reconstruction proposal kind. Each event contains:

- a bounded human-readable `time_label`;
- a title and evidence-backed description;
- zero or more referenced character names;
- zero or more referenced world names;
- one or more source evidence IDs and producer provenance.

The conservative local extractor recognizes:

```text
[[事件:时间|标题|描述|人物甲、人物乙|世界观名称]]
```

Use `无` or `-` for an empty reference group. DSH output uses the strict
`events` collection with `character_names` and `world_names` arrays. Unknown
fields, duplicate references, unsent evidence IDs, excessive output, missing
characters, or missing world declarations reject the complete remote result and
trigger the existing safe local fallback.

## Review and dependency invariants

An event cannot be accepted unless every referenced character is accepted
already or in the same atomic review set. Each referenced world name must match
exactly one accepted world proposal; missing or ambiguous names fail before any
decision is written.

Only one differing event version may be accepted for a normalized
time-label/title pair. Confidence remains advisory. Events become stale and are
removed from timeline and graph projections when their manuscript batch is
invalidated.

## Timeline projection and curation

Accepted events are written to the existing `knowledge/timeline.json`. Stable
event IDs derive from the reviewed base time label and title. Timeline order is
deterministic from the earliest chapter/evidence position, while the displayed
time label remains author-facing text.

`knowledge/curation.json.event_overrides` supports audited event text edits,
hiding, and restoration. Editing does not change the stable event ID. Hidden
events remain in `hidden_events` with evidence and base references but leave the
active timeline and graph. Character merges are resolved into retained entity
IDs, and links to hidden worlds leave the active graph until that world is
restored.

## Semantic graph projection

Schema-v2 Graph View now exposes three node kinds:

- `character`;
- `world`;
- `event`.

It exposes three edge kinds:

- `relationship`: the existing directed character relation;
- `participation`: character → event, labelled `参与事件`;
- `setting`: event → world, labelled `发生于`.

All semantic edges reuse the accepted event evidence. Python owns stable IDs,
endpoints, labels, order, and evidence. Cytoscape layout, dragged coordinates,
selection, type filters, and the visual event lane remain transient frontend
state.

## Electron surface

The review drawer renders event candidates, dependency warnings, alternatives,
producer provenance, and source evidence. Its summary includes accepted event
count. The knowledge drawer provides event editing, hide/restore recovery, and
resolved character/world link labels.

Graph View uses separate shapes and colors for the three node kinds, a node-type
filter, semantic edge inspection, and a deterministic event sequence lane.
Selecting an event in the lane focuses the same graph node.

## Compatibility and quality

This remains project schema 2 and RPC protocol 1. `timeline.json` was already a
required schema-v2 collection; `hidden_events`, event DTO fields, graph node/edge
kinds, and `event_overrides` are additive. Older schema-v2 projects reconcile to
an empty event projection without importing legacy timeline or canon data.

Synthetic `corpus-v3` adds independent local and recorded-combined event
precision, recall, F1, and calibration gates while retaining every earlier
entity, relation, world, and character-field gate.

## Exit criteria

- [x] Local and strict DSH producers emit bounded event candidates with evidence.
- [x] Atomic review validates character and uniquely named world dependencies.
- [x] Conflicting event versions cannot both enter accepted knowledge.
- [x] Active and hidden events survive deterministic reconciliation with audited
  text edit/hide/restore operations.
- [x] Accepted events create typed character/event/world nodes and evidence-backed
  semantic links.
- [x] Electron supports event review, curation, node-type filtering, semantic
  inspection, and an ordered event lane.
- [x] Corpus-v3, Python tests, Node-to-Sidecar integration, production build, and
  real Electron capture cover the new workflow.

## Recommended Phase 17

Phase 17 implements explicit semantic-link curation, manual event ordering,
temporal/link diagnostics, and evidence navigation in
[`phase-17-semantic-link-curation.md`](phase-17-semantic-link-curation.md).
