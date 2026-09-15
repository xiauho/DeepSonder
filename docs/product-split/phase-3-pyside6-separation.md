# Phase 3 — DeepSonder-PySide6 separation

Status: implemented and under verification on 2026-09-15.

## Outcome

- `D:\GitHub-store\novalist` launches and packages through the PySide6 entry only.
- Display brand is DeepSonder; executable, profile and release artifact use `DeepSonder-PySide6`.
- The independent series version restarts at `0.1.0-beta`.
- Settings and cache live below `DeepSonder\PySide6`; old Novalist configuration is not migrated.
- The package omits Electron, Node.js, Sidecar and the legacy automatic updater.
- Old Novalist projects can be imported into a newly created project without changing the source.

## Legacy import contract

The importer accepts a directory containing `project.json` and `outline/chapters/*.md`. It reads at most 1 MiB of identity metadata, applies per-file and total manuscript limits, skips symbolic links, detects source changes before creation, and rejects a destination nested below the source.

Only project name, author and the ordered `## 正文` sections are written to the new project. Memory, recognized characters, canon, graphs, AI output, caches, proposals, update state and trash are excluded. The new project starts with empty derived stores so the user can explicitly run memory extraction and character recognition again.

## Series isolation

PySide6 tags use the `pyside6-v<version>` namespace and produce `DeepSonder-PySide6-v<version>-windows-x64.zip`. The manifest has no update channel and is not a cross-series installation contract. Electron continues in `D:\GitHub-store\DeepSonder` with its own entry, profile, tests and release path.

## Verification gates

- read-only legacy fixture hashes are unchanged before and after import;
- changed sources fail before a destination is created;
- destinations inside the old source are rejected;
- Python tests and packaged executable self-test pass;
- both `docs/feature-parity.json` files report the import capability consistently.

Repository-history cleanup of duplicated Electron and legacy updater source is intentionally tracked separately from release contents. It does not enter the PySide6 package and should only be removed after an explicit, reviewed deletion approval.
