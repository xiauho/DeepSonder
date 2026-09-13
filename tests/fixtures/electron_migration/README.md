# Electron migration golden project

`golden_project` is synthetic test data for comparing the current PySide6
application with the future Electron application. It contains no user material
and must remain deterministic.

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

Do not update fixture expectations merely to make a new implementation pass.
Any intentional persistent-format change must update the schema baseline,
migration tests, and fixture version in the same change.

