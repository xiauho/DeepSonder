"""Transactional creation of Electron-first schema-v2 projects."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from core.project_data import sanitize_filename
from core.project_v2_schema import (
    PROJECT_V2_COLLECTIONS,
    PROJECT_V2_DIRECTORIES,
    PROJECT_V2_MANIFEST,
    PROJECT_V2_METADATA,
    ProjectV2Descriptor,
    empty_collection,
    project_metadata,
    schema_manifest,
    validate_project_v2,
)
from core.storage import atomic_write_text
from core.version import load_current_version

from .manuscript_import_service import ManuscriptImportPlan, ManuscriptImportService


class ProjectV2Service:
    """Create a v2 project without altering an import source."""

    def create_project(
        self,
        parent_directory: Path | str,
        name: str,
        *,
        author: str = "",
        import_plan: ManuscriptImportPlan | None = None,
    ) -> ProjectV2Descriptor:
        parent = Path(parent_directory).expanduser().resolve()
        if not parent.is_dir():
            raise NotADirectoryError(parent)
        display_name = str(name or "").strip()
        if not display_name:
            raise ValueError("项目名称不能为空。")
        if len(display_name) > 200 or len(str(author or "")) > 500:
            raise ValueError("项目名称或作者字段过长。")
        safe_name = sanitize_filename(display_name)
        if not safe_name:
            raise ValueError("项目名称无法生成安全目录名。")
        root = parent / safe_name
        if root.exists():
            raise FileExistsError(root)
        if import_plan is not None:
            self._validate_plan(import_plan)

        staging = parent / f".novalist-v2-create-{uuid4().hex}"
        try:
            staging.mkdir(parents=False, exist_ok=False)
            for relative in PROJECT_V2_DIRECTORIES:
                (staging / relative).mkdir(parents=True, exist_ok=True)

            created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            project_id = str(uuid4())
            self._write_json(
                staging / PROJECT_V2_METADATA,
                project_metadata(
                    project_id=project_id,
                    name=display_name,
                    author=str(author or "").strip(),
                    created_at=created_at,
                ),
            )
            self._write_json(
                staging / PROJECT_V2_MANIFEST,
                schema_manifest(created_by=str(load_current_version())),
            )

            for relative, key in PROJECT_V2_COLLECTIONS.items():
                if relative.as_posix() in {"manuscript/index.json", "provenance/imports.json"}:
                    continue
                collection = empty_collection(key)
                if relative.as_posix() == "knowledge/world_rules.json":
                    collection["hidden_rules"] = []
                elif relative.as_posix() == "knowledge/timeline.json":
                    collection["hidden_events"] = []
                    collection["diagnostics"] = []
                self._write_json(staging / relative, collection)
            self._write_json(
                staging / "provenance" / "evidence.json",
                empty_collection("evidence"),
            )
            self._write_json(
                staging / "knowledge" / "curation.json",
                {
                    "schema_version": 1,
                    "entity_overrides": {},
                    "entity_merges": {},
                    "relation_overrides": {},
                    "character_field_overrides": {},
                    "world_overrides": {},
                    "event_overrides": {},
                    "event_order": [],
                    "operations": [],
                },
            )
            self._write_json(
                staging / "provenance" / "curation_log.json",
                empty_collection("operations"),
            )

            chapters = self._write_manuscript(staging, import_plan)
            self._write_json(
                staging / "manuscript" / "index.json",
                {"schema_version": 1, "chapters": chapters},
            )
            imports = [] if import_plan is None else [{
                "import_id": str(uuid4()),
                "source_kind": import_plan.source_kind,
                "plan_digest": import_plan.digest,
                "chapter_count": len(import_plan.chapters),
                "source_bytes": import_plan.total_source_bytes,
                "imported_at": created_at,
            }]
            self._write_json(
                staging / "provenance" / "imports.json",
                {"schema_version": 1, "imports": imports},
            )

            validate_project_v2(staging)
            staging.replace(root)
            return validate_project_v2(root)
        except Exception:
            if staging.is_dir():
                shutil.rmtree(staging, ignore_errors=True)
            raise

    @staticmethod
    def open_project(path: Path | str) -> ProjectV2Descriptor:
        from .document_v2_service import DocumentV2Service
        from .reconstruction_service import ReconstructionService

        DocumentV2Service.recover_interrupted_save(path)
        DocumentV2Service.recover_interrupted_import(path)
        project = validate_project_v2(path)
        ReconstructionService().reconcile(project)
        return validate_project_v2(path)

    @classmethod
    def _write_manuscript(
        cls,
        staging: Path,
        plan: ManuscriptImportPlan | None,
    ) -> list[dict]:
        if plan is None:
            return []
        entries: list[dict] = []
        for chapter in plan.chapters:
            relative = Path("manuscript") / f"{chapter.chapter_id}.md"
            rendered = f"# {chapter.title}\n\n{chapter.content}" if chapter.content else f"# {chapter.title}\n"
            atomic_write_text(staging / relative, rendered, encoding="utf-8")
            entries.append({
                "id": chapter.chapter_id,
                "sequence": chapter.sequence,
                "title": chapter.title,
                "path": relative.as_posix(),
                "source_name": chapter.source_name,
                "source_sha256": chapter.source_sha256,
                "content_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            })
        return entries

    @staticmethod
    def _validate_plan(plan: ManuscriptImportPlan) -> None:
        if not plan.chapters:
            raise ValueError("正文导入计划不能为空。")
        expected_ids = [f"chapter_{index:04d}" for index in range(1, len(plan.chapters) + 1)]
        if [chapter.chapter_id for chapter in plan.chapters] != expected_ids:
            raise ValueError("正文导入计划中的章节 ID 不连续。")
        for chapter in plan.chapters:
            digest = hashlib.sha256(chapter.content.encode("utf-8")).hexdigest()
            if digest != chapter.content_sha256:
                raise ValueError("正文导入计划已发生变化，请重新扫描。")
        if ManuscriptImportService.plan_digest(plan.source_kind, plan.chapters) != plan.digest:
            raise ValueError("正文导入计划摘要无效，请重新扫描。")

    @staticmethod
    def _write_json(path: Path, value: dict) -> None:
        atomic_write_text(
            path,
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
