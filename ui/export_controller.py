"""Manuscript preview and export operations for the desktop UI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject

from core.export import render_manuscript
from core.project import NovelProject
from core.project_data import ProjectDataStore
from core.storage import atomic_write_text
from ui.project_session import ProjectSession


@dataclass(frozen=True)
class ExportResult:
    path: Path
    chapter_count: int
    format: str


class ExportController(QObject):
    """Own export validation, rendering and durable file writing."""

    def __init__(self, project_session: ProjectSession, parent=None):
        super().__init__(parent)
        self.project_session = project_session

    @property
    def project(self) -> NovelProject | None:
        return self.project_session.project

    def selected_chapters(self, options: dict) -> list[Path]:
        project = self._require_project()
        selected_ids = {str(item) for item in options.get("chapter_ids", [])}
        return [
            path
            for path in ProjectDataStore(project).list_chapters()
            if path.stem in selected_ids
        ]

    def normalize_options(self, options: dict, format_name: str | None = None) -> dict:
        normalized = dict(options)
        normalized["format"] = "txt" if format_name == "txt" or options.get("format") == "txt" else "md"
        normalized["chapter_ids"] = [
            str(item) for item in options.get("chapter_ids", [])
        ]
        for key in ("include_title", "include_toc", "separators", "strip"):
            normalized[key] = bool(options.get(key, False))
        return normalized

    def render(self, options: dict) -> str:
        project = self._require_project()
        normalized = self.normalize_options(options)
        return render_manuscript(project, normalized)

    def suggested_path(self, options: dict) -> Path:
        project = self._require_project()
        format_name = self.normalize_options(options)["format"]
        return project.root / f"{self.safe_name(project.name)}-全书.{format_name}"

    def export(
        self,
        options: dict,
        output: Path,
        *,
        format_name: str | None = None,
    ) -> ExportResult:
        normalized = self.normalize_options(options, format_name)
        chapters = self.selected_chapters(normalized)
        if not chapters:
            raise ValueError("请至少选择一个章节。")
        text = self.render(normalized)
        if not text:
            raise ValueError("所选章节没有可导出的内容。")
        output = Path(output)
        atomic_write_text(output, text + "\n", encoding="utf-8-sig")
        return ExportResult(
            path=output,
            chapter_count=len(chapters),
            format=normalized["format"],
        )

    def _require_project(self) -> NovelProject:
        project = self.project
        if project is None:
            raise RuntimeError("当前没有打开的项目。")
        return project

    @staticmethod
    def safe_name(value: str) -> str:
        forbidden = '<>:"/\\|?*'
        cleaned = "".join("_" if char in forbidden else char for char in value).strip(" .")
        return cleaned or "untitled"
