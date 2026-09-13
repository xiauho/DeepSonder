"""Revision-safe manuscript operations for schema-v2 projects."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from core.project_v2_schema import ProjectV2Descriptor, validate_project_v2
from core.storage import atomic_write_text

from .document_service import (
    DocumentNotFoundError,
    DocumentRevisionConflict,
    DocumentServiceError,
    DocumentSnapshot,
)


SAVE_JOURNAL = Path(".novalist") / "manuscript-save-journal.json"


@dataclass(frozen=True)
class ManuscriptItem:
    chapter_id: str
    sequence: int
    title: str
    path: str
    relative_path: str


@dataclass(frozen=True)
class ManuscriptSnapshot:
    chapters: tuple[ManuscriptItem, ...]
    item_count: int


class DocumentV2Service:
    REVISION_PREFIX = "v2"

    def snapshot(self, project: ProjectV2Descriptor) -> ManuscriptSnapshot:
        index = self._read_index(project.root)
        chapters = tuple(
            ManuscriptItem(
                chapter_id=item["id"],
                sequence=item["sequence"],
                title=item["title"],
                path=str((project.root / item["path"]).resolve()),
                relative_path=item["path"],
            )
            for item in index["chapters"]
        )
        return ManuscriptSnapshot(chapters=chapters, item_count=len(chapters))

    def open_document(
        self,
        project: ProjectV2Descriptor,
        chapter_id: str,
    ) -> DocumentSnapshot:
        item = self._item(project, chapter_id)
        path = project.root / item["path"]
        content, revision = self._read_consistent_text(path)
        return DocumentSnapshot(
            path=str(path.resolve()),
            relative_path=item["path"],
            category="正文",
            title=item["title"],
            content=content,
            revision=revision,
        )

    def save_document(
        self,
        project: ProjectV2Descriptor,
        chapter_id: str,
        content: str,
        *,
        expected_revision: str | None,
        force: bool = False,
    ) -> DocumentSnapshot:
        index_path = project.root / "manuscript" / "index.json"
        index = self._read_index(project.root)
        item = self._find_item(index, chapter_id)
        chapter_path = project.root / item["path"]
        actual_revision = self.revision_for_path(chapter_path)
        if not force and (not expected_revision or expected_revision != actual_revision):
            raise DocumentRevisionConflict(chapter_path, expected_revision, actual_revision)

        rendered = str(content).replace("\r\n", "\n").replace("\r", "\n")
        if rendered and not rendered.endswith("\n"):
            rendered += "\n"
        original_chapter = chapter_path.read_bytes()
        original_index = index_path.read_bytes()
        title = self._title(rendered, item["title"])
        next_index = json.loads(json.dumps(index, ensure_ascii=False))
        next_item = self._find_item(next_index, chapter_id)
        next_item["title"] = title
        next_item["content_sha256"] = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        next_index_text = json.dumps(next_index, ensure_ascii=False, indent=2) + "\n"

        journal_path = project.root / SAVE_JOURNAL
        journal = {
            "schema_version": 1,
            "chapter_id": chapter_id,
            "chapter_path": item["path"],
            "original_chapter": base64.b64encode(original_chapter).decode("ascii"),
            "original_index": base64.b64encode(original_index).decode("ascii"),
            "new_chapter_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "new_index_sha256": hashlib.sha256(next_index_text.encode("utf-8")).hexdigest(),
        }
        atomic_write_text(journal_path, json.dumps(journal, ensure_ascii=False, indent=2) + "\n")
        try:
            atomic_write_text(chapter_path, rendered, encoding="utf-8")
            atomic_write_text(index_path, next_index_text, encoding="utf-8")
            validate_project_v2(project.root)
            journal_path.unlink()
        except Exception:
            self.recover_interrupted_save(project.root)
            raise
        return self.open_document(project, chapter_id)

    @classmethod
    def recover_interrupted_save(cls, root: Path | str) -> bool:
        project_root = Path(root).expanduser().resolve()
        journal_path = project_root / SAVE_JOURNAL
        if not journal_path.is_file():
            return False
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            if not isinstance(journal, dict) or journal.get("schema_version") != 1:
                raise ValueError("保存日志格式无效。")
            relative = Path(str(journal.get("chapter_path") or ""))
            if (
                relative.as_posix() != f"manuscript/{journal.get('chapter_id')}.md"
                or relative.is_absolute()
                or ".." in relative.parts
            ):
                raise ValueError("保存日志中的章节路径无效。")
            chapter_path = (project_root / relative).resolve()
            manuscript_root = (project_root / "manuscript").resolve()
            if chapter_path.parent != manuscript_root:
                raise ValueError("保存日志中的章节路径越界。")
            index_path = manuscript_root / "index.json"
            if (
                cls._file_hash(chapter_path) == str(journal.get("new_chapter_sha256") or "")
                and cls._file_hash(index_path) == str(journal.get("new_index_sha256") or "")
            ):
                journal_path.unlink()
                return True
            chapter_bytes = base64.b64decode(journal["original_chapter"], validate=True)
            index_bytes = base64.b64decode(journal["original_index"], validate=True)
            atomic_write_text(chapter_path, chapter_bytes.decode("utf-8"), encoding="utf-8")
            atomic_write_text(index_path, index_bytes.decode("utf-8"), encoding="utf-8")
            journal_path.unlink()
            return True
        except Exception as exc:
            raise DocumentServiceError("无法恢复中断的正文保存事务。") from exc

    @classmethod
    def revision_for_path(cls, path: Path | str) -> str | None:
        candidate = Path(path)
        try:
            stat = candidate.stat()
            payload = candidate.read_bytes()
        except OSError:
            return None
        return f"{cls.REVISION_PREFIX}:{stat.st_mtime_ns}:{stat.st_size}:{hashlib.sha256(payload).hexdigest()}"

    @classmethod
    def _read_consistent_text(cls, path: Path) -> tuple[str, str]:
        for _attempt in range(2):
            try:
                before = path.stat()
                payload = path.read_bytes()
                after = path.stat()
            except FileNotFoundError as exc:
                raise DocumentNotFoundError(f"正文不存在：{path}") from exc
            except OSError as exc:
                raise DocumentServiceError(f"无法读取正文：{path}") from exc
            if (
                (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)
                and len(payload) == after.st_size
            ):
                try:
                    content = payload.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise DocumentServiceError(f"正文不是有效 UTF-8：{path}") from exc
                digest = hashlib.sha256(payload).hexdigest()
                return content, f"{cls.REVISION_PREFIX}:{after.st_mtime_ns}:{after.st_size}:{digest}"
        raise DocumentServiceError(f"读取期间正文持续变化：{path}")

    @staticmethod
    def _read_index(root: Path) -> dict:
        try:
            value = json.loads((root / "manuscript" / "index.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DocumentServiceError("正文索引无法安全读取。") from exc
        if not isinstance(value, dict) or not isinstance(value.get("chapters"), list):
            raise DocumentServiceError("正文索引格式无效。")
        return value

    @classmethod
    def _item(cls, project: ProjectV2Descriptor, chapter_id: str) -> dict:
        return cls._find_item(cls._read_index(project.root), chapter_id)

    @staticmethod
    def _find_item(index: dict, chapter_id: str) -> dict:
        identifier = str(chapter_id or "").strip()
        for item in index["chapters"]:
            if isinstance(item, dict) and item.get("id") == identifier:
                return item
        raise DocumentNotFoundError(f"正文章节不存在：{identifier}")

    @staticmethod
    def _title(content: str, fallback: str) -> str:
        first = content.splitlines()[0] if content.splitlines() else ""
        return first[2:].strip() if first.startswith("# ") and first[2:].strip() else fallback

    @staticmethod
    def _file_hash(path: Path) -> str:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return ""
