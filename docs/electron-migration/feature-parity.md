# Electron feature parity matrix

> Historical v1 transition inventory. ADR-0002 supersedes the stable-cutover
> requirement to reproduce every PySide6 workflow or migrate v1 structured
> story data. Existing Preview rows remain reusable implementation evidence;
> schema-v2 import, reconstruction, and Electron-only release gates are tracked
> in the Phase 7B roadmap.

Baseline: Novalist `2.1.0-beta`, recorded 2026-09-11.

## Status legend

- `Baseline`: confirmed current PySide6 behavior that must be preserved.
- `Preview`: exercised in the parallel Electron source Preview but not yet a
  packaged parity pass.
- `Planned`: target ownership is identified but implementation has not started.
- `Deferred`: intentionally excluded from the first Electron parity release.

Priorities:

- `P0`: required before Electron can replace PySide6 as the stable release.
- `P1`: required for the first stable Electron feature set but not for the initial
  writing vertical slice.
- `P2`: post-parity enhancement.

## Application shell and project lifecycle

| ID | Capability | Current implementation | Target owner | Parity gate | Priority | Status |
|---|---|---|---|---|---|---|
| APP-01 | Single desktop window and stable application identity | `main.py`, `ui/main_window.py` | Electron main | Packaged app starts once and focuses the existing instance. | P0 | Preview |
| APP-02 | Restore last project | `ProjectLifecycleController`, config | Python `ProjectService` + renderer bootstrap | Valid existing last project reopens; missing path fails safely. | P0 | Preview |
| APP-03 | Recent-project list | `ProjectLifecycleController`, main menu | Python `ProjectService` | Ordering, normalization, and missing-path handling match. | P1 | Baseline |
| APP-04 | Create project and initial skeleton | `NovelProject.create`, `ProjectSetupDialog` | Python `ProjectService` + React dialog | Current-schema project contains every required file. | P0 | Preview |
| APP-05 | Open and migrate existing project | `migrate_project`, `ProjectSession` | Python `ProjectService` | Golden current project opens without writes; legacy fixture migrates transactionally. | P0 | Preview |
| APP-06 | Reject future/invalid project formats | `project_migrations.py` | Python `ProjectService` | Structured error is shown without modifying the project. | P0 | Baseline |
| APP-07 | Unsaved-change guard on project switch and exit | `MainWindow`, `DocumentController` | React workflow + Python `DocumentService` | Save/discard/cancel outcomes are deterministic. | P0 | Preview |
| APP-08 | Native project directory selection | `QFileDialog` | Electron main + preload | Renderer receives only the selected path/result. | P0 | Preview |
| APP-09 | About/version information | `MainWindow.show_about` | Electron main + React dialog | UI and sidecar versions are visible and consistent. | P1 | Baseline |

## Navigation and window state

| ID | Capability | Current implementation | Target owner | Parity gate | Priority | Status |
|---|---|---|---|---|---|---|
| NAV-01 | Primary routes: dashboard, writing, canon, memory, reports, export, settings | `PrimaryNavigation`, `MainWindow.ROUTES` | React router | Route changes preserve valid project/document selection. | P0 | Baseline |
| NAV-02 | Search chapters, characters, and canon | `LeftPanel` | React tree/filter + project snapshot | Matching items and empty state behave consistently. | P0 | Preview |
| NAV-03 | Select and reveal project entries | `LeftPanel`, `StoryNavigationController` | React navigation state | Report/memory links open the correct document. | P0 | Baseline |
| NAV-04 | Collapsible navigation, inspector, and AI output | `WindowStateController` | Zustand/local preferences | Toggled states survive route changes. | P1 | Baseline |
| NAV-05 | Resizable three-pane writing workspace | Qt splitters | React split panels | Minimum editor width and usable saved sizes are enforced. | P1 | Baseline |
| NAV-06 | Focus mode and Escape/F11 exit | `WindowStateController`, shortcuts | Electron menu + React state | Panels and chrome restore to their previous state. | P1 | Baseline |
| NAV-07 | Light/dark theme and font sizes | `AppearanceController`, `theme.py`, config | React design tokens + Python config | Existing normalized settings render without data migration. | P0 | Preview |
| NAV-08 | Existing keyboard shortcuts | `MainWindow._build_actions` | Electron menu + renderer commands | Shortcut matrix has packaged E2E coverage. | P1 | Baseline |

## Editor and document safety

| ID | Capability | Current implementation | Target owner | Parity gate | Priority | Status |
|---|---|---|---|---|---|---|
| DOC-01 | Open Markdown document | `Editor.open_file` | Python `DocumentService` + CodeMirror | Content, title, category, and revision load together. | P0 | Preview |
| DOC-02 | Dirty/saved/error state | `Editor` | React document store | State transitions are covered by deterministic tests. | P0 | Preview |
| DOC-03 | Atomic manual save | `Editor`, `core/storage.py` | Python `DocumentService` | No partial write is possible. | P0 | Preview |
| DOC-04 | Timed autosave | `DocumentController` | React scheduler + Python `DocumentService` | Interval and enabled flag use normalized config values. | P0 | Preview |
| DOC-05 | Detect external file changes | `Editor`, `DocumentController` | Python revision token + React conflict dialog | Stale save never silently overwrites disk content. | P0 | Preview |
| DOC-06 | Undo/redo | Qt text document | CodeMirror | Standard shortcuts and menu commands work with IME input. | P0 | Preview |
| DOC-07 | Find, next/previous, replace, replace all | `Editor` | CodeMirror search extension | Counts and replacements match source text. | P1 | Preview |
| DOC-08 | Markdown source/readonly preview | `Editor`, `MarkdownPreview` | CodeMirror + sanitized React Markdown | Preview never mutates or autosaves source. | P0 | Preview |
| DOC-09 | Word/paragraph/read-time/cursor statistics | `Editor`, `core/text_metrics.py` | Python metrics or shared defined algorithm | Golden chapter values match the current implementation. | P1 | Baseline |
| DOC-10 | Reveal and highlight exact source range | `Editor.reveal_range` | CodeMirror decoration | Consistency-result navigation selects the correct range. | P1 | Baseline |
| DOC-11 | Safe body append/replace and anchored insertion | `Editor`, chapter/text anchor helpers | Python `DocumentService` + editor selection | Extra chapter sections are preserved. | P0 | Baseline |

## Project data and recycle bin

| ID | Capability | Current implementation | Target owner | Parity gate | Priority | Status |
|---|---|---|---|---|---|---|
| DATA-01 | Create/rename/reorder/delete/restore chapters | schema-v2 `DocumentV2Service`, typed Electron IPC, React chapter list | Python `DocumentV2Service` + React dialogs | Stable IDs, ordering, revision guards, neighbors, and trash metadata round-trip. | P0 | Candidate |
| DATA-02 | Import Markdown chapters | schema-v2 import plan, append transaction journal, native source dialog | Python `ManuscriptImportService` + `DocumentV2Service` | Sources are rescanned, duplicates are rejected, provenance is retained, and partial imports recover. | P1 | Candidate |
| DATA-03 | Create/delete/restore character cards | `ProjectDataStore`, `TrashDialog` | Python `CanonService` | Character template markers remain valid. | P0 | Preview |
| DATA-04 | Create/delete/restore world entries | `ProjectDataStore`, `TrashDialog` | Python `CanonService` | Author text and trash metadata round-trip. | P1 | Preview |
| DATA-05 | Create/delete/restore power systems | `ProjectDataStore`, `TrashDialog` | Python `CanonService` | Registry metadata round-trips with the file. | P1 | Preview |
| DATA-06 | Protect global core rules | `ProjectDataStore` | Python `CanonService` | Delete/rename attempts are rejected. | P0 | Preview |
| DATA-07 | Mark systems core/non-core | `LeftPanel`, `ProjectDataStore` | Python `CanonService` | Registry normalization remains authoritative. | P1 | Preview |
| DATA-08 | Singleton timeline creation/editing | `ProjectDataStore` | Python `CanonService` | Duplicate timeline creation is prevented. | P1 | Preview |
| DATA-09 | Permanent delete confirmation | `TrashDialog` | React alert dialog + Python store | Exact item and irreversible effect are stated. | P0 | Preview |

## Dashboard, story radar, and memory

| ID | Capability | Current implementation | Target owner | Parity gate | Priority | Status |
|---|---|---|---|---|---|---|
| MEM-01 | Project dashboard counts and readiness | `DashboardPage` | Python project snapshot + React page | Golden fixture metrics match. | P1 | Baseline |
| MEM-02 | Recent chapter and next-step actions | `DashboardPage` | React page | Actions navigate to the intended route/document. | P1 | Baseline |
| MEM-03 | Story radar chapter context | `Inspector` | Python context snapshot + React inspector | Chapter goals, characters, canon, and state update together. | P1 | Baseline |
| MEM-04 | Sanitized AI context report | `Inspector`, context report core | Python AI events + React inspector | No prompt body, credentials, or absolute private paths are exposed. | P0 | Preview |
| MEM-05 | Story-memory overview and chapter summaries | `StoryMemoryPage` | Python `MemoryService` + React page | Golden summaries and state render correctly. | P1 | Baseline |
| MEM-06 | Foreshadowing create/edit/status/delete/restore | `ForeshadowingStore`, dialogs/page | Python `MemoryService` + React forms | IDs, appearances, status, and timestamps remain valid. | P1 | Baseline |
| MEM-07 | Generate and explicitly adopt memory proposal | schema-v2 reviewed chapter memory | Python `AITaskService` + v2 context adapter | No durable state changes before explicit commit; stale records never re-enter context. | P0 | Candidate |
| MEM-08 | Character-card synchronization preview and selected commit | sync core and Qt dialogs | Python `CharacterService` + React review dialog | Only selected managed fields change; backup is created. | P0 | Baseline |

## AI workflows and consistency

| ID | Capability | Current implementation | Target owner | Parity gate | Priority | Status |
|---|---|---|---|---|---|---|
| AI-01 | DSH configuration and connection test | `AIEngineController`, settings | Python `AIEngineService` | Test runs off the UI thread and returns sanitized diagnostics. | P0 | Preview |
| AI-02 | First-use data-processing notice | `AIWorkflowController` | React dialog + Python config | No AI task starts before acknowledgement. | P0 | Preview |
| AI-03 | Expansion with context selection and length review | schema-v2 manuscript + reviewed knowledge adapter | Python `AITaskService` + React review | No legacy structured data enters context; adoption is context/revision guarded. | P0 | Candidate |
| AI-04 | Continuation with target-length rules | schema-v2 manuscript + reviewed knowledge adapter | Python `AITaskService` + React review | Only the generated fragment is appended after fresh context/revision validation. | P0 | Candidate |
| AI-05 | Long task progress, cancellation, and terminal state | `AITaskRunner`, task controllers | Sidecar task manager + renderer events | Every task ends once as succeeded/failed/cancelled. | P0 | Preview |
| AI-06 | Consistency check and severity/category rendering | reviewed schema-v2 knowledge + manuscript history | Python protocol validation + React review | Pending/rejected knowledge is excluded and output enums are validated. | P0 | Candidate |
| AI-07 | Jump from issue to unique source anchor | workflow/editor | Python anchor result + CodeMirror | Only uniquely resolved anchors enable jumping. | P1 | Baseline |
| AI-08 | AI repair proposal and guarded replacement | result coordinator/core | Python `AITaskService` + React review | Original range and expected text are revalidated at commit. | P0 | Baseline |
| AI-09 | AI output history for current session | `AITaskViewController` | React session store | Copy/clear/collapse work; no durable prompt log is added. | P1 | Baseline |

## Export, settings, and updates

| ID | Capability | Current implementation | Target owner | Parity gate | Priority | Status |
|---|---|---|---|---|---|---|
| SYS-01 | Markdown and plain-text whole-book export | schema-v2 ordered renderer + Electron native save dialog | Python `DocumentV2Service` + Electron main | Ordered title, contents, separators, encoding, and digest pass golden tests. | P0 | Candidate |
| SYS-02 | Settings validation, defaults, and persistence | config core/controller/page | Python `ConfigService` + React forms | Config schema remains 6 until an explicit migration is needed. | P0 | Preview |
| SYS-03 | Update check and release-channel preference | update core/controllers | Transition adapter, later Electron main | Stable/beta selection and skip behavior remain clear. | P1 | Baseline |
| SYS-04 | Verified download | update download core | Transition adapter, later Electron main | Platform, size, digest, archive, and manifest checks pass. | P1 | Baseline |
| SYS-05 | Install and recover without project overwrite | standalone updater | Electron NSIS + versioned portable recovery ZIP | Clean install, side-by-side recovery, and project hash checks pass; automatic update stays disabled until it consumes signed metadata. | P0 | Candidate |
| SYS-06 | Packaged application self-test | `main.py`, build script | Electron main + sidecar | Frontend load, preload API, handshake, resources, and temp-project read pass. | P0 | Preview |
| SYS-07 | Privacy: no telemetry and no credential storage | documented/core behavior | All layers | E2E/debug bundle review reveals no manuscript body or credentials. | P0 | Baseline |

## Post-parity Graph View

| ID | Capability | Current implementation | Target owner | Parity gate | Priority | Status |
|---|---|---|---|---|---|---|
| GRAPH-01 | Read-only character relationship projection | Structured `characters.*.relations` data exists | Python `RelationshipGraphService` | Golden fixture yields directed, labeled edges and unresolved-node warnings. | P1 | Preview |
| GRAPH-02 | Interactive graph layout, search, and filters | None | React + Cytoscape.js | Node/edge selection and filters do not modify story data. | P1 | Preview |
| GRAPH-03 | Open character/evidence from graph | Electron graph evidence navigation | React navigation + Python evidence projection | Navigation preserves editor state. | P1 | Preview |
| GRAPH-04 | Direct graph editing | None | Future project schema and application service | Requires stable entity IDs, history, rename, and conflict rules. | P2 | Deferred |

## Stable cutover gate

Electron may replace PySide6 as the stable release only when:

1. Every `P0` row has automated or documented packaged-build evidence.
2. The golden project opens without a project migration or content rewrite.
3. Save-conflict, AI cancellation, proposal review, export, and packaged self-test
   pass on Windows 10 and Windows 11.
4. The selected installer/update path cannot overwrite project or config data.
5. The Electron package provides manuscript-only transition guidance and a
   versioned portable recovery artifact.
6. The PySide6 source/build path remains available for maintainer rollback during
   the first stable Electron window, but is not a second distributed user entry.
