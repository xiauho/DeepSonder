# Persistent format and protocol baseline

Baseline date: 2026-09-11  
Application version: `2.1.0-beta`

These versions are independent compatibility axes. A future Electron application
version must not infer one version from another.

## Durable user and project formats

| Format | Current version | Authority | Location | Compatibility requirement |
|---|---:|---|---|---|
| Application configuration | 6 | `core/config.py` | Platform app config directory, `Novalist/config.json` | Reuse the existing path and Python normalization/migration logic. |
| Legacy project format | 1 | `core/project_schema.py` | `<project>/.novalist/project.json` | Frozen read-only manuscript source; never migrate it in place to v2. |
| Electron manuscript project | 2 | `core/project_v2_schema.py` | `<project>/project.json` and `<project>/.novalist/project.json` | New Electron target format; v1 is a read-only manuscript source and is never upgraded in place. |
| Project minimum app version | `2.0.8-beta` | `core/project_schema.py` | Project manifest | Reject unsupported future schemas rather than guessing. |
| Foreshadowing store | 1 | `core/foreshadowing.py` | `<project>/memory/foreshadowing.json` | Preserve author-owned IDs, status, links, and timestamps. |
| Accepted chapter memory | 1 | `core/accepted_memory.py` | `<project>/memory/accepted_chapter_memory.json` | Treat as durable provenance, not disposable cache. |
| Power-system registry | 1 | `core/project.py`, `core/project_data.py` | `<project>/canon/system_registry.json` | Preserve type and core/non-core importance. |
| Character card marker | 2 | `core/project_data.py` | Character Markdown | Preserve author prose and recognized marker blocks. |
| Managed character state marker | 1 | `core/character_card_sync.py` | Character Markdown | Only the marked block may be replaced by synchronization. |

## Rebuildable AI/cache formats

| Format | Current version | Authority | Compatibility behavior |
|---|---:|---|---|
| Chapter fact ledger | 1 | `core/chapter_facts.py` | Rebuild when incompatible. |
| Fact extraction prompt | 2 | `core/chapter_facts.py` | Part of cache/provenance identity. |
| Memory suggestion response | 2 | `core/chapter_memory.py` | Reject incompatible model output. |
| Memory proposal prompt | 4 | `core/chapter_memory.py` | Invalidate incompatible proposal cache. |
| Memory proposal cache | 1 | `core/chapter_memory.py` | Rebuild when incompatible. |
| Digest shard | 1 | `core/chapter_memory.py` | Rebuild when incompatible. |
| DSH task file | `NOVALIST_TASK_FILE_V1` | `core/dsh_client.py` | Keep internal; do not expose raw task files to the renderer. |

## Distribution and updater formats

| Format | Current version | Authority | Migration note |
|---|---:|---|---|
| Update install request | 1 | `core/update_installer.py` | Existing PySide6 portable-update path only until explicitly adapted. |
| Package file manifest | 1 | `core/update_installer.py` | Preserve allowlist, size, and SHA-256 semantics. |
| Release manifest | 2 | `scripts/build_windows.ps1` | Electron preview must use a distinct artifact identity/channel. |

## Electron contracts

Sidecar RPC protocol version `1` was introduced in Phase 2. It is an independent
integer and its startup handshake contains:

- application version;
- RPC protocol version;
- Sidecar build version;
- supported method allowlist;
- project schema range;
- configuration schema range.

The normative contract is [`rpc-v1.md`](rpc-v1.md). A protocol-breaking change
requires a new RPC version; changing the application version alone does not.

The renderer-facing preload API and the Python sidecar protocol are separate
contracts. Raw sidecar messages must not be forwarded directly to renderer code.

## Data ownership baseline

| Data | Owner | Renderer access |
|---|---|---|
| Project Markdown and JSON | Python project/application services | Typed snapshots and commands only. |
| Application configuration | Python config service | Normalized values and constrained updates. |
| DSH command execution | Python AI service | Start/cancel commands and sanitized events. |
| Native file/directory dialogs | Electron main | Allowlisted preload methods. |
| Window layout and ephemeral panel state | React/Electron | May remain frontend-local. |
| Graph layout coordinates | Frontend preferences | Must not become story truth. |
| Relationship meaning and evidence | Python project data | Read-only graph projection initially. |
| Update installation | Current updater, later Electron main | Status and user confirmation only. |

## Schema-v2 ownership reset

ADR-0002 supersedes the original requirement to preserve v1 structured story
data in the Electron release. Schema v2 owns `manuscript/`, `knowledge/`,
`proposals/`, `provenance/`, and rebuildable `cache/` independently. Only v1
chapter正文 can enter through the import plan. Reviewed v2 knowledge must be
reconstructed from manuscript evidence and explicit user decisions.

Phase 9 fixes the first collection semantics without changing project schema 2:

- `proposals/index.json` lists revision-bound review batches;
- `proposals/<batch-id>.json` stores evidence-backed candidates and durable
  accept/reject decisions;
- `knowledge/entities.json` and `knowledge/relations.json` contain only the
  reconciled projection of accepted, non-stale decisions;
- `provenance/evidence.json` contains the accepted evidence anchors used by the
  knowledge and graph projections.

The evidence projection is rebuildable and may be created when an earlier v2
project is opened. Its absence alone does not make a schema-2 project invalid.
