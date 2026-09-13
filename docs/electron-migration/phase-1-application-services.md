# Phase 1: Qt-independent application services

Phase 1 creates the Python boundary that both the current PySide6 shell and the
future Electron sidecar can call. The production entry point remains `main.py`.

## Service boundary

### `ProjectService`

Owns project-level use cases:

- validate a project directory before activation;
- apply the existing transactional project migration chain;
- return one `OpenedProject` containing the project and its data facade;
- create a current-schema project without overwriting an existing directory;
- normalize, deduplicate, bound, and remember recent projects.

### `DocumentService`

Owns editable Markdown use cases:

- validate that a path is an allowed document inside the active project;
- open a document as a single immutable `DocumentSnapshot`;
- calculate an opaque, versioned revision token from file metadata and content;
- reject a stale save with `DocumentRevisionConflict`;
- use the existing atomic-write primitive for successful saves;
- create, import, and move supported project documents to the existing recycle
  bin through `ProjectDataStore`.

`DocumentMutation.changed_paths` tells the current UI which dependent views need
refreshing. It is also suitable for a future sidecar event payload.

## Preserved invariants

- No module under `application/` imports PySide6, Electron, Node.js, or React.
- Project Markdown and JSON formats are unchanged.
- Python remains authoritative for path validation, migration, revision checks,
  atomic writes, templates, identifiers, and recycle-bin behavior.
- Revision tokens are opaque to callers. The `v1` prefix versions the token
  representation independently from the future RPC protocol.
- A forced save is explicit; a missing or stale expected revision never silently
  overwrites the current disk version.
- The renderer will not receive a general filesystem API when these methods are
  exposed over RPC.

## Current adapter

The PySide6 `ProjectSession`, `ProjectLifecycleController`, and
`DocumentController` now delegate these use cases to the services. `Editor`
accepts a `DocumentSnapshot` and reports source text plus its loaded revision,
but remains responsible for Qt-only presentation and dirty-state behavior.

Legacy direct `Editor.open_file()` and `Editor.save()` wrappers remain available
for compatibility during the transition. Production document orchestration uses
`DocumentService` through `DocumentController`.

## Phase 2 handoff

The next phase should wrap this boundary in a versioned NDJSON sidecar protocol:

1. define handshake, request, response, error, and event envelopes;
2. expose allowlisted project and document methods only;
3. serialize `OpenedProject`, `DocumentSnapshot`, and mutation results into stable
   transport DTOs rather than Python object representations;
4. send protocol data only on stdout and diagnostics only on stderr;
5. add contract tests for malformed input, version mismatch, cancellation,
   sidecar shutdown, and golden-project parity.
