# Phase 6: read-only relationship graph

Phase 6 replaces the Graph View placeholder with a deterministic projection of
existing Novalist character data. It does not introduce a relationship database,
change project schema 1, or allow graph edits.

## Ownership and precedence

`RelationshipGraphService` is the only graph-data builder. It reads current
`memory/story_state.json`, character cards, chapter summaries, and verified
`memory/accepted_chapter_memory.json` records without writing files or caches.

For a directed character pair:

1. current `story_state.characters.*.relations` wins;
2. the character card's `关键关系` section fills a missing pair;
3. verified accepted-memory relationship facts add evidence only.

Names and card aliases resolve to the same node. A referenced person without a
card remains visible as an unresolved node with a warning. The reverse edge is
never inferred.

## Delivered slice

- deterministic node and edge IDs plus a `graph-v1` source fingerprint;
- typed graph nodes, directed edges, evidence, warnings, and relation labels;
- allowlisted `graph.snapshot` RPC and a narrower preload operation;
- a lazy-loaded Cytoscape.js feature module with pan, zoom, fit, drag, search,
  exact relation filtering, selection, and read-only status;
- node and edge inspectors for current state, direction, data source, and
  verified accepted evidence;
- guarded navigation from a resolved graph node to its character card;
- automatic refresh after project-content events and fresh loading whenever the
  Graph View is reopened.

The Cytoscape bundle is a separate lazy chunk, so opening the writing workspace
does not load the graph engine.

## Safety and intentional limits

Graph layout lives only in renderer memory. Dragging a node cannot modify a card,
story state, memory, or project metadata. There is no graph mutation RPC.

Exact evidence-to-editor anchor navigation remains Phase 7 editor work because
it depends on the future range-aware editor. Stable entity IDs, rename history,
relationship history, conflict rules, and direct graph editing remain deferred.

## Verification

The golden project projects three nodes and three independent directed edges,
including one unresolved referenced character. Tests verify deterministic
output, no source-file writes, story-state precedence, alias resolution,
accepted-memory evidence, revision changes, typed RPC output, and real
Node-to-Sidecar transport. Electron capture self-test waits for the real graph
canvas before accepting the visual artifact.
