"""Read-only legacy-project import for the DeepSonder-PySide6 series."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from core.project import NovelProject
from core.project_data import sanitize_filename
from core.storage import atomic_write_text

from .manuscript_import_service import (
    ManuscriptImportPlan,
    ManuscriptImportService,
)


class LegacyProjectImportError(ValueError):
    """Raised when a legacy source cannot be copied into a new safe project."""


@dataclass(frozen=True)
class LegacyProjectImportPreview:
    plan: ManuscriptImportPlan
    suggested_name: str
    suggested_author: str


class LegacyProjectImportService:
    """Create a new PySide6 project from legacy metadata and chapter Markdown."""

    def __init__(self, scanner: ManuscriptImportService | None = None) -> None:
        self.scanner = scanner or ManuscriptImportService()

    def preview(self, source: Path | str) -> LegacyProjectImportPreview:
        plan = self.scanner.scan(source)
        if plan.source_kind != "novalist_v1_manuscript":
            raise LegacyProjectImportError("请选择旧版 Novalist 项目文件夹。")
        return LegacyProjectImportPreview(
            plan,
            plan.suggested_name,
            plan.suggested_author,
        )

    def create_project(
        self,
        parent_directory: Path | str,
        preview: LegacyProjectImportPreview,
        *,
        name: str,
        author: str = "",
    ) -> NovelProject:
        parent = Path(parent_directory).expanduser().resolve()
        if not parent.is_dir():
            raise NotADirectoryError(parent)
        display_name = str(name or "").strip()
        display_author = str(author or "").strip()
        if not display_name:
            raise LegacyProjectImportError("项目名称不能为空。")
        if len(display_name) > 200 or len(display_author) > 500:
            raise LegacyProjectImportError("项目名称或作者字段过长。")
        safe_name = sanitize_filename(display_name)
        if not safe_name:
            raise LegacyProjectImportError("项目名称无法生成安全目录名。")

        source_root = Path(preview.plan.source_root).resolve()
        refreshed = self.preview(source_root)
        if refreshed.plan.digest != preview.plan.digest:
            raise LegacyProjectImportError("旧项目正文在确认期间发生变化，请重新导入。")

        root = (parent / safe_name).resolve()
        if root.exists():
            raise FileExistsError(root)
        if self._is_relative_to(root, source_root):
            raise LegacyProjectImportError("新项目不能创建在旧项目文件夹内部。")

        staging = parent / f".deepsonder-pyside6-import-{uuid4().hex}"
        try:
            project = NovelProject.create(staging, display_name, display_author)
            for path in project.list_chapters():
                path.unlink()
            for chapter in refreshed.plan.chapters:
                title = " ".join(chapter.title.split())[:200] or f"第{chapter.sequence}章"
                body = chapter.content.rstrip("\n")
                rendered = f"# {title}\n\n## 大纲\n\n## 正文\n{body}\n"
                atomic_write_text(
                    project.chapters_dir / f"{chapter.chapter_id}.md",
                    rendered,
                    encoding="utf-8",
                )
            staging.replace(root)
            return NovelProject(root)
        except Exception:
            if staging.is_dir():
                shutil.rmtree(staging, ignore_errors=True)
            raise

    @staticmethod
    def _is_relative_to(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False
