# DeepSonder product split: phase 1 baseline

> 历史档案：保留当时的品牌、版本、路径与规划。本文不是当前操作指南；其中已删除的工具或旧升级步骤不适用于当前系列。当前说明见 [README](../../../README.md)，归档索引见 [历史文档](../README.md)。

Captured on 2026-09-15 at commit
`c132d9f67b98cc6c2b872ddd0ffef1a81f1562a9` before either product tree was
removed or copied.

## Safety anchors

- The local branch `codex/pre-split-c132d9f` points to the pre-split commit.
- `origin/develop` remains at `abef639602608d5a7a25c9d5d6ba0537f82ceb2b`.
- `D:\GitHub-store\DeepSonder` existed and contained zero items when the
  baseline was captured. It was not initialized or modified in phase 1.

## Observed release sizes

| Series | Artifact | Bytes | MiB |
| --- | --- | ---: | ---: |
| PySide6 | Portable ZIP | 59,912,392 | 57.137 |
| PySide6 | Unpacked application | 132,647,006 | 126.502 |
| Electron | NSIS installer | 125,569,499 | 119.752 |
| Electron | Portable ZIP | 168,713,604 | 160.898 |
| Electron | Unpacked application | 420,064,962 | 400.605 |
| Electron | Bundled Sidecar | 10,715,994 | 10.220 |

These are measurements of existing local rehearsal artifacts, not reproducible
release attestations. The PySide6 artifacts were last written on 2026-09-08;
the Electron installer and ZIP were last written on 2026-09-14, while the
unpacked Electron executable was regenerated on 2026-09-15. Their manifests do
not bind a source commit, so the values are optimization baselines only.

The one-time cross-series measurement script was retired on 2026-09-18.
These measurements remain historical records; current builds use
`scripts/build_windows.ps1` and produce independent PySide6 release artifacts.

## Frozen legacy-import fixture

`tests/fixtures/electron_migration/golden_project` is the synthetic legacy
project used as the split baseline. Its manifest now locks every file by
SHA-256 and defines the minimal import contract:

- import project name, author, chapter order, titles, and chapter Markdown;
- do not promote memory, character cards, world data, power-system data,
  timelines, style guides, or other derived state into a new project;
- leave the source tree byte-for-byte unchanged.

The fixture remains in its current location during phase 1 so existing tests do
not require a broad path rewrite. It may be copied independently into each
series during the physical split.

## Verification at capture

- Python: 554 tests passed, 3 skipped.
- Electron: build and type checking passed; 20 tests passed.
- Fixture hash lock: 5 focused tests passed.
- Baseline measurement script completed and reported the Electron target as
  present and empty.

## Phase 2 entry conditions

Phase 2 may begin when:

1. the safety branch resolves to the recorded commit;
2. the fixture hash-lock test passes;
3. the target directory is still empty; and
4. generated folders (`node_modules`, `.venv`, `build`, `dist`, caches, and
   Electron release output) are excluded from the copy plan.
