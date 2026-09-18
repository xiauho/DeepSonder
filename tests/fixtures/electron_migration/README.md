# Legacy import fixture

`golden_project` is frozen synthetic old-project data. The historical directory
name `electron_migration` is retained because several service tests share it;
it does not establish PySide6/Electron compatibility or release parity.
`fixture-manifest.json` locks its complete tree by SHA-256. Do not edit the
fixture's content merely to make tests pass.

The fixture includes two ordered chapters with standard and extra Markdown
sections, characters, world and power documents, timeline and style notes,
relationships, memory, and a system registry. These extra files exercise the
import exclusion boundary and support service tests on temporary copies.
The fixture is not required to match the current project schema or navigation.

The old-project importer may carry forward only the project name, author, and
ordered chapter Markdown described by `legacy_import_contract`. Existing
memory, recognized characters, canon, style, caches, and other derived state
must not be imported. Import tests verify the source tree remains unchanged.

Copy the project to a temporary directory before any operation that writes,
migrates, deletes, or creates backup/trash content. Current-project creation
and schema behavior belong in tests using freshly created projects, not in
assertions that this historical fixture must remain a current native project.
