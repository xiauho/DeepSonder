"""Schema contract for Electron-first manuscript reconstruction projects.

Schema v2 intentionally lives beside, rather than replacing, the frozen v1
schema.  The legacy application must see the hidden schema-2 manifest as a
future format and reject it without attempting a v0-to-v1 migration.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


PROJECT_V2_SCHEMA_VERSION = 2
PROJECT_V2_KIND = "novalist-manuscript-reconstruction"
PROJECT_V2_MANIFEST = Path(".novalist") / "project.json"
PROJECT_V2_METADATA = Path("project.json")
PROJECT_V2_MINIMUM_APP = "electron-v2-preview"

PROJECT_V2_DIRECTORIES = (
    Path("manuscript"),
    Path("knowledge"),
    Path("proposals"),
    Path("provenance"),
    Path("cache"),
)

PROJECT_V2_COLLECTIONS: dict[Path, str] = {
    Path("knowledge/entities.json"): "entities",
    Path("knowledge/relations.json"): "relations",
    Path("knowledge/world_rules.json"): "rules",
    Path("knowledge/timeline.json"): "events",
    Path("proposals/index.json"): "batches",
    Path("provenance/imports.json"): "imports",
    Path("manuscript/index.json"): "chapters",
}


class ProjectV2ValidationError(ValueError):
    """Raised when a directory does not satisfy the schema-v2 contract."""


@dataclass(frozen=True)
class ProjectV2Descriptor:
    root: Path
    project_id: str
    name: str
    author: str
    created_at: str


def schema_manifest(*, created_by: str) -> dict:
    return {
        "schema_version": PROJECT_V2_SCHEMA_VERSION,
        "project_kind": PROJECT_V2_KIND,
        "created_by": str(created_by or "unknown"),
        "minimum_app_version": PROJECT_V2_MINIMUM_APP,
        "legacy_import_policy": "manuscript_only",
    }


def project_metadata(
    *,
    project_id: str,
    name: str,
    author: str,
    created_at: str,
) -> dict:
    return {
        "schema_version": PROJECT_V2_SCHEMA_VERSION,
        "project_kind": PROJECT_V2_KIND,
        "project_id": project_id,
        "name": name,
        "author": author,
        "created_at": created_at,
        "updated_at": created_at,
        "content_policy": {
            "legacy_import": "manuscript_only",
            "knowledge_requires_review": True,
            "manuscript_is_primary_source": True,
        },
    }


def empty_collection(key: str) -> dict:
    return {"schema_version": 1, key: []}


def validate_project_v2(root: Path | str) -> ProjectV2Descriptor:
    """Validate one v2 project without modifying it."""
    project_root = Path(root).expanduser().resolve()
    if not project_root.is_dir():
        raise ProjectV2ValidationError(f"项目目录不存在：{project_root}")

    metadata = _read_object(project_root / PROJECT_V2_METADATA, "项目元数据")
    manifest = _read_object(project_root / PROJECT_V2_MANIFEST, "项目格式清单")
    for label, value in (("项目元数据", metadata), ("项目格式清单", manifest)):
        if value.get("schema_version") != PROJECT_V2_SCHEMA_VERSION:
            raise ProjectV2ValidationError(f"{label}不是 schema 2。")
        if value.get("project_kind") != PROJECT_V2_KIND:
            raise ProjectV2ValidationError(f"{label}的 project_kind 无效。")

    project_id = metadata.get("project_id")
    try:
        UUID(str(project_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ProjectV2ValidationError("project_id 必须是有效 UUID。") from exc

    name = metadata.get("name")
    author = metadata.get("author")
    created_at = metadata.get("created_at")
    if not isinstance(name, str) or not name.strip():
        raise ProjectV2ValidationError("项目名称不能为空。")
    if not isinstance(author, str) or not isinstance(created_at, str):
        raise ProjectV2ValidationError("项目作者或创建时间无效。")
    policy = metadata.get("content_policy")
    if (
        not isinstance(policy, dict)
        or policy.get("legacy_import") != "manuscript_only"
        or policy.get("knowledge_requires_review") is not True
        or policy.get("manuscript_is_primary_source") is not True
    ):
        raise ProjectV2ValidationError("项目内容策略无效。")

    for relative in PROJECT_V2_DIRECTORIES:
        if not (project_root / relative).is_dir():
            raise ProjectV2ValidationError(f"项目缺少目录：{relative.as_posix()}")
    for relative, key in PROJECT_V2_COLLECTIONS.items():
        value = _read_object(project_root / relative, relative.as_posix())
        if value.get("schema_version") != 1 or not isinstance(value.get(key), list):
            raise ProjectV2ValidationError(f"集合文件格式无效：{relative.as_posix()}")
        if relative.as_posix() == "manuscript/index.json":
            _validate_chapter_index(project_root, value[key])

    return ProjectV2Descriptor(
        root=project_root,
        project_id=str(project_id),
        name=name.strip(),
        author=author.strip(),
        created_at=created_at,
    )


def _read_object(path: Path, label: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectV2ValidationError(f"{label}无法安全读取：{path}") from exc
    if not isinstance(payload, dict):
        raise ProjectV2ValidationError(f"{label}必须是 JSON 对象。")
    return payload


def _validate_chapter_index(root: Path, chapters: list) -> None:
    seen: set[str] = set()
    for sequence, item in enumerate(chapters, start=1):
        if not isinstance(item, dict):
            raise ProjectV2ValidationError("正文索引条目必须是对象。")
        chapter_id = item.get("id")
        expected_id = f"chapter_{sequence:04d}"
        if chapter_id != expected_id or chapter_id in seen:
            raise ProjectV2ValidationError("正文索引 ID 必须连续且唯一。")
        seen.add(chapter_id)
        if item.get("sequence") != sequence:
            raise ProjectV2ValidationError("正文索引顺序无效。")
        if not isinstance(item.get("title"), str) or not item["title"].strip():
            raise ProjectV2ValidationError("正文章节标题不能为空。")
        expected_path = f"manuscript/{chapter_id}.md"
        chapter_path = root / expected_path
        if item.get("path") != expected_path or not chapter_path.is_file():
            raise ProjectV2ValidationError("正文索引路径无效或文件缺失。")
        for digest_key in ("source_sha256", "content_sha256"):
            value = item.get(digest_key)
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ProjectV2ValidationError(f"正文索引的 {digest_key} 无效。")
