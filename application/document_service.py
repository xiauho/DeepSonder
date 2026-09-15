"""Conflict-safe project document operations without UI dependencies."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Iterable

from core.project import NovelProject
from core.project_data import (
    ChapterIdConflictError,
    ProjectDataStore,
    chapter_id_exists,
    next_available_chapter_id,
    normalize_canon_entry_kind,
    sanitize_filename,
)
from core.storage import atomic_write_text


class DocumentServiceError(RuntimeError):
    """Base error for document operations exposed through an application API."""


class DocumentNotFoundError(DocumentServiceError, FileNotFoundError):
    pass


class DocumentPathError(DocumentServiceError, ValueError):
    pass


class DocumentRevisionConflict(DocumentServiceError):
    """Raised when a save would overwrite a different on-disk revision."""

    def __init__(self, path: Path, expected: str | None, actual: str | None):
        self.path = Path(path)
        self.expected_revision = expected
        self.actual_revision = actual
        super().__init__(f"文件已在外部发生变化：{self.path}")


@dataclass(frozen=True)
class DocumentSnapshot:
    path: str
    relative_path: str
    category: str
    title: str
    content: str
    revision: str


@dataclass(frozen=True)
class DocumentMutation:
    """One project-data mutation and paths that dependent views must refresh."""

    result_path: str
    changed_paths: tuple[str, ...]
    kind: str


class DocumentService:
    """Authoritative read/write boundary for editable project documents."""

    REVISION_PREFIX = "v1"

    def open_document(
        self,
        project: NovelProject,
        category: str,
        path: Path | str,
    ) -> DocumentSnapshot:
        resolved = self._resolve_editable_path(project, path, require_exists=True)
        content, revision = self._read_consistent_text(resolved)
        return self._snapshot(project, resolved, category, content, revision)

    def save_document(
        self,
        project: NovelProject,
        category: str,
        path: Path | str,
        content: str,
        *,
        expected_revision: str | None,
        force: bool = False,
    ) -> DocumentSnapshot:
        resolved = self._resolve_editable_path(project, path, require_exists=True)
        actual = self.revision_for_path(resolved)
        if not force and (not expected_revision or expected_revision != actual):
            raise DocumentRevisionConflict(resolved, expected_revision, actual)
        atomic_write_text(resolved, str(content), encoding="utf-8")
        saved_content, revision = self._read_consistent_text(resolved)
        return self._snapshot(project, resolved, category, saved_content, revision)

    def next_chapter_id(self, project: NovelProject) -> str:
        return next_available_chapter_id(project)

    def create_chapter(
        self,
        project: NovelProject,
        store: ProjectDataStore,
        title: str,
        chapter_id: str,
    ) -> DocumentMutation:
        title = str(title or "").strip()
        raw_chapter_id = str(chapter_id or "").strip()
        if not title or not raw_chapter_id:
            raise ValueError("章节标题和文件标识不能为空。")
        safe_id = sanitize_filename(raw_chapter_id)
        path = project.chapters_dir / f"{safe_id}.md"
        if chapter_id_exists(project, safe_id):
            raise ChapterIdConflictError(
                safe_id,
                next_available_chapter_id(project, safe_id),
                path,
            )
        store.write_new_file(
            path,
            f"# {title}\n\n## 大纲\n- 本章目标：\n- 核心冲突：\n- 章节钩子：\n\n"
            "## 剧情简写\n\n\n## 正文\n\n",
        )
        return self._mutation(path, (path,), "chapter")

    def create_canon_entry(
        self,
        project: NovelProject,
        store: ProjectDataStore,
        kind: str,
        title: str,
    ) -> DocumentMutation:
        entry_kind = normalize_canon_entry_kind(kind)
        path = store.create_canon_entry(entry_kind, title)
        return self._mutation(path, (path,), "canon")

    def create_timeline(
        self,
        project: NovelProject,
        store: ProjectDataStore,
    ) -> DocumentMutation:
        path = store.create_timeline()
        return self._mutation(path, (path,), "canon")

    def delete_chapter(
        self,
        project: NovelProject,
        store: ProjectDataStore,
        chapter_id: str,
    ) -> DocumentMutation:
        target = project.chapters_dir / f"{str(chapter_id).strip()}.md"
        result = store.delete_chapter(chapter_id)
        return self._mutation(
            result,
            (target, project.memory_dir / "chapter_summaries.json"),
            "chapter",
        )

    def delete_character(
        self,
        project: NovelProject,
        store: ProjectDataStore,
        character_id: str,
    ) -> DocumentMutation:
        raw_id = str(character_id or "").strip()
        target = project.canon_dir / "characters" / f"{raw_id}.md"
        result = store.delete_character(raw_id)
        return self._mutation(result, (target,), "canon")

    def delete_canon_entry(
        self,
        project: NovelProject,
        store: ProjectDataStore,
        kind: str,
        path: Path | str,
    ) -> DocumentMutation:
        entry_kind = normalize_canon_entry_kind(kind)
        if entry_kind == "character":
            raise ValueError("角色卡请使用角色卡专用删除接口。")
        target = Path(path)
        if not target.is_absolute():
            target = project.root / target
        result = store.delete_canon_entry(entry_kind, target)
        changed = [target]
        if entry_kind == "power":
            changed.append(store.system_registry_path)
        return self._mutation(result, changed, "canon")

    def import_markdown(
        self,
        project: NovelProject,
        store: ProjectDataStore,
        sources: Iterable[Path | str],
    ) -> tuple[DocumentMutation, ...]:
        imported: list[DocumentMutation] = []
        for source_value in sources:
            source = Path(source_value)
            stem = sanitize_filename(source.stem) or "imported_chapter"
            chapter_id = next_available_chapter_id(project, stem)
            destination = project.chapters_dir / f"{chapter_id}.md"
            text = self._read_import_text(source)
            if not text.lstrip().startswith("# "):
                text = f"# {source.stem}\n\n## 正文\n\n{text.strip()}\n"
            store.write_new_file(destination, text)
            imported.append(self._mutation(destination, (destination,), "chapter"))
        return tuple(imported)

    @classmethod
    def revision_for_path(cls, path: Path | str) -> str | None:
        candidate = Path(path)
        try:
            stat = candidate.stat()
            payload = candidate.read_bytes()
        except OSError:
            return None
        digest = hashlib.sha256(payload).hexdigest()
        return (
            f"{cls.REVISION_PREFIX}:{stat.st_mtime_ns}:{stat.st_ctime_ns}:"
            f"{stat.st_size}:{digest}"
        )

    @classmethod
    def _read_consistent_text(cls, path: Path) -> tuple[str, str]:
        for _attempt in range(2):
            try:
                before = path.stat()
                payload = path.read_bytes()
                after = path.stat()
            except FileNotFoundError as exc:
                raise DocumentNotFoundError(f"文件不存在：{path}") from exc
            except OSError as exc:
                raise DocumentServiceError(f"无法读取文件：{path}") from exc
            before_key = (before.st_mtime_ns, before.st_ctime_ns, before.st_size)
            after_key = (after.st_mtime_ns, after.st_ctime_ns, after.st_size)
            if before_key != after_key or len(payload) != after.st_size:
                continue
            try:
                content = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise DocumentServiceError(f"文件不是有效的 UTF-8 文本：{path}") from exc
            digest = hashlib.sha256(payload).hexdigest()
            revision = (
                f"{cls.REVISION_PREFIX}:{after.st_mtime_ns}:{after.st_ctime_ns}:"
                f"{after.st_size}:{digest}"
            )
            return content, revision
        raise DocumentServiceError(f"读取期间文件持续发生变化：{path}")

    @staticmethod
    def _read_import_text(source: Path) -> str:
        try:
            return source.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            return source.read_text(encoding="gb18030")

    @classmethod
    def _resolve_editable_path(
        cls,
        project: NovelProject,
        path: Path | str,
        *,
        require_exists: bool,
    ) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = project.root / candidate
        try:
            resolved = candidate.resolve()
            relative = resolved.relative_to(project.root.resolve())
        except (OSError, RuntimeError, ValueError) as exc:
            raise DocumentPathError("文档路径不属于当前项目。") from exc
        if not cls._is_editable_relative_path(relative):
            raise DocumentPathError(f"该文件不是可编辑的项目文档：{relative.as_posix()}")
        if require_exists and not resolved.is_file():
            raise DocumentNotFoundError(f"文件不存在：{resolved}")
        return resolved

    @staticmethod
    def _is_editable_relative_path(path: Path) -> bool:
        parts = path.parts
        if path.as_posix() in {
            "writing/style_guide.md",
            "outline/main_arc.md",
            "outline/future_plan.md",
            "canon/timeline.md",
        }:
            return True
        if len(parts) == 3 and parts[0:2] == ("outline", "chapters"):
            return path.suffix.casefold() == ".md"
        if (
            len(parts) == 3
            and parts[0] == "canon"
            and parts[1] in {"characters", "world", "power"}
        ):
            return path.suffix.casefold() == ".md"
        return False

    @staticmethod
    def _title(content: str, path: Path) -> str:
        first_line = str(content).splitlines()[0] if str(content).splitlines() else ""
        return first_line[2:].strip() if first_line.startswith("# ") else path.stem

    @classmethod
    def _snapshot(
        cls,
        project: NovelProject,
        path: Path,
        category: str,
        content: str,
        revision: str,
    ) -> DocumentSnapshot:
        return DocumentSnapshot(
            path=str(path),
            relative_path=path.relative_to(project.root).as_posix(),
            category=str(category or ""),
            title=cls._title(content, path),
            content=content,
            revision=revision,
        )

    @staticmethod
    def _mutation(
        result_path: Path | str,
        changed_paths: Iterable[Path | str],
        kind: str,
    ) -> DocumentMutation:
        return DocumentMutation(
            result_path=str(result_path),
            changed_paths=tuple(str(path) for path in changed_paths),
            kind=kind,
        )

