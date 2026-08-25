"""Unified project data access for UI and application services."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
from uuid import uuid4

from .foreshadowing import ForeshadowingStore
from .project import NovelProject
from .storage import atomic_write_text


def sanitize_filename(value: str) -> str:
    """Return a stable filename stem safe for the supported platforms."""
    forbidden = '<>:"/\\|?*'
    cleaned = "".join("_" if char in forbidden else char for char in str(value)).strip(" .")
    return cleaned or "untitled"


def chapter_id_exists(project: NovelProject, chapter_id: str) -> bool:
    """Return whether a chapter id is occupied, case-insensitively."""
    normalized = sanitize_filename(chapter_id).casefold()
    return any(path.stem.casefold() == normalized for path in project.list_chapters())


def next_available_chapter_id(
    project: NovelProject,
    preferred: str | None = None,
) -> str:
    """Return a non-conflicting chapter id for a project.

    The default id is based on the largest canonical chapter number, not the
    number of files. This remains stable when chapters were deleted or when a
    project contains custom/imported chapter ids.
    """
    existing = {path.stem.casefold() for path in project.list_chapters()}
    if preferred is None:
        numeric_ids = []
        for path in project.list_chapters():
            match = re.fullmatch(r"chapter[_-]?(\d+)", path.stem, re.IGNORECASE)
            if match:
                numeric_ids.append(int(match.group(1)))
        base = f"chapter_{max(numeric_ids, default=0) + 1:02d}"
    else:
        base = sanitize_filename(preferred)

    candidate = base
    suffix = 2
    while candidate.casefold() in existing:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


class ChapterIdConflictError(FileExistsError):
    """Raised when a requested chapter id already belongs to another file."""

    def __init__(self, chapter_id: str, suggested_id: str, path: Path):
        self.chapter_id = str(chapter_id)
        self.suggested_id = str(suggested_id)
        self.path = Path(path)
        super().__init__(
            f"章节 ID 已存在：{self.chapter_id}。可使用：{self.suggested_id}。"
        )


@dataclass(frozen=True)
class TrashEntry:
    """Metadata for one chapter stored in the project recycle bin."""

    trash_id: str
    chapter_id: str
    title: str
    original_path: str
    deleted_at: str
    has_summary: bool
    path: Path


class ProjectDataStore:
    """Facade around project persistence and common read-only projections.

    ``NovelProject`` remains the compatible low-level model. New UI and service
    code should use this facade so file layout, fallbacks and multi-file writes
    stay in one place.
    """

    def __init__(self, project: NovelProject):
        self.project = project

    @property
    def name(self) -> str:
        return self.project.name

    @property
    def root(self) -> Path:
        return self.project.root

    @property
    def trash_dir(self) -> Path:
        return self.root / ".novalist" / "trash"

    def list_chapters(self) -> list[Path]:
        return self.project.list_chapters()

    def list_characters(self) -> list[Path]:
        return self.project.list_characters()

    def list_world(self) -> list[Path]:
        return self.project.list_world()

    def list_power(self) -> list[Path]:
        return self.project.list_power()

    def load_chapter(self, chapter_id: str):
        return self.project.load_chapter(chapter_id)

    def find_related_canon(self, chapter_id: str, **options):
        return self.project.find_related_canon(chapter_id, **options)

    def load_story_state(self) -> dict:
        return self.project.load_story_state()

    def load_chapter_summaries(self) -> dict:
        return self.project.load_chapter_summaries()

    def load_foreshadowing(self, *, status: str | None = None) -> list[dict]:
        return ForeshadowingStore(self.project).list_notes(status=status)

    def create_foreshadowing(self, title: str, **fields) -> dict:
        return ForeshadowingStore(self.project).create_note(title, **fields)

    def update_foreshadowing(self, note_id: str, **changes) -> dict:
        return ForeshadowingStore(self.project).update_note(note_id, **changes)

    def delete_foreshadowing(self, note_id: str):
        return ForeshadowingStore(self.project).delete_note(note_id)

    def list_foreshadowing_trash(self):
        return ForeshadowingStore(self.project).list_trash()

    def restore_foreshadowing(self, trash_id: str) -> dict:
        return ForeshadowingStore(self.project).restore_trash_item(trash_id)

    def delete_foreshadowing_trash(self, trash_id: str) -> None:
        ForeshadowingStore(self.project).delete_trash_item(trash_id)

    def load_main_arc(self) -> str:
        return self.project.load_main_arc()

    def load_future_plan(self) -> str:
        return self.project.load_future_plan()

    def save_story_state(self, state: dict) -> None:
        self.project.save_story_state(state)

    def save_chapter_summaries(self, summaries: dict) -> None:
        self.project.save_chapter_summaries(summaries)

    def save_chapter_summary(self, chapter_id: str, summary: str) -> None:
        summaries = self.load_chapter_summaries()
        summaries[chapter_id] = str(summary or "").strip()
        self.save_chapter_summaries(summaries)

    def write_new_file(self, path: Path, text: str) -> None:
        """Create one new project file without allowing accidental overwrite."""
        path = Path(path)
        root = self.root.resolve()
        target = path.resolve()
        if root not in target.parents:
            raise ValueError("目标文件必须位于当前项目目录内。")
        if path.exists():
            raise FileExistsError(path)
        self.project.write_file(path, text)

    def move_chapter_to_trash(self, chapter_id: str) -> TrashEntry:
        """Move one chapter and its summary into the project recycle bin."""
        raw_id = str(chapter_id or "").strip()
        path = self.project.chapters_dir / f"{raw_id}.md"
        if (
            not raw_id
            or Path(raw_id).name != raw_id
            or path.resolve().parent != self.project.chapters_dir.resolve()
        ):
            raise ValueError("只能删除当前项目章节目录内的章节文件。")
        if not path.is_file():
            raise FileNotFoundError(path)

        original_text = self.project.read_file(path)
        chapter = self.project.load_chapter(raw_id)
        old_summaries = deepcopy(self.load_chapter_summaries())
        new_summaries = deepcopy(old_summaries)
        had_summary = raw_id in new_summaries
        new_summaries.pop(raw_id, None)
        deleted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        trash_id = (
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
            f"_{raw_id}_{uuid4().hex[:8]}"
        )
        entry_path = self.trash_dir / trash_id
        entry = TrashEntry(
            trash_id=trash_id,
            chapter_id=raw_id,
            title=chapter.title,
            original_path=str(path.relative_to(self.root)),
            deleted_at=deleted_at,
            has_summary=had_summary,
            path=entry_path,
        )
        try:
            entry_path.mkdir(parents=True, exist_ok=False)
            atomic_write_text(entry_path / "chapter.md", original_text)
            atomic_write_text(
                entry_path / "summary.json",
                json.dumps(
                    old_summaries.get(raw_id) if had_summary else None,
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            atomic_write_text(
                entry_path / "manifest.json",
                json.dumps(
                    {
                        "trash_id": entry.trash_id,
                        "chapter_id": entry.chapter_id,
                        "title": entry.title,
                        "original_path": entry.original_path,
                        "deleted_at": entry.deleted_at,
                        "has_summary": entry.has_summary,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            path.unlink()
            if had_summary:
                self.save_chapter_summaries(new_summaries)
        except Exception:
            try:
                if not path.exists():
                    self.project.write_file(path, original_text)
                if had_summary:
                    self.save_chapter_summaries(old_summaries)
            except Exception:
                pass
            self._remove_trash_path(entry_path)
            raise
        return entry

    def delete_chapter(self, chapter_id: str) -> Path:
        """Compatibility wrapper: move the chapter into the recycle bin."""
        entry = self.move_chapter_to_trash(chapter_id)
        return self.root / entry.original_path

    def list_trash(self) -> list[TrashEntry]:
        """Return valid recycle-bin entries, newest first."""
        if not self.trash_dir.is_dir():
            return []
        entries: list[TrashEntry] = []
        for path in self.trash_dir.iterdir():
            if not path.is_dir():
                continue
            try:
                entries.append(self._read_trash_entry(path))
            except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
                continue
        return sorted(entries, key=lambda item: item.deleted_at, reverse=True)

    def restore_trash_item(
        self,
        trash_id: str,
        *,
        conflict_policy: str = "error",
    ) -> Path:
        """Restore one recycle-bin entry to its original or a new chapter path."""
        if conflict_policy not in {"error", "rename"}:
            raise ValueError("无效的章节恢复冲突策略。")
        entry = self._read_trash_entry(self._trash_entry_path(trash_id))
        target = self.root / entry.original_path
        if (
            target.resolve().parent != self.project.chapters_dir.resolve()
            or target.name != f"{entry.chapter_id}.md"
        ):
            raise ValueError("回收站条目不是有效的章节路径。")
        restored_id = entry.chapter_id
        if target.exists() or chapter_id_exists(self.project, entry.chapter_id):
            if conflict_policy != "rename":
                raise ChapterIdConflictError(
                    entry.chapter_id,
                    next_available_chapter_id(self.project, entry.chapter_id),
                    target,
                )
            restored_id = next_available_chapter_id(self.project, entry.chapter_id)
            target = self.project.chapters_dir / f"{restored_id}.md"

        old_summaries = deepcopy(self.load_chapter_summaries())
        chapter_text = (entry.path / "chapter.md").read_text(encoding="utf-8")
        try:
            self.project.write_file(target, chapter_text)
            if entry.has_summary:
                summary = json.loads((entry.path / "summary.json").read_text(encoding="utf-8"))
                summaries = deepcopy(old_summaries)
                summaries[restored_id] = summary
                self.save_chapter_summaries(summaries)
            self._remove_trash_path(entry.path)
        except Exception:
            try:
                if target.exists():
                    target.unlink()
                self.save_chapter_summaries(old_summaries)
            except Exception:
                pass
            raise
        return target

    def delete_trash_item(self, trash_id: str) -> None:
        """Permanently remove one validated recycle-bin entry."""
        entry_path = self._trash_entry_path(trash_id)
        self._read_trash_entry(entry_path)
        self._remove_trash_path(entry_path)

    def _trash_entry_path(self, trash_id: str) -> Path:
        raw_id = str(trash_id or "").strip()
        path = self.trash_dir / raw_id
        if not raw_id or Path(raw_id).name != raw_id or path.resolve().parent != self.trash_dir.resolve():
            raise ValueError("无效的回收站条目。")
        if not path.is_dir():
            raise FileNotFoundError(path)
        return path

    @staticmethod
    def _remove_trash_path(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path)

    def _read_trash_entry(self, path: Path) -> TrashEntry:
        manifest_path = Path(path) / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        required = ("trash_id", "chapter_id", "title", "original_path", "deleted_at", "has_summary")
        if any(key not in data for key in required):
            raise ValueError("回收站条目元数据不完整。")
        if str(data["trash_id"]) != Path(path).name:
            raise ValueError("回收站条目标识不一致。")
        return TrashEntry(
            trash_id=str(data["trash_id"]),
            chapter_id=str(data["chapter_id"]),
            title=str(data["title"]),
            original_path=str(data["original_path"]),
            deleted_at=str(data["deleted_at"]),
            has_summary=bool(data["has_summary"]),
            path=Path(path),
        )

    def commit_memory_update(
        self,
        chapter_id: str,
        summary: str,
        state: dict,
    ) -> None:
        """Persist summary and state together with best-effort rollback."""
        old_summaries = deepcopy(self.load_chapter_summaries())
        old_state = deepcopy(self.load_story_state())
        new_summaries = deepcopy(old_summaries)
        new_summaries[chapter_id] = str(summary or "").strip()
        try:
            self.save_chapter_summaries(new_summaries)
            self.save_story_state(state)
        except Exception:
            try:
                self.save_chapter_summaries(old_summaries)
                self.save_story_state(old_state)
            except Exception:
                # Preserve the original failure; callers still receive a clear
                # error while the next load can surface any rollback issue.
                pass
            raise

    def read_text(self, path: Path, *, default: str = "") -> str:
        try:
            return self.project.read_file(Path(path))
        except (OSError, UnicodeError):
            return default

    def chapter_display_name(self, path: Path) -> str:
        path = Path(path)
        first_line = self.read_text(path).splitlines()
        if first_line and first_line[0].startswith("# "):
            return first_line[0][2:].strip() or path.stem
        return path.stem

    def total_word_count(self, counter) -> int:
        total = 0
        for path in self.list_chapters():
            total += int(counter(self.read_text(path)))
        return total

    def total_chinese_character_count(self) -> int:
        import re

        return sum(
            len(re.findall(r"[\u3400-\u9fff]", self.read_text(path)))
            for path in self.list_chapters()
        )
