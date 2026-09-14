# Novalist Electron desktop

This directory contains the Electron + React + TypeScript shell used by the root
source launcher and selected as the only entry point for future distributions.
Until a signed prerelease completes the clean-machine gate, existing public
artifacts remain unchanged.

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
npm run check:bundles
npm run benchmark:workspace
npm run benchmark:renderer
npm run self-test
npm start
npm run package:win
```

`npm start` builds and opens the desktop shell. `npm run self-test` starts a hidden
Electron window, verifies the context-isolated preload bridge and Sidecar ping,
then exits. `npm run capture` additionally writes an ignored visual-QA screenshot
to `dist/electron-preview.png`. A normal `npm run build` invokes
`npm run check:bundles` automatically; the standalone command can re-check an
existing `dist/renderer/assets` directory without rebuilding.

On Windows, the repository-root `run.bat` is the normal source entry. It creates
the Sidecar environment, verifies Node.js 24+, repairs an incomplete npm install
when safe, and then delegates to `npm start`. It never starts the legacy PySide6
shell.

The self-test also checks that an opened schema-v2 project exposes chapter
lifecycle, append/export, and reviewed-only AI controls. A live AI acceptance
run is intentionally separate and uses only disposable synthetic prose:

```powershell
python ..\scripts\verify_v2_ai_workflows.py --acknowledge-synthetic-remote --report ..\build\v2-ai-smoke.json
```

The acknowledgement is mandatory because the configured DSH may invoke a
remote provider and incur cost. The report contains no prompt or generated
novel text.

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

The latest source-bound post-cutover rehearsal and artifact hashes are recorded
in [`phase-22c-source-bound-rehearsal.md`](../docs/electron-migration/phase-22c-source-bound-rehearsal.md).

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

Phase 20A adds the schema-v2 daily chapter lifecycle: create after the current
chapter, revision-safe rename, stable-ID reordering, recoverable deletion, and
permanent trash removal. Phase 20B adds duplicate-guarded append import with
durable provenance and transaction recovery, plus ordered Markdown/plain-text
whole-book export through a native save dialog. Phase 20C enables review-first
schema-v2 expansion, continuation, consistency checks, and adopted chapter
memory using only manuscript content and reviewed v2 knowledge.

Phase 23A replaces CodeMirror's broad convenience setup with the extensions the
product actually uses, preserves the lazy editor boundary, and enforces a 480
KiB renderer-chunk budget during every production build. The measured bundle
results and release boundary are recorded in
[`phase-23a-renderer-bundle-budget.md`](../docs/electron-migration/phase-23a-renderer-bundle-budget.md).

Phase 23B adds an explicit large-workspace benchmark. It verifies a
million-character schema-v2 save/open round trip and a 500-node/2,000-edge graph
without adding that machine-sensitive timing workload to every unit-test run.
Graphs above 200 nodes use a complete, non-truncating fast grid layout; ordinary
graphs retain the `cose` layout. See
[`phase-23b-large-workspace-performance.md`](../docs/electron-migration/phase-23b-large-workspace-performance.md).
