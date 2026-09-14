# Phase 22A: source-entry cutover

Phase 22A makes Electron the ordinary source-workspace entry point. It does not
remove the legacy Python UI modules because they remain useful for maintainer
rollback and regression comparison, but no user-facing launcher invokes them.

## Root launcher

`run.bat` now:

1. creates or reuses the repository `.venv` for the Python Sidecar;
2. requires Python 3.10+ and Node.js 24+;
3. checks the locked Electron toolchain before launching;
4. runs `npm ci` only when TypeScript, Vite, or Electron is missing;
5. refuses dependency repair while this repository's Electron executable still
   holds files under `node_modules`;
6. exports the selected Python executable through `NOVALIST_PYTHON`;
7. builds and starts the Electron shell through `npm start`.

The launcher intentionally does not install PySide6. Runtime distribution still
embeds the Python Sidecar, while source mode only needs a Python interpreter able
to execute `python -m sidecar`.

`NOVALIST_CHECK_ONLY=1` performs prerequisite inspection without installing npm
packages or opening a window. Missing Electron dependencies are reported as a
state that a normal launch will repair. `NOVALIST_SELF_TEST=1` follows the full
launcher path but uses Electron's hidden self-test instead of leaving a desktop
window open, so the entry point can be exercised in automation.

## Authority boundary

- `run.bat` and future distributed `Novalist.exe` are Electron entry points.
- `main.py` is retained only as internal legacy rollback source; it is not part
  of the Electron distribution and is no longer documented as a launch command.
- An unsigned local package remains a rehearsal artifact, not an approved public
  release.

## Exit criteria

- [x] The root source launcher never invokes `main.py`.
- [x] Python is selected only for the restricted Sidecar.
- [x] Missing/partial npm installs recover through the lock file.
- [x] Repository-owned Electron locks fail with actionable diagnostics.
- [x] The launcher has a non-mutating check-only mode.
- [x] The full launcher can execute the hidden Electron self-test.
- [x] Root and Electron documentation identify Electron as the source entry.
- [x] Rebuild the local unpacked application from this source revision.
- [x] Exercise the launcher and packaged UI after repository-owned stale
  Electron processes have been closed.
