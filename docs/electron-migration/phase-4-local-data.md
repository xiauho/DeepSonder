# Phase 4: local project-data management

Phase 4 turns the Phase 3 editor shell into a usable local project library while
keeping the PySide6 application available. It deliberately reuses the existing
Markdown/JSON format and `ProjectDataStore`; no project migration or second
database is introduced.

## Delivered slice

- A Python `ProjectContentService` builds one grouped navigation snapshot for
  chapters, outlines, writing settings, characters, world entries, power systems,
  and the optional timeline.
- RPC v1 exposes explicit create, import, delete, restore, permanent-delete, and
  power-importance operations. Each mutation returns a fresh snapshot.
- React renders the snapshot as a searchable grouped library and can create all
  local author-document types, import Markdown chapters, edit them, and manage a
  combined project recycle bin.
- Core power rules remain protected. Power importance remains in the existing
  registry and round-trips through trash.
- Same-ID restore conflicts stop with `ID_CONFLICT`; a rename restore is a
  separate user decision.

## Ownership and safety

Python owns project paths, IDs, templates, ordering, atomic writes, registry
normalization, and recycle-bin transactions. Electron main owns native file
selection and destructive confirmation. The renderer receives typed snapshots
and named preload methods only; it has no Node.js, shell, or generic filesystem
capability.

Deleting an active dirty document first passes the unsaved-change guard. Normal
deletion is recoverable and permanent deletion has a second native confirmation.
Import source paths are created by Electron's native picker rather than supplied
by renderer input.

## Verification gates

- Service and RPC tests cover grouped snapshots, protected metadata, create and
  delete mutations, typed trash validation, restore, rename-on-conflict, and
  permanent deletion.
- Node integration tests spawn the real Sidecar and execute the full
  create-delete-restore round trip against a temporary project copy.
- TypeScript type checking and production renderer build must pass.
- The hidden Electron self-test loads the isolated preload bridge; preview capture
  opens the read-only golden project to exercise the real library UI.

Phase 4 is still a source-run Preview. Packaging, signing, updater replacement,
AI workflows, reports, settings parity, and the real graph projection remain
later phases.
