"""Author-owned foreshadowing notes and their project-local recycle bin."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .project import NovelProject
from .storage import atomic_write_text

FORESHADOWING_VERSION = 1
VALID_STATUSES = {"open", "resolved", "abandoned"}
VALID_PRIORITIES = {"low", "medium", "high"}
NOTE_FIELDS = {
    "title",
    "note",
    "first_seen_chapter",
    "planned_resolution_chapter",
    "recent_seen_chapter",
    "resolved_chapter",
    "status",
    "priority",
    "related_characters",
    "tags",
    "appearances",
}


@dataclass(frozen=True)
class ForeshadowingTrashEntry:
    """Metadata for one deleted foreshadowing note."""

    trash_id: str
    foreshadowing_id: str
    title: str
    deleted_at: str
    path: Path


class ForeshadowingStore:
    """Persist author-owned foreshadowing notes independently from story state.

    The store deliberately does not perform AI discovery or full-text scans.
    Notes are created and edited by the author; future AI workflows may apply
    validated, task-scoped appearance/resolution events to these records.
    """

    def __init__(self, project: NovelProject):
        self.project = project

    @property
    def path(self) -> Path:
        return self.project.memory_dir / "foreshadowing.json"

    @property
    def trash_dir(self) -> Path:
        return self.project.root / ".novalist" / "trash" / "foreshadowing"

    def load_notes(self) -> list[dict]:
        """Load notes, migrating the legacy story-state list once if needed."""
        if not self.path.exists():
            notes = self._migrate_legacy_notes()
            self.save_notes(notes)
            return notes

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"伏笔笔记文件无法读取：{self.path}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ValueError("伏笔笔记文件格式无效，缺少 items 数组。")
        return self._normalize_notes(data["items"])

    def save_notes(self, notes: list[dict]) -> None:
        normalized = self._normalize_notes(notes)
        atomic_write_text(
            self.path,
            json.dumps(
                {"version": FORESHADOWING_VERSION, "items": normalized},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def list_notes(self, *, status: str | None = None) -> list[dict]:
        notes = self.load_notes()
        if status is None:
            return notes
        if status not in VALID_STATUSES:
            raise ValueError(f"无效的伏笔状态：{status}")
        return [note for note in notes if note["status"] == status]

    def get_note(self, note_id: str) -> dict | None:
        raw_id = self._validate_id(note_id)
        return next(
            (note for note in self.load_notes() if note["id"] == raw_id),
            None,
        )

    def create_note(
        self,
        title: str,
        *,
        note: str = "",
        first_seen_chapter: str = "",
        planned_resolution_chapter: str = "",
        priority: str = "medium",
        related_characters: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        title = str(title or "").strip()
        if not title:
            raise ValueError("伏笔标题不能为空。")
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"无效的伏笔优先级：{priority}")

        now = _now()
        note_data = {
            "id": f"f-{uuid4().hex[:12]}",
            "title": title,
            "note": str(note or "").strip(),
            "first_seen_chapter": str(first_seen_chapter or "").strip(),
            "planned_resolution_chapter": str(planned_resolution_chapter or "").strip(),
            "recent_seen_chapter": "",
            "resolved_chapter": "",
            "status": "open",
            "priority": priority,
            "related_characters": _string_list(related_characters),
            "tags": _string_list(tags),
            "appearances": [],
            "created_at": now,
            "updated_at": now,
        }
        notes = self.load_notes()
        notes.append(note_data)
        self.save_notes(notes)
        return deepcopy(note_data)

    def update_note(self, note_id: str, **changes) -> dict:
        raw_id = self._validate_id(note_id)
        unknown = set(changes) - NOTE_FIELDS
        if unknown:
            raise ValueError(f"不允许修改伏笔字段：{', '.join(sorted(unknown))}")

        notes = self.load_notes()
        target = next((note for note in notes if note["id"] == raw_id), None)
        if target is None:
            raise KeyError(raw_id)
        updated = deepcopy(target)
        for key, value in changes.items():
            if key in {"title", "note", "first_seen_chapter", "planned_resolution_chapter", "recent_seen_chapter", "resolved_chapter"}:
                updated[key] = str(value or "").strip()
            elif key in {"related_characters", "tags"}:
                updated[key] = _string_list(value)
            elif key == "appearances":
                updated[key] = _appearance_list(value)
            elif key == "status":
                if value not in VALID_STATUSES:
                    raise ValueError(f"无效的伏笔状态：{value}")
                updated[key] = value
            elif key == "priority":
                if value not in VALID_PRIORITIES:
                    raise ValueError(f"无效的伏笔优先级：{value}")
                updated[key] = value
        if not updated["title"]:
            raise ValueError("伏笔标题不能为空。")
        if updated["status"] != "resolved":
            updated["resolved_chapter"] = ""
        updated["updated_at"] = _now()
        notes[notes.index(target)] = updated
        self.save_notes(notes)
        return deepcopy(updated)

    def resolve_notes(self, note_ids: list[str] | tuple[str, ...], chapter_id: str) -> list[dict]:
        """Mark existing open notes as resolved in one idempotent file write."""
        raw_chapter_id = str(chapter_id or "").strip()
        if not raw_chapter_id or Path(raw_chapter_id).name != raw_chapter_id:
            raise ValueError("无效的回收章节 ID。")
        resolved_ids: list[str] = []
        seen: set[str] = set()
        for note_id in note_ids:
            raw_id = self._validate_id(note_id)
            if raw_id not in seen:
                seen.add(raw_id)
                resolved_ids.append(raw_id)
        if not resolved_ids:
            return []

        notes = self.load_notes()
        by_id = {note["id"]: note for note in notes}
        missing = [note_id for note_id in resolved_ids if note_id not in by_id]
        if missing:
            raise KeyError(missing[0])

        now = _now()
        changed: list[dict] = []
        for note_id in resolved_ids:
            note = by_id[note_id]
            if note["status"] != "open":
                continue
            note["status"] = "resolved"
            note["resolved_chapter"] = raw_chapter_id
            note["updated_at"] = now
            changed.append(deepcopy(note))
        if changed:
            self.save_notes(notes)
        return changed

    def delete_note(self, note_id: str) -> ForeshadowingTrashEntry:
        """Soft-delete one note into the foreshadowing recycle-bin area."""
        raw_id = self._validate_id(note_id)
        notes = self.load_notes()
        target = next((note for note in notes if note["id"] == raw_id), None)
        if target is None:
            raise KeyError(raw_id)

        deleted_at = _now()
        trash_id = f"foreshadowing_{uuid4().hex[:16]}"
        entry_path = self.trash_dir / trash_id
        entry = ForeshadowingTrashEntry(
            trash_id=trash_id,
            foreshadowing_id=raw_id,
            title=target["title"],
            deleted_at=deleted_at,
            path=entry_path,
        )
        remaining = [note for note in notes if note["id"] != raw_id]
        try:
            entry_path.mkdir(parents=True, exist_ok=False)
            atomic_write_text(
                entry_path / "foreshadowing.json",
                json.dumps(target, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            atomic_write_text(
                entry_path / "manifest.json",
                json.dumps(
                    {
                        "kind": "foreshadowing",
                        "trash_id": entry.trash_id,
                        "foreshadowing_id": entry.foreshadowing_id,
                        "title": entry.title,
                        "deleted_at": entry.deleted_at,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            self.save_notes(remaining)
        except Exception:
            self._remove_trash_path(entry_path)
            raise
        return entry

    def list_trash(self) -> list[ForeshadowingTrashEntry]:
        if not self.trash_dir.is_dir():
            return []
        entries: list[ForeshadowingTrashEntry] = []
        for path in self.trash_dir.iterdir():
            if not path.is_dir():
                continue
            try:
                entries.append(self._read_trash_entry(path))
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                continue
        return sorted(entries, key=lambda item: item.deleted_at, reverse=True)

    def restore_trash_item(self, trash_id: str) -> dict:
        entry = self._read_trash_entry(self._trash_entry_path(trash_id))
        note = json.loads((entry.path / "foreshadowing.json").read_text(encoding="utf-8"))
        notes = self.load_notes()
        if any(item["id"] == entry.foreshadowing_id for item in notes):
            raise FileExistsError(f"伏笔 ID 已存在：{entry.foreshadowing_id}")
        notes.append(note)
        self.save_notes(notes)
        self._remove_trash_path(entry.path)
        return deepcopy(note)

    def delete_trash_item(self, trash_id: str) -> None:
        entry_path = self._trash_entry_path(trash_id)
        self._read_trash_entry(entry_path)
        self._remove_trash_path(entry_path)

    def _migrate_legacy_notes(self) -> list[dict]:
        state = self.project.load_story_state()
        legacy = state.get("foreshadowing", []) if isinstance(state, dict) else []
        notes: list[dict] = []
        for item in legacy if isinstance(legacy, list) else []:
            title = str(item or "").strip()
            if not title:
                continue
            stable_id = f"legacy-{hashlib.sha256(title.encode('utf-8')).hexdigest()[:12]}"
            notes.append(
                {
                    "id": stable_id,
                    "title": title,
                    "note": "由旧版故事状态迁移，首次出现章节待作者补充。",
                    "first_seen_chapter": "",
                    "planned_resolution_chapter": "",
                    "recent_seen_chapter": "",
                    "resolved_chapter": "",
                    "status": "open",
                    "priority": "medium",
                    "related_characters": [],
                    "tags": ["legacy"],
                    "appearances": [],
                    "created_at": _now(),
                    "updated_at": _now(),
                }
            )
        return self._normalize_notes(notes)

    def _normalize_notes(self, notes: object) -> list[dict]:
        if not isinstance(notes, list):
            raise ValueError("伏笔笔记 items 必须是数组。")
        normalized = []
        seen: set[str] = set()
        for raw in notes:
            if not isinstance(raw, dict):
                continue
            note = _normalize_note(raw)
            if note["id"] in seen:
                raise ValueError(f"伏笔 ID 重复：{note['id']}")
            seen.add(note["id"])
            normalized.append(note)
        return normalized

    def _trash_entry_path(self, trash_id: str) -> Path:
        raw_id = str(trash_id or "").strip()
        path = self.trash_dir / raw_id
        if not raw_id or Path(raw_id).name != raw_id or path.resolve().parent != self.trash_dir.resolve():
            raise ValueError("无效的伏笔回收站条目。")
        if not path.is_dir():
            raise FileNotFoundError(path)
        return path

    def _read_trash_entry(self, path: Path) -> ForeshadowingTrashEntry:
        data = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        required = ("kind", "trash_id", "foreshadowing_id", "title", "deleted_at")
        if any(key not in data for key in required) or data["kind"] != "foreshadowing":
            raise ValueError("伏笔回收站条目元数据不完整。")
        if str(data["trash_id"]) != path.name:
            raise ValueError("伏笔回收站条目标识不一致。")
        return ForeshadowingTrashEntry(
            trash_id=str(data["trash_id"]),
            foreshadowing_id=str(data["foreshadowing_id"]),
            title=str(data["title"]),
            deleted_at=str(data["deleted_at"]),
            path=path,
        )

    @staticmethod
    def _validate_id(note_id: str) -> str:
        raw_id = str(note_id or "").strip()
        if not raw_id or Path(raw_id).name != raw_id:
            raise ValueError("无效的伏笔 ID。")
        return raw_id

    @staticmethod
    def _remove_trash_path(path: Path) -> None:
        if path.is_dir():
            import shutil

            shutil.rmtree(path)


def _normalize_note(raw: dict) -> dict:
    note = {
        "id": str(raw.get("id") or "").strip(),
        "title": str(raw.get("title") or "").strip(),
        "note": str(raw.get("note") or "").strip(),
        "first_seen_chapter": str(raw.get("first_seen_chapter") or "").strip(),
        "planned_resolution_chapter": str(raw.get("planned_resolution_chapter") or "").strip(),
        "recent_seen_chapter": str(raw.get("recent_seen_chapter") or "").strip(),
        "resolved_chapter": str(raw.get("resolved_chapter") or "").strip(),
        "status": str(raw.get("status") or "open").strip(),
        "priority": str(raw.get("priority") or "medium").strip(),
        "related_characters": _string_list(raw.get("related_characters")),
        "tags": _string_list(raw.get("tags")),
        "appearances": _appearance_list(raw.get("appearances")),
        "created_at": str(raw.get("created_at") or _now()).strip(),
        "updated_at": str(raw.get("updated_at") or _now()).strip(),
    }
    if not note["id"] or Path(note["id"]).name != note["id"]:
        raise ValueError("伏笔笔记缺少有效 ID。")
    if not note["title"]:
        raise ValueError("伏笔笔记缺少标题。")
    if note["status"] not in VALID_STATUSES:
        raise ValueError(f"无效的伏笔状态：{note['status']}")
    if note["priority"] not in VALID_PRIORITIES:
        raise ValueError(f"无效的伏笔优先级：{note['priority']}")
    return note


def _appearance_list(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        chapter_id = str(item.get("chapter_id") or "").strip()
        if chapter_id:
            result.append(
                {
                    "chapter_id": chapter_id,
                    "note": str(item.get("note") or "").strip(),
                }
            )
    return result


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
