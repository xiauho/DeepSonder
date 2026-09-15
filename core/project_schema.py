"""Version identifiers shared by project creation and migration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


PROJECT_SCHEMA_VERSION = 1
PROJECT_SCHEMA_MINIMUM_APP_VERSION = "2.0.8-beta"
PROJECT_MANIFEST_RELATIVE_PATH = Path(".novalist") / "project.json"
PROJECT_MIGRATION_JOURNAL_RELATIVE_PATH = (
    Path(".novalist") / "migration-journal.json"
)


def current_app_version() -> str:
    """Return the installed app version without making migration depend on it."""
    try:
        from .version import load_current_version

        return str(load_current_version())
    except (OSError, ValueError):
        return "unknown"


def project_manifest(
    *,
    created_by: str | None = None,
    migrated_from: int | None = None,
) -> dict:
    """Build the content-only project format manifest."""
    app_version = current_app_version()
    payload = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "created_by": str(created_by or app_version),
        "last_migrated_by": app_version,
        "minimum_app_version": PROJECT_SCHEMA_MINIMUM_APP_VERSION,
    }
    if migrated_from is not None:
        payload["migration"] = {
            "from_schema": int(migrated_from),
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    return payload
