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
from .project import (
    DEFAULT_TIMELINE,
    SYSTEM_REGISTRY_FILENAME,
    NovelProject,
)
from .storage import atomic_write_text


def sanitize_filename(value: str) -> str:
    """Return a stable filename stem safe for the supported platforms."""
    forbidden = '<>:"/\\|?*'
    cleaned = "".join("_" if char in forbidden else char for char in str(value)).strip(" .")
    return cleaned or "untitled"


CANON_ENTRY_TYPES = {
    "character": {
        "label": "角色",
        "directory": "characters",
        "template": (
            "# {title}\n\n"
            "<!-- novalist:character-card:v2 -->\n\n"
            "## 基础档案\n\n"
            "- 姓名：{title}\n"
            "- 别名：\n"
            "- 年龄／出生信息：\n"
            "- 性别：\n"
            "- 身份／阵营：\n"
            "- 首次出场：\n\n"
            "## 外貌与辨识特征\n\n"
            "## 性格、动机与核心欲望\n\n"
            "## 长期目标与人物弧线\n\n"
            "## 能力档案\n\n"
            "### 能力名称\n\n"
            "- 所属体系：\n"
            "- 能力属性：\n"
            "- 当前等级：\n"
            "- 当前数值：\n"
            "- 使用条件：\n"
            "- 限制与代价：\n"
            "- 克制关系：\n\n"
            "## 关键关系\n\n"
            "## 秘密与人物弧线\n\n"
            "## 当前剧情状态（Novalist 同步）\n\n"
            "<!-- novalist:auto-state:v1:start -->\n"
            "- 本次同步截止章节：未记录\n"
            "- 当前状态：未记录\n"
            "- 当前位置：未记录\n"
            "- 当前战力：未记录\n"
            "- 持有物：无\n"
            "- 当前关系：无\n"
            "<!-- novalist:auto-state:v1:end -->\n\n"
            "## 作者自由备注\n"
        ),
    },
    "world": {
        "label": "世界观",
        "directory": "world",
        "template": (
            "# {title}\n\n"
            "## 核心规则\n\n"
            "## 地理与空间\n\n"
            "## 历史与现状\n\n"
            "## 社会、势力与秩序\n\n"
            "## 技术、魔法或特殊现象\n\n"
            "## 对剧情的约束\n\n"
            "## 已知例外与未解问题\n"
        ),
    },
    "power": {
        "label": "体系设定",
        "directory": "power",
        "template": (
            "# {title}\n\n"
            "## 体系定位\n\n"
            "## 等级或层级\n\n"
            "## 核心原则\n\n"
            "## 获取方式\n\n"
            "## 使用限制\n\n"
            "## 代价与副作用\n\n"
            "## 克制关系\n\n"
            "## 对剧情的约束\n\n"
            "## 已知例外\n"
        ),
    },
}


def normalize_canon_entry_kind(kind: str) -> str:
    """Return the stable internal id for a canon entry type."""
    aliases = {
        "character": "character",
        "角色": "character",
        "world": "world",
        "世界观": "world",
        "power": "power",
        "战力": "power",
        "能力体系": "power",
        "体系设定": "power",
        "timeline": "timeline",
        "时间线": "timeline",
    }
    normalized = aliases.get(str(kind or "").strip().casefold())
    if normalized is None:
        raise ValueError("不支持的故事资料类型。")
    return normalized


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


def character_id_exists(project: NovelProject, character_id: str) -> bool:
    """Return whether a character card filename is occupied."""
    normalized = sanitize_filename(character_id).casefold()
    return any(path.stem.casefold() == normalized for path in project.list_characters())


def next_available_character_id(
    project: NovelProject,
    preferred: str,
) -> str:
    """Return a non-conflicting character card filename stem."""
    base = sanitize_filename(preferred)
    existing = {path.stem.casefold() for path in project.list_characters()}
    candidate = base
    suffix = 2
    while candidate.casefold() in existing:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def canon_entry_id_exists(project: NovelProject, kind: str, entry_id: str) -> bool:
    """Return whether a world/power entry id is occupied."""
    normalized_kind = str(kind or "").strip().casefold()
    directory_name = {"world": "world", "power": "power"}.get(normalized_kind)
    if directory_name is None:
        return False
    normalized = sanitize_filename(entry_id).casefold()
    directory = project.canon_dir / directory_name
    return any(path.stem.casefold() == normalized for path in directory.glob("*.md"))


def next_available_canon_entry_id(
    project: NovelProject,
    kind: str,
    preferred: str,
) -> str:
    """Return a non-conflicting id for a world or ordinary power entry."""
    normalized_kind = str(kind or "").strip().casefold()
    if normalized_kind not in {"world", "power"}:
        raise ValueError("只有世界观和普通体系支持改名恢复。")
    base = sanitize_filename(preferred)
    directory = project.canon_dir / normalized_kind
    existing = {path.stem.casefold() for path in directory.glob("*.md")}
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


class CharacterIdConflictError(FileExistsError):
    """Raised when a restored character card name is already occupied."""

    def __init__(self, character_id: str, suggested_id: str, path: Path):
        self.character_id = str(character_id)
        self.suggested_id = str(suggested_id)
        self.path = Path(path)
        super().__init__(
            f"角色卡标识已存在：{self.character_id}。可使用：{self.suggested_id}。"
        )


class CanonEntryConflictError(FileExistsError):
    """Raised when a restored canon entry's destination is occupied."""

    def __init__(
        self,
        kind: str,
        entry_id: str,
        suggested_id: str | None,
        path: Path,
    ):
        self.kind = str(kind)
        self.entry_id = str(entry_id)
        self.suggested_id = str(suggested_id or "")
        self.path = Path(path)
        labels = {"world": "世界观条目", "power": "体系设定", "timeline": "时间线"}
        label = labels.get(self.kind, "故事资料")
        if self.suggested_id:
            message = f"{label}标识已存在：{self.entry_id}。可使用：{self.suggested_id}。"
        else:
            message = f"{label}原始位置已被占用：{self.path.name}。"
        super().__init__(message)


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


@dataclass(frozen=True)
class CharacterTrashEntry:
    """Metadata for one character card stored in the project recycle bin."""

    trash_id: str
    character_id: str
    title: str
    original_path: str
    deleted_at: str
    path: Path


@dataclass(frozen=True)
class CanonTrashEntry:
    """Metadata for a world, power, or timeline document in the recycle bin."""

    trash_id: str
    kind: str
    entry_id: str
    title: str
    original_path: str
    deleted_at: str
    path: Path
    metadata: dict[str, str]


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
    def style_guide_path(self) -> Path:
        return self.project.style_guide_path

    @property
    def trash_dir(self) -> Path:
        return self.root / ".novalist" / "trash"

    def list_chapters(self) -> list[Path]:
        return self.project.list_chapters()

    def list_characters(self) -> list[Path]:
        return self.project.list_characters()

    @property
    def character_trash_dir(self) -> Path:
        return self.trash_dir / "characters"

    @property
    def canon_trash_dir(self) -> Path:
        """Storage for deleted world, power, and timeline documents."""
        return self.trash_dir / "canon"

    def list_world(self) -> list[Path]:
        return self.project.list_world()

    def list_power(self) -> list[Path]:
        return self.project.list_power()

    @property
    def core_power_path(self) -> Path:
        return self.project.core_power_path

    def list_power_entries(self) -> list[Path]:
        """Return selectable systems, excluding the always-on global rules."""
        core_path = self.core_power_path.resolve()
        return [path for path in self.list_power() if path.resolve() != core_path]

    @property
    def system_registry_path(self) -> Path:
        return self.project.canon_dir / SYSTEM_REGISTRY_FILENAME

    def load_system_registry(self) -> dict:
        """Load and normalize author-defined system importance metadata."""
        try:
            data = json.loads(self.system_registry_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            data = {}
        entries = data.get("entries", {}) if isinstance(data, dict) else {}
        normalized: dict[str, dict[str, str]] = {}
        for path in self.list_power_entries():
            key = self._system_key(path)
            item = self._registry_item(entries, key)
            importance = str(item.get("importance", "non_core"))
            system_type = str(item.get("type", "custom"))
            if importance not in {"core", "non_core"}:
                importance = "non_core"
            if system_type not in {"ability", "space", "custom"}:
                system_type = "custom"
            normalized[key] = {"type": system_type, "importance": importance}
        return {"version": 1, "entries": normalized}

    def save_system_registry(self, registry: dict) -> None:
        entries = registry.get("entries", {}) if isinstance(registry, dict) else {}
        payload = {"version": 1, "entries": entries if isinstance(entries, dict) else {}}
        self.project.write_file(
            self.system_registry_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )

    def system_metadata(self, path: Path) -> dict[str, str]:
        key = self._system_key(Path(path))
        return self.load_system_registry().get("entries", {}).get(
            key, {"type": "custom", "importance": "non_core"}
        )

    def is_core_power_path(self, path: Path) -> bool:
        """Return whether a path is the always-on global rules file."""
        return Path(path).resolve() == self.core_power_path.resolve()

    def set_system_importance(self, path: Path, importance: str) -> None:
        importance = {
            "核心": "core",
            "非核心": "non_core",
            "core": "core",
            "non_core": "non_core",
        }.get(str(importance or "").strip().casefold(), "")
        if importance not in {"core", "non_core"}:
            raise ValueError("体系重要性必须是“核心”或“非核心”。")
        path = Path(path).resolve()
        valid_paths = {item.resolve() for item in self.list_power_entries()}
        if path not in valid_paths:
            raise ValueError("只能设置当前项目中的体系设定。")
        registry = self.load_system_registry()
        key = self._system_key(path)
        item = registry["entries"].setdefault(key, {"type": "custom"})
        item["importance"] = importance
        self.save_system_registry(registry)

    def list_core_systems(self) -> list[Path]:
        registry = self.load_system_registry()
        return [
            path
            for path in self.list_power_entries()
            if registry["entries"].get(self._system_key(path), {}).get("importance")
            == "core"
        ]

    def list_non_core_systems(self) -> list[Path]:
        core = set(self.list_core_systems())
        return [path for path in self.list_power_entries() if path not in core]

    def _system_key(self, path: Path) -> str:
        return str(Path(path).resolve().relative_to(self.project.root.resolve())).replace(
            "\\", "/"
        )

    @staticmethod
    def _registry_item(entries: object, key: str) -> dict:
        if not isinstance(entries, dict):
            return {}
        item = entries.get(key)
        if isinstance(item, dict):
            return item
        # Compatibility with the first registry format, which omitted the
        # canon/ prefix from relative paths.
        legacy_key = key.removeprefix("canon/")
        item = entries.get(legacy_key)
        return item if isinstance(item, dict) else {}

    def load_style_guide(self) -> str:
        """Return author-written style rules without template-only comments.

        A newly seeded guide contains headings and HTML comments that help the
        author fill it in. Those hints must not become accidental AI rules.
        """
        raw = self.read_text(self.style_guide_path)
        cleaned = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL).strip()
        substantive = [
            line.strip()
            for line in cleaned.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        return cleaned if substantive else ""

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

    def resolve_foreshadowing(
        self,
        note_ids: list[str] | tuple[str, ...],
        chapter_id: str,
    ) -> list[dict]:
        return ForeshadowingStore(self.project).resolve_notes(note_ids, chapter_id)

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
        path.parent.mkdir(parents=True, exist_ok=True)
        self.project.write_file(path, text)

    def create_canon_entry(self, kind: str, title: str) -> Path:
        """Create a templated canon entry or the singleton timeline."""
        entry_kind = normalize_canon_entry_kind(kind)
        if entry_kind == "timeline":
            return self.create_timeline()
        title = str(title or "").strip()
        if not title:
            label = CANON_ENTRY_TYPES[entry_kind]["label"]
            raise ValueError(f"{label}条目名称不能为空。")

        spec = CANON_ENTRY_TYPES[entry_kind]
        path = self.project.canon_dir / spec["directory"] / f"{sanitize_filename(title)}.md"
        template = str(spec["template"]).format(title=title)
        self.write_new_file(path, template)
        return path

    def create_timeline(self) -> Path:
        """Create the singleton timeline document when it is missing."""
        path = self.project.canon_dir / "timeline.md"
        self.write_new_file(path, DEFAULT_TIMELINE)
        return path

    def move_canon_entry_to_trash(
        self,
        kind: str,
        path_or_id: Path | str,
    ) -> CanonTrashEntry:
        """Move one world, ordinary power, or timeline document to trash.

        Character cards retain their older, compatible trash representation;
        this method covers the remaining canon documents and keeps power
        importance metadata together with the document being deleted.
        """
        entry_kind = normalize_canon_entry_kind(kind)
        if entry_kind == "character":
            raise ValueError("角色卡请使用角色卡专用回收站接口。")
        target = self._resolve_canon_entry_target(entry_kind, path_or_id)
        if entry_kind == "power" and self.is_core_power_path(target):
            raise ValueError("核心规则是项目常驻资料，不能移入回收站。")
        if not target.is_file():
            raise FileNotFoundError(target)

        original_text = self.project.read_file(target)
        metadata: dict[str, str] = {}
        old_registry: dict | None = None
        old_registry_text: str | None = None
        old_registry_exists = False
        if entry_kind == "power":
            metadata = dict(self.system_metadata(target))
            old_registry = deepcopy(self.load_system_registry())
            old_registry_exists = self.system_registry_path.exists()
            try:
                old_registry_text = self.system_registry_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                old_registry_text = None

        deleted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        trash_id = (
            f"canon_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
            f"_{entry_kind}_{sanitize_filename(target.stem)}_{uuid4().hex[:8]}"
        )
        entry_path = self.canon_trash_dir / trash_id
        entry = CanonTrashEntry(
            trash_id=trash_id,
            kind=entry_kind,
            entry_id=target.stem,
            title=self.chapter_display_name(target),
            original_path=str(target.relative_to(self.root)),
            deleted_at=deleted_at,
            path=entry_path,
            metadata=metadata,
        )
        try:
            entry_path.mkdir(parents=True, exist_ok=False)
            atomic_write_text(entry_path / "document.md", original_text)
            atomic_write_text(
                entry_path / "manifest.json",
                json.dumps(
                    {
                        "schema_version": 1,
                        "trash_id": entry.trash_id,
                        "kind": entry.kind,
                        "entry_id": entry.entry_id,
                        "title": entry.title,
                        "original_path": entry.original_path,
                        "deleted_at": entry.deleted_at,
                        "metadata": entry.metadata,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            if entry_kind == "power":
                registry = deepcopy(old_registry or {"version": 1, "entries": {}})
                entries = registry.setdefault("entries", {})
                entries.pop(self._system_key(target), None)
                self.save_system_registry(registry)
            target.unlink()
        except Exception:
            try:
                if not target.exists():
                    self.project.write_file(target, original_text)
                if entry_kind == "power" and old_registry is not None:
                    self._restore_registry_snapshot(old_registry_text, old_registry_exists)
            except Exception:
                pass
            self._remove_trash_path(entry_path)
            raise
        return entry

    def delete_canon_entry(self, kind: str, path_or_id: Path | str) -> Path:
        """Compatibility wrapper returning the original canon path."""
        entry = self.move_canon_entry_to_trash(kind, path_or_id)
        return self.root / entry.original_path

    def delete_world(self, path_or_id: Path | str) -> Path:
        return self.delete_canon_entry("world", path_or_id)

    def delete_power(self, path_or_id: Path | str) -> Path:
        return self.delete_canon_entry("power", path_or_id)

    def delete_timeline(self) -> Path:
        return self.delete_canon_entry("timeline", self.project.canon_dir / "timeline.md")

    def move_world_to_trash(self, path_or_id: Path | str) -> CanonTrashEntry:
        return self.move_canon_entry_to_trash("world", path_or_id)

    def move_power_to_trash(self, path_or_id: Path | str) -> CanonTrashEntry:
        return self.move_canon_entry_to_trash("power", path_or_id)

    def move_timeline_to_trash(self) -> CanonTrashEntry:
        return self.move_canon_entry_to_trash(
            "timeline", self.project.canon_dir / "timeline.md"
        )

    def list_canon_trash(self, kind: str | None = None) -> list[CanonTrashEntry]:
        """Return valid world/power/timeline trash entries, newest first."""
        if kind is not None:
            kind = normalize_canon_entry_kind(kind)
            if kind == "character":
                raise ValueError("角色卡请使用角色卡专用回收站接口。")
        if not self.canon_trash_dir.is_dir():
            return []
        entries: list[CanonTrashEntry] = []
        for path in self.canon_trash_dir.iterdir():
            if not path.is_dir():
                continue
            try:
                entry = self._read_canon_trash_entry(path)
            except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
                continue
            if kind is None or entry.kind == kind:
                entries.append(entry)
        return sorted(entries, key=lambda item: item.deleted_at, reverse=True)

    def list_world_trash(self) -> list[CanonTrashEntry]:
        return self.list_canon_trash("world")

    def list_power_trash(self) -> list[CanonTrashEntry]:
        return self.list_canon_trash("power")

    def list_timeline_trash(self) -> list[CanonTrashEntry]:
        return self.list_canon_trash("timeline")

    def restore_canon_trash_item(
        self,
        trash_id: str,
        *,
        conflict_policy: str = "error",
    ) -> Path:
        """Restore a canon document to its original path or a new id."""
        if conflict_policy not in {"error", "rename"}:
            raise ValueError("无效的故事资料恢复冲突策略。")
        entry = self._read_canon_trash_entry(self._canon_trash_entry_path(trash_id))
        target = self._canon_target_from_entry(entry)
        restored_id = entry.entry_id
        if entry.kind == "timeline":
            if target.exists():
                raise CanonEntryConflictError(entry.kind, entry.entry_id, None, target)
        elif target.exists() or canon_entry_id_exists(self.project, entry.kind, entry.entry_id):
            if conflict_policy != "rename":
                raise CanonEntryConflictError(
                    entry.kind,
                    entry.entry_id,
                    next_available_canon_entry_id(
                        self.project, entry.kind, entry.entry_id
                    ),
                    target,
                )
            restored_id = next_available_canon_entry_id(
                self.project, entry.kind, entry.entry_id
            )
            target = target.parent / f"{restored_id}.md"

        document_text = (entry.path / "document.md").read_text(encoding="utf-8")
        old_registry: dict | None = None
        old_registry_text: str | None = None
        old_registry_exists = False
        if entry.kind == "power":
            old_registry = deepcopy(self.load_system_registry())
            old_registry_exists = self.system_registry_path.exists()
            try:
                old_registry_text = self.system_registry_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                old_registry_text = None
        try:
            self.project.write_file(target, document_text)
            if entry.kind == "power":
                registry = deepcopy(old_registry or {"version": 1, "entries": {}})
                metadata = {
                    "type": str(entry.metadata.get("type", "custom")),
                    "importance": str(entry.metadata.get("importance", "non_core")),
                }
                if metadata["type"] not in {"ability", "space", "custom"}:
                    metadata["type"] = "custom"
                if metadata["importance"] not in {"core", "non_core"}:
                    metadata["importance"] = "non_core"
                registry.setdefault("entries", {})[self._system_key(target)] = metadata
                self.save_system_registry(registry)
            self._remove_trash_path(entry.path)
        except Exception:
            try:
                if target.exists():
                    target.unlink()
                if entry.kind == "power" and old_registry is not None:
                    self._restore_registry_snapshot(old_registry_text, old_registry_exists)
            except Exception:
                pass
            raise
        return target

    def restore_world_trash_item(
        self, trash_id: str, *, conflict_policy: str = "error"
    ) -> Path:
        self._assert_canon_trash_kind(trash_id, "world")
        return self.restore_canon_trash_item(
            trash_id, conflict_policy=conflict_policy
        )

    def restore_power_trash_item(
        self, trash_id: str, *, conflict_policy: str = "error"
    ) -> Path:
        self._assert_canon_trash_kind(trash_id, "power")
        return self.restore_canon_trash_item(
            trash_id, conflict_policy=conflict_policy
        )

    def restore_timeline_trash_item(
        self, trash_id: str, *, conflict_policy: str = "error"
    ) -> Path:
        self._assert_canon_trash_kind(trash_id, "timeline")
        return self.restore_canon_trash_item(
            trash_id, conflict_policy=conflict_policy
        )

    def delete_canon_trash_item(self, trash_id: str) -> None:
        """Permanently remove one validated canon trash entry."""
        entry_path = self._canon_trash_entry_path(trash_id)
        self._read_canon_trash_entry(entry_path)
        self._remove_trash_path(entry_path)

    def _assert_canon_trash_kind(self, trash_id: str, expected: str) -> None:
        entry = self._read_canon_trash_entry(self._canon_trash_entry_path(trash_id))
        if entry.kind != expected:
            raise ValueError("回收站条目类型与操作不匹配。")

    def _restore_registry_snapshot(
        self,
        raw_text: str | None,
        existed: bool,
    ) -> None:
        """Restore the registry bytes when a canon transaction rolls back."""
        if raw_text is not None:
            self.project.write_file(self.system_registry_path, raw_text)
            return
        if not existed and self.system_registry_path.exists():
            self.system_registry_path.unlink()

    def delete_world_trash_item(self, trash_id: str) -> None:
        self._assert_canon_trash_kind(trash_id, "world")
        self.delete_canon_trash_item(trash_id)

    def delete_power_trash_item(self, trash_id: str) -> None:
        self._assert_canon_trash_kind(trash_id, "power")
        self.delete_canon_trash_item(trash_id)

    def delete_timeline_trash_item(self, trash_id: str) -> None:
        self._assert_canon_trash_kind(trash_id, "timeline")
        self.delete_canon_trash_item(trash_id)

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

    def move_character_to_trash(self, character_id: str) -> CharacterTrashEntry:
        """Move a character card to the recycle bin without changing story memory."""
        raw_id = str(character_id or "").strip()
        characters_dir = self.project.canon_dir / "characters"
        path = characters_dir / f"{raw_id}.md"
        if (
            not raw_id
            or Path(raw_id).name != raw_id
            or path.resolve().parent != characters_dir.resolve()
        ):
            raise ValueError("只能删除当前项目角色目录内的角色卡文件。")
        if not path.is_file():
            raise FileNotFoundError(path)

        original_text = self.project.read_file(path)
        deleted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        trash_id = (
            f"character_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
            f"_{raw_id}_{uuid4().hex[:8]}"
        )
        entry_path = self.character_trash_dir / trash_id
        entry = CharacterTrashEntry(
            trash_id=trash_id,
            character_id=raw_id,
            title=self.chapter_display_name(path),
            original_path=str(path.relative_to(self.root)),
            deleted_at=deleted_at,
            path=entry_path,
        )
        try:
            entry_path.mkdir(parents=True, exist_ok=False)
            atomic_write_text(entry_path / "character.md", original_text)
            atomic_write_text(
                entry_path / "manifest.json",
                json.dumps(
                    {
                        "trash_id": entry.trash_id,
                        "character_id": entry.character_id,
                        "title": entry.title,
                        "original_path": entry.original_path,
                        "deleted_at": entry.deleted_at,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            path.unlink()
        except Exception:
            try:
                if not path.exists():
                    self.project.write_file(path, original_text)
            except Exception:
                pass
            self._remove_trash_path(entry_path)
            raise
        return entry

    def delete_character(self, character_id: str) -> Path:
        """Compatibility wrapper: move a character card into the recycle bin."""
        entry = self.move_character_to_trash(character_id)
        return self.root / entry.original_path

    def list_character_trash(self) -> list[CharacterTrashEntry]:
        """Return valid character-card recycle-bin entries, newest first."""
        if not self.character_trash_dir.is_dir():
            return []
        entries: list[CharacterTrashEntry] = []
        for path in self.character_trash_dir.iterdir():
            if not path.is_dir():
                continue
            try:
                entries.append(self._read_character_trash_entry(path))
            except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
                continue
        return sorted(entries, key=lambda item: item.deleted_at, reverse=True)

    def restore_character_trash_item(
        self,
        trash_id: str,
        *,
        conflict_policy: str = "error",
    ) -> Path:
        """Restore one character card to its original or a new card id."""
        if conflict_policy not in {"error", "rename"}:
            raise ValueError("无效的角色卡恢复冲突策略。")
        entry = self._read_character_trash_entry(
            self._character_trash_entry_path(trash_id)
        )
        characters_dir = self.project.canon_dir / "characters"
        target = self.root / entry.original_path
        if (
            target.resolve().parent != characters_dir.resolve()
            or target.name != f"{entry.character_id}.md"
        ):
            raise ValueError("回收站条目不是有效的角色卡路径。")
        restored_id = entry.character_id
        if target.exists() or character_id_exists(self.project, entry.character_id):
            if conflict_policy != "rename":
                raise CharacterIdConflictError(
                    entry.character_id,
                    next_available_character_id(self.project, entry.character_id),
                    target,
                )
            restored_id = next_available_character_id(self.project, entry.character_id)
            target = characters_dir / f"{restored_id}.md"

        card_text = (entry.path / "character.md").read_text(encoding="utf-8")
        try:
            self.project.write_file(target, card_text)
            self._remove_trash_path(entry.path)
        except Exception:
            try:
                if target.exists():
                    target.unlink()
            except Exception:
                pass
            raise
        return target

    def delete_character_trash_item(self, trash_id: str) -> None:
        """Permanently remove one validated character-card trash entry."""
        entry_path = self._character_trash_entry_path(trash_id)
        self._read_character_trash_entry(entry_path)
        self._remove_trash_path(entry_path)

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

    def _character_trash_entry_path(self, trash_id: str) -> Path:
        raw_id = str(trash_id or "").strip()
        path = self.character_trash_dir / raw_id
        if (
            not raw_id
            or Path(raw_id).name != raw_id
            or path.resolve().parent != self.character_trash_dir.resolve()
        ):
            raise ValueError("无效的角色卡回收站条目。")
        if not path.is_dir():
            raise FileNotFoundError(path)
        return path

    def _canon_trash_entry_path(self, trash_id: str) -> Path:
        raw_id = str(trash_id or "").strip()
        path = self.canon_trash_dir / raw_id
        if (
            not raw_id
            or Path(raw_id).name != raw_id
            or path.resolve().parent != self.canon_trash_dir.resolve()
        ):
            raise ValueError("无效的故事资料回收站条目。")
        if not path.is_dir():
            raise FileNotFoundError(path)
        return path

    def _resolve_canon_entry_target(
        self,
        kind: str,
        path_or_id: Path | str,
    ) -> Path:
        """Resolve and validate a live canon document target."""
        kind = normalize_canon_entry_kind(kind)
        if kind == "timeline":
            expected = self.project.canon_dir / "timeline.md"
            raw = str(path_or_id or "").strip()
            if raw and raw not in {"timeline", "timeline.md"}:
                candidate = Path(path_or_id)
                if not candidate.is_absolute():
                    candidate = self.root / candidate
                if candidate.resolve() != expected.resolve():
                    raise ValueError("只能操作当前项目的时间线文件。")
            return expected

        directory = self.project.canon_dir / kind
        raw = str(path_or_id or "").strip()
        if isinstance(path_or_id, Path):
            candidate = path_or_id
            if not candidate.is_absolute():
                if candidate.parent == Path(".") and candidate.suffix.casefold() == ".md":
                    candidate = directory / candidate.name
                else:
                    candidate = self.root / candidate
        elif Path(raw).is_absolute():
            candidate = Path(raw)
        elif Path(raw).name == raw and raw.casefold().endswith(".md"):
            candidate = directory / raw
        elif "/" in raw or "\\" in raw:
            candidate = self.root / Path(raw)
        else:
            candidate = directory / f"{raw}.md"
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise ValueError("故事资料路径无效。") from exc
        if (
            resolved.parent != directory.resolve()
            or resolved.suffix.casefold() != ".md"
        ):
            raise ValueError("只能操作当前项目中的世界观或体系设定文件。")
        return candidate

    def _canon_target_from_entry(self, entry: CanonTrashEntry) -> Path:
        """Validate a manifest's original path before restoring it."""
        target = self.root / entry.original_path
        if entry.kind == "timeline":
            expected = self.project.canon_dir / "timeline.md"
            if target.resolve() != expected.resolve() or entry.entry_id != "timeline":
                raise ValueError("回收站条目不是有效的时间线路径。")
            return expected
        directory = self.project.canon_dir / entry.kind
        if (
            target.resolve().parent != directory.resolve()
            or target.name != f"{entry.entry_id}.md"
            or target.suffix.casefold() != ".md"
        ):
            raise ValueError("回收站条目不是有效的故事资料路径。")
        return target

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

    def _read_character_trash_entry(self, path: Path) -> CharacterTrashEntry:
        manifest_path = Path(path) / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        required = ("trash_id", "character_id", "title", "original_path", "deleted_at")
        if any(key not in data for key in required):
            raise ValueError("角色卡回收站条目元数据不完整。")
        if str(data["trash_id"]) != Path(path).name:
            raise ValueError("角色卡回收站条目标识不一致。")
        return CharacterTrashEntry(
            trash_id=str(data["trash_id"]),
            character_id=str(data["character_id"]),
            title=str(data["title"]),
            original_path=str(data["original_path"]),
            deleted_at=str(data["deleted_at"]),
            path=Path(path),
        )

    def _read_canon_trash_entry(self, path: Path) -> CanonTrashEntry:
        manifest_path = Path(path) / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        required = (
            "trash_id",
            "kind",
            "entry_id",
            "title",
            "original_path",
            "deleted_at",
        )
        if any(key not in data for key in required):
            raise ValueError("故事资料回收站条目元数据不完整。")
        if str(data["trash_id"]) != Path(path).name:
            raise ValueError("故事资料回收站条目标识不一致。")
        kind = normalize_canon_entry_kind(str(data["kind"]))
        if kind not in {"world", "power", "timeline"}:
            raise ValueError("回收站条目不是受支持的故事资料类型。")
        entry_id = str(data["entry_id"])
        if not entry_id or Path(entry_id).name != entry_id:
            raise ValueError("故事资料回收站条目标识无效。")
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        return CanonTrashEntry(
            trash_id=str(data["trash_id"]),
            kind=kind,
            entry_id=entry_id,
            title=str(data["title"]),
            original_path=str(data["original_path"]),
            deleted_at=str(data["deleted_at"]),
            path=Path(path),
            metadata={str(key): str(value) for key, value in metadata.items()},
        )

    def commit_memory_update(
        self,
        chapter_id: str,
        summary: str,
        state: dict,
        *,
        accepted_record: dict | None = None,
        invalidated_chapter_ids: tuple[str, ...] | list[str] = (),
    ) -> None:
        """Persist summary and state together with best-effort rollback."""
        from .accepted_memory import load_accepted_memory, save_accepted_memory

        old_records = load_accepted_memory(self.project)
        new_records = deepcopy(old_records)
        invalidated = {
            str(item).strip()
            for item in invalidated_chapter_ids
            if str(item).strip() and str(item).strip() != chapter_id
        }
        for invalidated_id in invalidated:
            new_records.pop(invalidated_id, None)
        if accepted_record is not None:
            new_records[chapter_id] = deepcopy(accepted_record)
        else:
            new_records.pop(chapter_id, None)
        old_summaries = deepcopy(self.load_chapter_summaries())
        old_state = deepcopy(self.load_story_state())
        new_summaries = deepcopy(old_summaries)
        for invalidated_id in invalidated:
            new_summaries.pop(invalidated_id, None)
        new_summaries[chapter_id] = str(summary or "").strip()
        try:
            self.save_chapter_summaries(new_summaries)
            self.save_story_state(state)
            if accepted_record is not None or old_records:
                save_accepted_memory(self.project, new_records)
        except Exception:
            try:
                self.save_chapter_summaries(old_summaries)
                self.save_story_state(old_state)
                if accepted_record is not None or old_records:
                    save_accepted_memory(self.project, old_records)
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
