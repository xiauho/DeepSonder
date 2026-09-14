# ADR-0001: Adopt Electron as the future desktop shell

- Status: Superseded by ADR-0002
- Date: 2026-09-11
- Decision owners: Novalist maintainers

## Context

Novalist is currently a PySide6 desktop application. The Python `core/` package
contains the project model, durable storage, AI workflows, context selection,
memory, consistency, export, and update validation without importing PySide6.
The `ui/` package contains the Qt widgets plus some application orchestration.

The product roadmap calls for a more refined interface and graph-oriented views,
beginning with an interactive character relationship view. Continuing to expand
the Qt widget layer would increase the eventual porting cost and make it harder
to reuse the mature browser visualization ecosystem.

## Decision

The following text records the original incremental migration decision. ADR-0002
supersedes its temporary PySide-entry and full-parity conditions: Electron is
now the only user entry, and legacy structured data is reconstructed from
imported manuscript evidence rather than copied into schema v2.

Electron will become the final desktop shell. The renderer will use React,
TypeScript, and Vite. The existing Python domain core will remain authoritative
and will run in a packaged local sidecar process.

The process boundary will be:

```text
React renderer
    -> typed, allowlisted preload API
    -> Electron main process
    -> versioned NDJSON request/response/event protocol over stdin/stdout
    -> Python application services
    -> existing core package, project files, and dsh
```

PySide6 remains the stable application entry point during the migration. The new
shell will be introduced as a separate preview and will replace PySide6 only
after packaged-build parity gates pass.

## Required architectural boundaries

- `core/` remains independent of Qt, Electron, Node.js, and React.
- A new Python application-service layer owns use-case orchestration that is
  currently embedded in Qt controllers.
- The Electron renderer never receives general filesystem, shell, Node.js, or
  raw IPC access.
- Electron main owns windows, native dialogs, external-link policy, single-instance
  behavior, sidecar lifecycle, and eventually desktop updating.
- Python remains the authority for project validation, migrations, atomic writes,
  DSH execution, AI proposal validation, and author-confirmed commits.
- Existing project and configuration locations remain compatible.

## Migration strategy

The migration uses incremental vertical slices rather than a line-by-line Qt
translation:

1. Record the baseline and parity gates.
2. Extract Qt-independent application services.
3. Define and test a versioned sidecar contract.
4. Deliver project open, document open, edit, conflict-safe save, and close as the
   first Electron slice.
5. Port local data-management features.
6. Port long-running AI workflows with progress and cancellation.
7. Add a read-only relationship graph projection and Graph View.
8. Stabilize packaging, signing, updating, and the PySide6-to-Electron cutover.

## Graph data decision

The first Graph View is a read-only projection of existing authoritative data,
including `memory/story_state.json`, character-card managed state, and accepted
memory evidence when available. It does not create a second relationship store.

Directed relationships are preserved: one character's view of another must not
be silently merged into a reciprocal edge. Graph editing is deferred until the
project model has explicit stable entity identifiers, rename semantics, relation
history, and conflict rules.

## Distribution decision

During preview releases, Electron will use full-package distribution and will not
replace the current stable PySide6 updater path. The stable Electron release will
prefer a signed per-user Windows installer with standard Electron updating. A
portable ZIP may remain available as a secondary, manually updated artifact.

The current updater's safety properties remain release requirements even if the
implementation changes:

- verify the selected version and platform;
- verify downloaded artifacts before installation;
- never overwrite project or user configuration data;
- exit the running application before replacement;
- run a packaged self-test before accepting an installed version;
- provide a recoverable failure path.

## Consequences

Positive consequences:

- the Python business core and current project format remain reusable;
- graph, timeline, filtering, and modern component ecosystems become available;
- UI state and domain state receive an explicit boundary;
- the renderer can be tested without invoking real DSH requests;
- a future Tauri shell would remain possible behind the same renderer-facing API.

Costs and risks:

- development adds Node.js and Electron to the existing Python toolchain;
- packaged builds contain both Electron/Chromium and a Python runtime;
- sidecar startup, crash recovery, cancellation, logging, and version mismatch
  become explicit engineering responsibilities;
- UI parity is a substantial rewrite even though the domain core is preserved;
- the existing portable updater cannot be assumed to work unchanged with the new
  package layout.

## Non-goals

- Rewriting the Python domain core in TypeScript or Rust.
- Replacing the project Markdown and JSON formats in the first Electron release.
- Enabling arbitrary web content or Node.js integration in the renderer.
- Making the first relationship graph directly editable.
- Removing PySide6 before packaged Electron parity has been demonstrated.
