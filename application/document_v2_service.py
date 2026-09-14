"""Revision-safe manuscript operations for schema-v2 projects."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from core.export import strip_markdown
from core.project_v2_schema import ProjectV2Descriptor, validate_project_v2
from core.storage import atomic_write_text

from .document_service import (
    DocumentNotFoundError,
    DocumentRevisionConflict,
    DocumentServiceError,
    DocumentSnapshot,
)
from .manuscript_import_service import ManuscriptImportPlan, ManuscriptImportService


SAVE_JOURNAL = Path(".novalist") / "manuscript-save-journal.json"
MANUSCRIPT_TRASH = Path(".novalist") / "trash" / "manuscript"
APPEND_IMPORT_JOURNAL = Path(".novalist") / "manuscript-import-journal.json"


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


@dataclass(frozen=True)
class ManuscriptTrashItem:
    trash_id: str
    chapter_id: str
    title: str
    sequence: int
    deleted_at: str


@dataclass(frozen=True)
class ManuscriptTrashSnapshot:
    items: tuple[ManuscriptTrashItem, ...]


@dataclass(frozen=True)
class ManuscriptExportResult:
    path: str
    format: str
    chapter_count: int
    character_count: int
    sha256: str


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

    def create_chapter(
        self,
        project: ProjectV2Descriptor,
        title: str,
        *,
        after_chapter_id: str | None = None,
    ) -> tuple[ManuscriptSnapshot, DocumentSnapshot]:
        clean_title = self._clean_title(title)
        index = self._read_index(project.root)
        chapter_id = self._next_chapter_id(project, index)
        relative_path = f"manuscript/{chapter_id}.md"
        chapter_path = project.root / relative_path
        rendered = f"# {clean_title}\n"
        digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        entry = {
            "id": chapter_id,
            "sequence": 0,
            "title": clean_title,
            "path": relative_path,
            "source_name": "manual",
            "source_sha256": digest,
            "content_sha256": digest,
        }
        insert_at = len(index["chapters"])
        if after_chapter_id:
            insert_at = next(
                (position + 1 for position, item in enumerate(index["chapters"])
                 if item.get("id") == after_chapter_id),
                -1,
            )
            if insert_at < 0:
                raise DocumentNotFoundError(f"正文章节不存在：{after_chapter_id}")
        next_index = self._copy_index(index)
        next_index["chapters"].insert(insert_at, entry)
        self._resequence(next_index)
        if chapter_path.exists():
            raise FileExistsError(chapter_path)
        try:
            atomic_write_text(chapter_path, rendered, encoding="utf-8")
            self._write_index(project.root, next_index)
            validate_project_v2(project.root)
        except Exception:
            chapter_path.unlink(missing_ok=True)
            self._write_index(project.root, index)
            raise
        return self.snapshot(project), self.open_document(project, chapter_id)

    def rename_chapter(
        self,
        project: ProjectV2Descriptor,
        chapter_id: str,
        title: str,
        *,
        expected_revision: str | None,
    ) -> tuple[ManuscriptSnapshot, DocumentSnapshot]:
        clean_title = self._clean_title(title)
        index = self._read_index(project.root)
        item = self._find_item(index, chapter_id)
        chapter_path = project.root / item["path"]
        actual_revision = self.revision_for_path(chapter_path)
        if not expected_revision or expected_revision != actual_revision:
            raise DocumentRevisionConflict(chapter_path, expected_revision, actual_revision)
        rendered = self._replace_title(chapter_path.read_text(encoding="utf-8"), clean_title)
        document = self.save_document(
            project,
            chapter_id,
            rendered,
            expected_revision=expected_revision,
        )
        return self.snapshot(project), document

    def reorder_chapters(
        self,
        project: ProjectV2Descriptor,
        chapter_ids: list[str],
    ) -> ManuscriptSnapshot:
        index = self._read_index(project.root)
        current_ids = [str(item.get("id")) for item in index["chapters"]]
        normalized = [str(value or "").strip() for value in chapter_ids]
        if len(normalized) != len(set(normalized)) or set(normalized) != set(current_ids):
            raise ValueError("章节排序必须且只能包含当前全部章节。")
        next_index = self._copy_index(index)
        by_id = {str(item["id"]): item for item in next_index["chapters"]}
        next_index["chapters"] = [by_id[chapter_id] for chapter_id in normalized]
        self._resequence(next_index)
        self._write_index(project.root, next_index)
        try:
            validate_project_v2(project.root)
        except Exception:
            self._write_index(project.root, index)
            raise
        return self.snapshot(project)

    def delete_chapter(
        self,
        project: ProjectV2Descriptor,
        chapter_id: str,
    ) -> tuple[ManuscriptSnapshot, ManuscriptTrashSnapshot, ManuscriptTrashItem]:
        index = self._read_index(project.root)
        item = self._find_item(index, chapter_id)
        chapter_path = project.root / item["path"]
        content = chapter_path.read_text(encoding="utf-8")
        trash_id = f"deleted_{uuid4().hex}"
        deleted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        trash_record = {
            "schema_version": 1,
            "trash_id": trash_id,
            "deleted_at": deleted_at,
            "chapter": self._copy_value(item),
            "content": content,
        }
        trash_path = project.root / MANUSCRIPT_TRASH / f"{trash_id}.json"
        trash_path.parent.mkdir(parents=True, exist_ok=True)
        next_index = self._copy_index(index)
        next_index["chapters"] = [value for value in next_index["chapters"] if value.get("id") != chapter_id]
        self._resequence(next_index)
        self._write_json(trash_path, trash_record)
        try:
            self._write_index(project.root, next_index)
            chapter_path.unlink()
            validate_project_v2(project.root)
        except Exception:
            atomic_write_text(chapter_path, content, encoding="utf-8")
            self._write_index(project.root, index)
            trash_path.unlink(missing_ok=True)
            raise
        deleted = ManuscriptTrashItem(trash_id, chapter_id, str(item["title"]), int(item["sequence"]), deleted_at)
        return self.snapshot(project), self.trash_snapshot(project), deleted

    def trash_snapshot(self, project: ProjectV2Descriptor) -> ManuscriptTrashSnapshot:
        trash_root = project.root / MANUSCRIPT_TRASH
        items: list[ManuscriptTrashItem] = []
        if trash_root.is_dir():
            for path in trash_root.glob("deleted_*.json"):
                record = self._read_trash_record(path)
                chapter = record["chapter"]
                items.append(ManuscriptTrashItem(
                    str(record["trash_id"]), str(chapter["id"]), str(chapter["title"]),
                    int(chapter["sequence"]), str(record["deleted_at"]),
                ))
        items.sort(key=lambda item: item.deleted_at, reverse=True)
        return ManuscriptTrashSnapshot(tuple(items))

    def restore_chapter(
        self,
        project: ProjectV2Descriptor,
        trash_id: str,
    ) -> tuple[ManuscriptSnapshot, ManuscriptTrashSnapshot, DocumentSnapshot]:
        trash_path = self._trash_path(project, trash_id)
        record = self._read_trash_record(trash_path)
        entry = self._copy_value(record["chapter"])
        content = str(record["content"])
        index = self._read_index(project.root)
        chapter_id = str(entry["id"])
        if any(item.get("id") == chapter_id for item in index["chapters"]):
            raise FileExistsError(f"章节 {chapter_id} 已存在，无法恢复。")
        chapter_path = project.root / str(entry["path"])
        if chapter_path.exists():
            raise FileExistsError(chapter_path)
        next_index = self._copy_index(index)
        insert_at = max(0, min(int(entry.get("sequence", 1)) - 1, len(next_index["chapters"])))
        next_index["chapters"].insert(insert_at, entry)
        self._resequence(next_index)
        try:
            atomic_write_text(chapter_path, content, encoding="utf-8")
            self._write_index(project.root, next_index)
            validate_project_v2(project.root)
            trash_path.unlink()
        except Exception:
            chapter_path.unlink(missing_ok=True)
            self._write_index(project.root, index)
            raise
        return self.snapshot(project), self.trash_snapshot(project), self.open_document(project, chapter_id)

    def delete_trash_forever(
        self,
        project: ProjectV2Descriptor,
        trash_id: str,
    ) -> ManuscriptTrashSnapshot:
        path = self._trash_path(project, trash_id)
        self._read_trash_record(path)
        path.unlink()
        return self.trash_snapshot(project)

    def append_import(
        self,
        project: ProjectV2Descriptor,
        plan: ManuscriptImportPlan,
        *,
        after_chapter_id: str | None = None,
    ) -> tuple[ManuscriptSnapshot, DocumentSnapshot]:
        self._validate_import_plan(plan)
        index_path = project.root / "manuscript" / "index.json"
        imports_path = project.root / "provenance" / "imports.json"
        index = self._read_index(project.root)
        imports = self._read_imports(imports_path)
        existing_sources = {
            str(item.get("source_sha256"))
            for item in index["chapters"]
            if isinstance(item, dict) and isinstance(item.get("source_sha256"), str)
        }
        duplicates: list[str] = []
        seen_sources = set(existing_sources)
        for chapter in plan.chapters:
            if chapter.source_sha256 in seen_sources:
                duplicates.append(chapter.source_name)
            seen_sources.add(chapter.source_sha256)
        if duplicates:
            preview = "、".join(duplicates[:5])
            suffix = "等" if len(duplicates) > 5 else ""
            raise ValueError(f"检测到已经导入的正文：{preview}{suffix}。请移除重复来源后重新扫描。")
        insert_at = len(index["chapters"])
        if after_chapter_id:
            insert_at = next(
                (position + 1 for position, item in enumerate(index["chapters"])
                 if item.get("id") == after_chapter_id),
                -1,
            )
            if insert_at < 0:
                raise DocumentNotFoundError(f"正文章节不存在：{after_chapter_id}")

        next_index = self._copy_index(index)
        next_number = int(self._next_chapter_id(project, index).removeprefix("chapter_"))
        created_paths: list[Path] = []
        rendered_files: list[tuple[Path, str]] = []
        entries: list[dict] = []
        for offset, chapter in enumerate(plan.chapters):
            chapter_id = f"chapter_{next_number + offset:04d}"
            relative_path = f"manuscript/{chapter_id}.md"
            rendered = f"# {chapter.title}\n\n{chapter.content}" if chapter.content else f"# {chapter.title}\n"
            if not rendered.endswith("\n"):
                rendered += "\n"
            entry = {
                "id": chapter_id,
                "sequence": 0,
                "title": chapter.title,
                "path": relative_path,
                "source_name": chapter.source_name,
                "source_sha256": chapter.source_sha256,
                "content_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            }
            entries.append(entry)
            target = project.root / relative_path
            created_paths.append(target)
            rendered_files.append((target, rendered))
        next_index["chapters"][insert_at:insert_at] = entries
        self._resequence(next_index)

        imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        next_imports = self._copy_value(imports)
        next_imports["imports"].append({
            "import_id": str(uuid4()),
            "source_kind": plan.source_kind,
            "plan_digest": plan.digest,
            "chapter_count": len(plan.chapters),
            "source_bytes": plan.total_source_bytes,
            "imported_at": imported_at,
            "mode": "append",
            "insert_after": after_chapter_id or "",
            "chapter_ids": [item["id"] for item in entries],
        })
        next_index_text = json.dumps(next_index, ensure_ascii=False, indent=2) + "\n"
        next_imports_text = json.dumps(next_imports, ensure_ascii=False, indent=2) + "\n"
        occupied = next((path for path in created_paths if path.exists()), None)
        if occupied is not None:
            raise FileExistsError(occupied)
        journal_path = project.root / APPEND_IMPORT_JOURNAL
        journal = {
            "schema_version": 1,
            "original_index": base64.b64encode(index_path.read_bytes()).decode("ascii"),
            "original_imports": base64.b64encode(imports_path.read_bytes()).decode("ascii"),
            "created_paths": [path.relative_to(project.root).as_posix() for path in created_paths],
            "new_index_sha256": hashlib.sha256(next_index_text.encode("utf-8")).hexdigest(),
            "new_imports_sha256": hashlib.sha256(next_imports_text.encode("utf-8")).hexdigest(),
            "created_sha256": {
                path.relative_to(project.root).as_posix(): hashlib.sha256(text.encode("utf-8")).hexdigest()
                for path, text in rendered_files
            },
        }
        self._write_json(journal_path, journal)
        try:
            for target, rendered in rendered_files:
                atomic_write_text(target, rendered, encoding="utf-8")
            atomic_write_text(index_path, next_index_text, encoding="utf-8")
            atomic_write_text(imports_path, next_imports_text, encoding="utf-8")
            validate_project_v2(project.root)
            journal_path.unlink()
        except Exception:
            self.recover_interrupted_import(project.root, force_rollback=True)
            raise
        first_id = str(entries[0]["id"])
        return self.snapshot(project), self.open_document(project, first_id)

    def export_manuscript(
        self,
        project: ProjectV2Descriptor,
        destination: Path | str,
        *,
        format_name: str,
    ) -> ManuscriptExportResult:
        normalized_format = str(format_name or "").strip().casefold()
        if normalized_format not in {"md", "txt"}:
            raise ValueError("导出格式必须是 md 或 txt。")
        output = Path(destination).expanduser().resolve()
        if output.suffix.casefold() != f".{normalized_format}":
            raise ValueError(f"导出文件必须使用 .{normalized_format} 扩展名。")
        manuscript_root = (project.root / "manuscript").resolve()
        if output == manuscript_root or output.is_relative_to(manuscript_root):
            raise ValueError("导出文件不能覆盖项目正文目录。")
        chapters = self.snapshot(project).chapters
        if not chapters:
            raise ValueError("当前项目没有可导出的正文。")
        plain = normalized_format == "txt"
        blocks = [project.name if plain else f"# {project.name}"]
        toc_title = "目录" if plain else "## 目录"
        blocks.append(toc_title + "\n\n" + "\n".join(
            f"{position}. {item.title}" for position, item in enumerate(chapters, start=1)
        ))
        for item in chapters:
            raw = self.open_document(project, item.chapter_id).content.strip()
            blocks.append(strip_markdown(raw).strip() if plain else raw)
        separator = "\n\n***\n\n" if not plain else "\n\n\n"
        rendered = separator.join(blocks).strip() + "\n"
        atomic_write_text(output, rendered, encoding="utf-8-sig")
        return ManuscriptExportResult(
            path=str(output),
            format=normalized_format,
            chapter_count=len(chapters),
            character_count=len(rendered.replace("\ufeff", "")),
            sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        )

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
    def recover_interrupted_import(cls, root: Path | str, *, force_rollback: bool = False) -> bool:
        project_root = Path(root).expanduser().resolve()
        journal_path = project_root / APPEND_IMPORT_JOURNAL
        if not journal_path.is_file():
            return False
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            if not isinstance(journal, dict) or journal.get("schema_version") != 1:
                raise ValueError("追加导入日志格式无效。")
            created = journal.get("created_paths")
            created_hashes = journal.get("created_sha256")
            if not isinstance(created, list) or not isinstance(created_hashes, dict):
                raise ValueError("追加导入日志缺少文件清单。")
            paths: list[Path] = []
            for value in created:
                relative = Path(str(value))
                target = (project_root / relative).resolve()
                if relative.is_absolute() or ".." in relative.parts or target.parent != (project_root / "manuscript").resolve():
                    raise ValueError("追加导入日志包含越界路径。")
                paths.append(target)
            index_path = project_root / "manuscript" / "index.json"
            imports_path = project_root / "provenance" / "imports.json"
            committed = (
                not force_rollback
                and cls._file_hash(index_path) == journal.get("new_index_sha256")
                and cls._file_hash(imports_path) == journal.get("new_imports_sha256")
                and all(cls._file_hash(path) == created_hashes.get(path.relative_to(project_root).as_posix()) for path in paths)
            )
            if not committed:
                for path in paths:
                    path.unlink(missing_ok=True)
                atomic_write_text(
                    index_path,
                    base64.b64decode(journal["original_index"], validate=True).decode("utf-8"),
                    encoding="utf-8",
                )
                atomic_write_text(
                    imports_path,
                    base64.b64decode(journal["original_imports"], validate=True).decode("utf-8"),
                    encoding="utf-8",
                )
            journal_path.unlink()
            return True
        except Exception as exc:
            raise DocumentServiceError("无法恢复中断的正文追加导入事务。") from exc

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

    @staticmethod
    def _read_imports(path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DocumentServiceError("正文导入来源记录无法安全读取。") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("imports"), list):
            raise DocumentServiceError("正文导入来源记录格式无效。")
        return value

    @staticmethod
    def _validate_import_plan(plan: ManuscriptImportPlan) -> None:
        if not plan.chapters:
            raise ValueError("正文导入计划不能为空。")
        for chapter in plan.chapters:
            if hashlib.sha256(chapter.content.encode("utf-8")).hexdigest() != chapter.content_sha256:
                raise ValueError("正文导入计划已发生变化，请重新扫描。")
        if ManuscriptImportService.plan_digest(plan.source_kind, plan.chapters) != plan.digest:
            raise ValueError("正文导入计划摘要无效，请重新扫描。")

    @staticmethod
    def _write_index(root: Path, value: dict) -> None:
        DocumentV2Service._write_json(root / "manuscript" / "index.json", value)

    @staticmethod
    def _write_json(path: Path, value: dict) -> None:
        atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _copy_value(value):
        return json.loads(json.dumps(value, ensure_ascii=False))

    @classmethod
    def _copy_index(cls, index: dict) -> dict:
        return cls._copy_value(index)

    @staticmethod
    def _resequence(index: dict) -> None:
        for sequence, item in enumerate(index["chapters"], start=1):
            item["sequence"] = sequence

    @classmethod
    def _next_chapter_id(cls, project: ProjectV2Descriptor, index: dict) -> str:
        identifiers = [str(item.get("id", "")) for item in index["chapters"]]
        trash_root = project.root / MANUSCRIPT_TRASH
        if trash_root.is_dir():
            for path in trash_root.glob("deleted_*.json"):
                try:
                    identifiers.append(str(cls._read_trash_record(path)["chapter"]["id"]))
                except DocumentServiceError:
                    continue
        numbers = [int(value.removeprefix("chapter_")) for value in identifiers
                   if value.startswith("chapter_") and value.removeprefix("chapter_").isdigit()]
        return f"chapter_{max(numbers, default=0) + 1:04d}"

    @staticmethod
    def _clean_title(title: str) -> str:
        clean = " ".join(str(title or "").split())
        if not clean:
            raise ValueError("章节标题不能为空。")
        if len(clean) > 200:
            raise ValueError("章节标题不能超过 200 个字符。")
        return clean

    @staticmethod
    def _replace_title(content: str, title: str) -> str:
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.splitlines()
        if lines and lines[0].startswith("# "):
            lines[0] = f"# {title}"
            rendered = "\n".join(lines)
        else:
            rendered = f"# {title}\n\n{normalized.lstrip()}"
        return rendered.rstrip("\n") + "\n"

    @staticmethod
    def _trash_path(project: ProjectV2Descriptor, trash_id: str) -> Path:
        identifier = str(trash_id or "").strip()
        if not identifier.startswith("deleted_") or not identifier.removeprefix("deleted_").isalnum():
            raise DocumentServiceError("回收站条目标识无效。")
        return project.root / MANUSCRIPT_TRASH / f"{identifier}.json"

    @staticmethod
    def _read_trash_record(path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DocumentServiceError("回收站条目不存在或无法读取。") from exc
        chapter = value.get("chapter") if isinstance(value, dict) else None
        if (
            not isinstance(value, dict) or value.get("schema_version") != 1
            or value.get("trash_id") != path.stem or not isinstance(value.get("deleted_at"), str)
            or not isinstance(value.get("content"), str) or not isinstance(chapter, dict)
            or not isinstance(chapter.get("id"), str) or not isinstance(chapter.get("title"), str)
        ):
            raise DocumentServiceError("回收站条目格式无效。")
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
