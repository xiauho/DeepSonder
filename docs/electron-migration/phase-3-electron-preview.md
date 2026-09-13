# Phase 3: Electron project/document Preview

Phase 3 establishes the first runnable Electron vertical slice while leaving the
PySide6 release path intact.

## Architecture

```text
React renderer
  -> frozen named API in sandboxed context-isolated preload
  -> allowlisted ipcMain handlers and native dialogs
  -> validated SidecarClient
  -> RPC v1 over direct child-process stdio
  -> Python Sidecar and Phase 1 application services
```

The renderer cannot invoke arbitrary IPC channels. The main process validates
every argument again and converts Sidecar failures to a small public error DTO.
Raw transport events are translated through an event allowlist before reaching
the renderer.

## Implemented user flow

1. Electron main starts the Sidecar and validates its handshake before creating
   the window.
2. The user selects a project with a native directory dialog.
3. Python validates and, when required, transactionally migrates the project.
4. The editor opens an allowlisted Markdown path as content plus an opaque
   revision.
5. The renderer tracks dirty state; Python performs conflict-safe atomic saves.
6. A stale revision shows a warning and requires an explicit overwrite action.
7. Document switching, project switching, project close, and window close guard
   unsaved changes.
8. Window close requests a graceful Sidecar shutdown with a bounded forced-exit
   fallback.

## Verification

- TypeScript strict typechecking covers main, preload, shared contracts, and
  renderer code.
- Vite produces a CSP-compatible local renderer build.
- Node integration tests use the real Python Sidecar and golden project.
- The hidden Electron self-test verifies window load, isolated preload API,
  handshake, ping, and clean process shutdown.
- Python tests remain the authority for project/data compatibility.

## Intentional limits

This is a source-run Preview, not a distributable Electron release. The packaged
Sidecar location is reserved in main-process code, but Phase 3 does not add a
second release artifact, installer, signing, or updater path.

The Graph View route is a presentation placeholder only. It does not infer,
persist, or edit relationships. A later Python projection service remains the
required data source.
