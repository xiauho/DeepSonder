# Novalist local Sidecar RPC v1

Status: implemented for project/document editing and the local-data management slice.

RPC v1 is a local, newline-delimited JSON protocol over the Sidecar process's
stdin and stdout. UTF-8 is mandatory. Each line is one complete JSON object and
messages larger than 16 MiB are rejected.

stdout is protocol-only. Logs, diagnostics, and tracebacks belong on stderr and
must never be parsed as RPC messages.

## Envelope

Request:

```json
{"type":"request","protocolVersion":1,"id":"open-1","method":"project.open","params":{"path":"D:/Books/My Novel"}}
```

Success response:

```json
{"type":"response","protocolVersion":1,"id":"open-1","ok":true,"result":{}}
```

Error response:

```json
{"type":"response","protocolVersion":1,"id":"save-2","ok":false,"error":{"code":"REVISION_CONFLICT","message":"文件已在外部发生变化。","retryable":false,"data":{}}}
```

Event:

```json
{"type":"event","protocolVersion":1,"event":"document.changed","data":{}}
```

Request IDs are non-empty strings of at most 128 characters or JSON-safe
integers. A request ID must not be reused while its earlier request is active.
Responses may arrive out of input order and must be correlated by ID.

Unknown envelope and parameter fields are rejected. The Electron main process
must validate and translate these messages; raw messages are not a renderer API.

## Startup and shutdown

The first stdout message is `sidecar.ready`, containing the protocol version and
`ndjson-stdio` transport name. The client then calls `system.handshake` and must
verify all compatibility axes before opening a project.

The handshake returns `applicationVersion`, `protocolVersion`,
`sidecarBuildVersion`, `supportedMethods`, `projectSchema`, and `configSchema`.
Optional `clientName` and `clientVersion` fields are echoed for diagnostics.

EOF performs a clean shutdown after accepted work completes. `system.shutdown`
returns an acknowledgement and emits `sidecar.stopping`. The Electron main
process may terminate an unresponsive process after its own bounded grace period.

## Allowlisted methods

| Method | Parameters | Result / effect |
|---|---|---|
| `system.handshake` | optional `clientName`, `clientVersion` | Compatibility and capability descriptor. |
| `system.ping` | none | `{ "alive": true }`. |
| `system.shutdown` | none | Graceful shutdown acknowledgement. |
| `request.cancel` | `targetId` | Cancels a queued request or reports `running`, `finished`, or `notFound`. |
| `project.open` | `path`, optional `remember` | Validates, migrates if required, activates, and returns an opened-project DTO; explicit `remember` updates existing recent-project settings. |
| `project.create` | `parentDirectory`, `name`, optional `author`, optional `remember` | Creates and activates a current-schema project. |
| `project.restoreLast` | none | Opens the valid configured last project or returns `opened: null`. |
| `project.close` | none | Clears the active Sidecar project. Unsaved-document decisions stay in the frontend workflow. |
| `project.current` | none | Returns the active opened-project DTO or `null`. |
| `project.snapshot` | none | Returns grouped chapters, outlines, characters, world entries, power systems, timeline, protected/importance metadata, and the next chapter ID. |
| `project.setSystemImportance` | `path`, `importance` | Updates one ordinary power system to `core` or `non_core` and returns a fresh project snapshot. |
| `preferences.get` | none | Returns the frontend-safe normalized preference subset. |
| `preferences.update` | `patch` | Allowlisted update of theme, UI/editor font size, autosave, interval, or line-number settings. |
| `document.open` | `category`, `path` | Returns one immutable document snapshot. Paths may be project-relative. |
| `document.save` | `category`, `path`, `content`, `expectedRevision`, optional `force` | Atomically saves and returns the new snapshot. |
| `document.createChapter` | `title`, `chapterId` | Creates the existing chapter template and returns the mutation plus a fresh project snapshot. |
| `document.createCanonEntry` | `kind`, `title` | Creates a `character`, `world`, or `power` template. |
| `document.createTimeline` | none | Creates the singleton timeline or returns `ALREADY_EXISTS`. |
| `document.importMarkdown` | `sources` | Imports 1–100 native-dialog-selected Markdown files as conflict-free chapters. |
| `document.delete` | `kind`, `itemId`, `path` | Moves one deletable project document to its typed project trash. |
| `trash.list` | none | Returns the combined typed project recycle-bin snapshot. |
| `trash.restore` | `kind`, `trashId`, optional `conflictPolicy` | Restores an item; policy is `error` or explicit `rename`. |
| `trash.deleteForever` | `kind`, `trashId` | Permanently deletes one validated typed trash item. |
| `graph.snapshot` | none | Returns a deterministic read-only projection of directed character relationships and accepted evidence. |
| `knowledge.snapshot` | none | Returns accepted, curated entities, relations, active/hidden worlds, active/hidden character fields, merge mappings, and audit-operation count. |
| `knowledge.renameEntity` | `entityId`, `displayName` | Persists a display-name override after collision validation. |
| `knowledge.setEntityAliases` | `entityId`, `aliases` | Replaces the entity's curated aliases (maximum 20). |
| `knowledge.mergeEntities` | `sourceEntityId`, `targetEntityId` | Projects one identity into another without deleting the accepted source. |
| `knowledge.unmergeEntity` | `sourceEntityId` | Removes a merge mapping and restores the accepted source identity. |
| `knowledge.updateRelation` | `relationId`, `sourceEntityId`, `targetEntityId`, `label` | Overrides relationship direction, endpoints, and label. |
| `knowledge.deleteRelation` | `relationId` | Hides one accepted relationship while retaining its proposal history. |
| `knowledge.updateCharacterField` | `entityId`, allowlisted `field`, `value` | Persists an audited value override for one visible accepted character field. |
| `knowledge.hideCharacterField` | `entityId`, allowlisted `field` | Hides one character field while retaining its value and evidence-backed base. |
| `knowledge.restoreCharacterField` | `entityId`, allowlisted `field` | Restores one hidden character field. |
| `knowledge.updateWorld` | `worldId`, `name`, `category`, `description` | Persists an audited world-entry override after collision validation. |
| `knowledge.hideWorld` | `worldId` | Removes one world from active projection while retaining recovery state. |
| `knowledge.restoreWorld` | `worldId` | Restores one hidden world after collision validation. |
| `knowledge.updateEvent` | `eventId`, `timeLabel`, `title`, `description` | Persists an audited text override for one active reviewed event. |
| `knowledge.updateEventLinks` | `eventId`, `participantEntityIds`, `worldIds` | Replaces one event's semantic links using validated active stable IDs. |
| `knowledge.reorderEvents` | complete ordered `eventIds` | Persists an audited order for every active event while retaining source order. |
| `knowledge.hideEvent` | `eventId` | Removes one event and its semantic links from active projection while retaining recovery state. |
| `knowledge.restoreEvent` | `eventId` | Restores one hidden event after collision validation. |
| `knowledge.openCard` | `ownerKind`, `ownerId`, `mode` | Opens a Python-resolved generated or author knowledge card; generated mode is read-only. |
| `knowledge.saveAuthorCard` | `ownerKind`, `ownerId`, `content`, `expectedRevision` | Atomically saves a Python-resolved author card if its opaque revision is current. |
| `knowledge.reopenProposal` | `batchId`, `proposalId` | Returns an accepted/rejected proposal to pending and rebuilds projected knowledge. |
| `reconstruction.start` | optional `mode` (`local` or `dsh`), `remoteConsent` | Starts background extraction. `dsh` requires `remoteConsent: true` for that invocation and safely falls back to local rules on remote failure. |
| `reconstruction.reviewMany` | `batchId`, 1–200 unique `decisions` (`proposalId`, `decision`) | Validates the complete pending set, applies accepted/rejected decisions together, and rebuilds projected knowledge once. |
| `ai.status` | none | Returns the active task and bounded session task history. |
| `ai.start` | `kind`, `chapterId`, `sourceRevision`, `noticeAccepted`, optional `options` | Starts one background `expand`, `continuation`, `check`, `memory`, or `connection` task and returns immediately. |
| `ai.cancel` | `taskId` | Requests cooperative cancellation through the existing DSH cancel event. |
| `ai.result` | `taskId` | Returns a validated, review-safe result after successful completion. |
| `ai.discardResult` | `taskId` | Releases one uncommitted review result. |
| `ai.applyWritingResult` | `taskId` | Revalidates task context and revision, then applies expansion or continuation to the chapter. |
| `ai.commitMemoryResult` | `taskId` | Revalidates and explicitly commits a blocker-free memory proposal. |

All ordinary application calls execute serially in one worker so project
mutations cannot overlap. The input reader handles cancellation and shutdown
control messages without waiting behind that queue.

RPC v1 only cancels work that has not started. A running synchronous operation
returns `accepted: false, state: "running"`; this prevents a caller from assuming
a durable mutation was rolled back. Long-running AI tasks use their separate
cooperative cancellation lifecycle; ordinary synchronous project mutations
retain the queued-only rule above.

## DTOs

An opened-project DTO contains:

- `project.root`, `project.name`, and `project.author`;
- migration `fromSchema`, `toSchema`, `changedFiles`, optional `backupPath`, and
  `recoveredInterruptedMigration`.

A document DTO contains:

- absolute `path` plus project-relative `relativePath`;
- `category`, `title`, and Markdown `content`;
- opaque `revision` token.

Clients must not parse revision tokens. A normal save requires exactly the token
returned by the last open/save response. Missing or stale tokens produce
`REVISION_CONFLICT`; overwriting requires explicit `force: true` after user
confirmation.

A project snapshot is navigation data, not a second source of truth. Every item
contains its stable local ID, type/category, display title, absolute and
project-relative paths, plus protection and power-system importance metadata.
The renderer replaces snapshots after mutations; Python continues to own file
enumeration, templates, ordering, IDs, registries, and trash transactions.

A preferences snapshot contains camel-case theme, UI/editor font size,
autosave, interval, line-number, last-project, and recent-project fields only.
It deliberately excludes DSH execution settings, credentials, update metadata,
and all other configuration keys. Update patches use the corresponding
snake-case schema-6 keys inside the Sidecar and are normalized before saving.

A relationship-graph snapshot contains deterministic `nodes`, directed `edges`,
data-quality `warnings`, relation labels, and a `graph-v1` source revision. Node
paths exist only for resolved project character cards. Edge evidence comes only
from verified accepted-memory records. The snapshot is a projection: it cannot
be sent back to mutate project data, and renderer layout coordinates are not
part of the DTO.

A reconstruction proposal is one of `entity`, `relation`, `world`,
`character_field`, or `event`. World proposals carry name/category/description;
character fields carry character name, one allowlisted field, and its value;
events carry time/title/description plus bounded character/world name arrays. A knowledge
snapshot includes accepted and hidden world entries, accepted and hidden
character profile fields, stable project-relative generated-card paths,
read-only manuscript evidence references, and derived timeline diagnostics.
These paths are descriptive DTO data and do not grant renderer filesystem
access. A card DTO returns owner kind/ID, generated-or-author mode, content,
opaque revision, and read-only state. Clients never send card paths.

A graph node declares `nodeKind` (`character`, `world`, or `event`) and optional
timeline `order`. A graph edge declares `edgeKind` (`relationship`,
`participation`, or `setting`). Event semantic links carry the same reviewed
evidence as their accepted event proposal.

## Events

- `sidecar.ready`
- `sidecar.stopping`
- `project.opened`
- `project.closed`
- `document.changed`
- `project.contentChanged`
- `trash.changed`
- `ai.taskUpdated`
- `ai.contextReport`
- `reconstruction.taskUpdated`
- `reconstruction.updated`
- `knowledge.updated`

Events report completed state changes only. They are not permission to access
arbitrary paths.

## Stable error codes

Protocol errors include `INVALID_JSON`, `INVALID_ENCODING`, `MESSAGE_TOO_LARGE`,
`INVALID_REQUEST`, `INVALID_PARAMS`, `PROTOCOL_VERSION_MISMATCH`,
`DUPLICATE_REQUEST_ID`, `METHOD_NOT_FOUND`, `REQUEST_CANCELLED`, and
`SIDECAR_STOPPING`.

Application errors include `PROJECT_NOT_OPEN`, `PROJECT_MIGRATION_FAILED`,
`DOCUMENT_NOT_FOUND`, `DOCUMENT_PATH_INVALID`, `REVISION_CONFLICT`,
`ID_CONFLICT`, `AI_TASK_RUNNING`, `ALREADY_EXISTS`, `NOT_FOUND`, `NOT_A_DIRECTORY`, `PERMISSION_DENIED`,
`INVALID_ARGUMENT`, `DOCUMENT_ERROR`, `IO_ERROR`, and `INTERNAL_ERROR`.

Unexpected exceptions are logged to stderr and become a generic
`INTERNAL_ERROR`; tracebacks and manuscript contents are never serialized to
stdout.

## Security boundary

- There is no generic filesystem, shell, process, import, or eval method.
- Document paths are revalidated by `DocumentService` against the active project
  and its editable Markdown allowlist.
- Python remains authoritative for migrations, atomic writes, and revision
  conflicts.
- Electron main must spawn the Sidecar directly with pipes, never through a shell.
- Electron renderer access is narrower than this protocol through an allowlisted,
  typed preload API with context isolation and sandboxing enabled.
- Import source paths originate in Electron main's native file dialog; renderer
  code cannot supply arbitrary import paths.
- Recoverable and permanent deletion require separate capabilities. Electron
  main shows the final native confirmation for destructive actions.
- AI start requires an explicit data-processing acknowledgement and a current
  chapter revision. Generated writing and memory remain non-durable until a
  separate apply/commit call passes a fresh project-context check.
- Context reports contain counts, allocation states, token estimates, and
  transport diagnostics only; prompt and manuscript bodies are excluded.
- Graph RPC is read-only. Current story state takes precedence over character
  cards, unresolved names are warnings, and accepted memory adds provenance
  without creating relationships.
- Knowledge-card RPC derives paths from validated stable owner IDs in Python.
  Generated cards cannot be saved; author saves require the current opaque
  revision and cannot address a renderer-supplied path.
