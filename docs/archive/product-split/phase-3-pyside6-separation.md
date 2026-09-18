# Phase 3 — DeepSonder-PySide6 separation

> 历史档案：保留当时的品牌、版本、路径与规划。本文不是当前操作指南；其中已删除的工具或旧升级步骤不适用于当前系列。当前说明见 [README](../../../README.md)，归档索引见 [历史文档](../README.md)。

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
- this series maintains `docs/feature-status.json`; the parity file is a historical archive.

The duplicated Electron renderer and its migration documentation were removed after explicit approval; their authoritative copies remain in `D:\GitHub-store\DeepSonder`. On 2026-09-18, the retired updater, Sidecar transport, Sidecar-only preferences/content adapters, obsolete Electron build/split scripts and their dedicated tests were removed from this repository. The unused desktop callbacks and helper methods were also removed. Historical schema-v2 services, relationship graphs, structured extraction, reconstruction and evaluation remain unchanged pending a separate reuse review. Current project services, read-only import fixtures, user data and release history are retained.
