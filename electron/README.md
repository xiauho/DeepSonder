# Novalist Electron desktop

This directory contains the Electron + React + TypeScript shell selected as the
only entry point for future distributions. Until a signed prerelease completes
the Phase 19 clean-machine gate, the current public release remains unchanged.

## Requirements

- Node.js 24 or newer
- the repository Python environment, or another Python executable capable of
  running `python -m sidecar`

The source launcher uses `<repository>/.venv/Scripts/python.exe` when available,
then falls back to `python`. Set `NOVALIST_PYTHON` to an explicit executable when
needed.

## Commands

```powershell
npm install
npm run typecheck
npm test
npm run self-test
npm start
npm run package:win
```

`npm start` builds and opens the desktop shell. `npm run self-test` starts a hidden
Electron window, verifies the context-isolated preload bridge and Sidecar ping,
then exits. `npm run capture` additionally writes an ignored visual-QA screenshot
to `dist/electron-preview.png`.

## Security model

- renderer `nodeIntegration` is disabled;
- context isolation, sandboxing, and web security are enabled;
- the content security policy denies network connections and remote content;
- new windows and unexpected navigation are denied;
- preload exposes named product operations only, never raw `ipcRenderer`;
- Electron main spawns the Python Sidecar directly with `shell: false`;
- raw Sidecar messages are validated and translated before renderer delivery;
- the Sidecar remains authoritative for project migration, document paths,
  revisions, and atomic writes.

For a complete Windows release rehearsal, including the embedded Sidecar and
portable recovery checks, run from the repository root:

```powershell
.\scripts\build_electron_windows.ps1 -PythonExecutable .\.venv\Scripts\python.exe
```

Unsigned output is explicitly local-only. Tagged CI packaging additionally
requires Ed25519 release-metadata keys and a Windows Authenticode certificate.

## Current slice

The Phase 18 candidate supports:

- Sidecar startup, handshake, health check, and graceful shutdown;
- native project-directory and Markdown-file dialogs;
- current-schema and legacy-project open through `ProjectService`;
- open/edit/save for allowed project Markdown documents;
- stale-revision warning with explicit forced-save confirmation;
- dirty-state guards for document/project switching and window close;
- searchable project data creation, import, recoverable deletion, and trash;
- review-first AI expansion, continuation, consistency, and memory workflows;
- a lazy-loaded, read-only directed relationship graph with filters, warnings,
  accepted evidence, and guarded character-card navigation.
- a lazy-loaded CodeMirror Markdown editor with undo/redo and search/replace;
- source/escaped-preview switching and revision-safe timed autosave;
- native-location project creation plus safe last-project restoration;
- a frontend-safe settings drawer for theme, font size, autosave, and line
  numbers without exposing sensitive application configuration.

Phase 19 embeds the protected release public key in production packages and
provides a clean Windows 10/11 candidate-validation matrix. Provisioning the
official keys/runners and collecting both signed reports remain release-owner
actions; automatic updating stays disabled.
