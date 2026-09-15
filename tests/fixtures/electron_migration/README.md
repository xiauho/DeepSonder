# Product-split legacy import project

`golden_project` is synthetic test data for comparing the PySide6 and Electron
series and for defining the minimal old-project import boundary. It contains no
user material and must remain deterministic. `fixture-manifest.json` locks the
complete tree by SHA-256 so accidental fixture edits fail loudly.

The fixture intentionally exercises:

- a current project manifest;
- two naturally ordered chapters with standard and extra Markdown sections;
- character, world, power-system, timeline, and style-guide documents;
- directed character relationships whose two directions have different labels;
- an unresolved relationship target (`白鸥`) for Graph View warning coverage;
- chapter summaries and structured open/resolved foreshadowing notes;
- a power-system registry with both core and non-core entries.

Tests must copy the project to a temporary directory before exercising any
operation that can write, migrate, delete, or create backup/trash content.

The split importer may promote only the project name, author, and ordered
chapter Markdown listed in `legacy_import_contract`. Memory, recognized
characters, canon, style, caches, and other derived state are intentionally not
carried forward; each product series must rebuild them from the imported
manuscript.

Do not update fixture expectations merely to make a new implementation pass.
Any intentional persistent-format change must update the schema baseline,
migration tests, and fixture version in the same change.
