# Phase 7A: editor and desktop preferences

Phase 7 begins the final parity-building pass without changing the production
PySide6 entry point, project schema, or configuration schema. This increment
focuses on the daily writing loop and project startup behavior.

## Implemented scope

- The plain renderer textarea is replaced by a lazy-loaded CodeMirror 6 feature.
  CodeMirror owns editor-local undo/redo, search/replace, selection, IME input,
  and Markdown syntax support; Python continues to own file access and saves.
- Source and read-only preview modes share the same in-memory text. Preview is
  rendered as escaped React nodes and cannot execute manuscript HTML or mutate
  project files.
- Timed autosave uses the existing normalized `auto_save` and
  `auto_save_interval` configuration values. Every save still supplies the
  opaque revision token, so external changes produce the existing explicit
  conflict flow.
- A narrow `PreferencesService` exposes only theme, UI/editor font size,
  autosave, interval, line numbers, and recent-project metadata. DSH commands,
  credentials, update metadata, and prompt-related settings do not cross into
  the renderer contract.
- Electron can create a current-schema project through a main-process directory
  picker and restore a valid last project on startup. Normal open/create actions
  update the existing recent-project keys; automated preview runs use an
  isolated configuration directory.
- The React settings drawer edits the allowlisted preference subset and applies
  light/dark appearance, font sizes, autosave timing, and line-number display.

## Ownership and safety

The renderer never receives a generic config object or arbitrary project-create
path. Electron main owns the native directory picker, validates renderer input,
translates camel-case DTO fields, and validates Sidecar results. The Sidecar
normalizes values through the existing schema-6 configuration code.

CodeMirror is a renderer implementation detail. Document identity, editable
path allowlists, atomic writes, and stale-revision rejection remain in
`DocumentService`. Changing preview mode neither saves nor clears dirty state.

## Verification

- Preferences service tests cover field allowlisting, normalization, recent
  projects, and last-project restoration.
- Sidecar application tests cover the frontend-safe DTO and a remembered
  open/close/restore round trip.
- Electron typecheck and Node-to-Sidecar tests cover the widened typed bridge.
- The hidden Electron capture verifies a real golden project reaches the
  CodeMirror DOM, switches to preview, and renders under a sandboxed preload.

## Remaining Phase 7 parity work

This increment does not complete the stable Electron cutover. The recent-project
menu, full settings/AI configuration, dashboard, memory/editor anchors, report
navigation, whole-book export, complete shortcut/E2E matrix, and packaged
Windows installer/update evidence remain separate parity slices. The source
preview intentionally supports the manuscript structures used by the current
project templates; full CommonMark/GFM parity should use a separately audited
sanitized renderer if required.
