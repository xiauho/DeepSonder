"""Snapshots used to prevent stale AI results from being written."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from .project import NovelProject


_FILE_HASH_CACHE: dict[str, tuple[int, int, str]] = {}
_FILE_HASH_LOCK = RLock()


def context_paths(project: NovelProject, chapter_id: str) -> list[Path]:
    """Return every project file that can contribute to an AI prompt."""
    paths = [
        project.root / "project.json",
        project.chapters_dir / f"{chapter_id}.md",
        project.outline_dir / "main_arc.md",
        project.outline_dir / "future_plan.md",
        project.canon_dir / "timeline.md",
        project.memory_dir / "story_state.json",
        project.memory_dir / "chapter_summaries.json",
        *project.list_characters(),
        *project.list_world(),
        *project.list_power(),
    ]
    unique: dict[str, Path] = {}
    for path in paths:
        resolved = str(Path(path).resolve())
        unique[resolved.casefold()] = Path(path)
    return [unique[key] for key in sorted(unique)]


@dataclass(frozen=True)
class AIContextSnapshot:
    """Immutable fingerprint of the context used by one AI task."""

    project_root: str
    chapter_id: str
    editor_hash: str | None
    context_hash: str
    file_hashes: tuple[tuple[str, str], ...]

    @classmethod
    def capture(
        cls,
        project: NovelProject,
        chapter_id: str,
        editor_text: str | None,
    ) -> "AIContextSnapshot":
        root = project.root.resolve()
        hashes = []
        for path in context_paths(project, chapter_id):
            try:
                key = str(path.resolve().relative_to(root)).casefold()
            except ValueError:
                key = str(path.resolve()).casefold()
            hashes.append((key, _file_hash(path)))
        file_hashes = tuple(hashes)
        editor_hash = _text_hash(editor_text) if editor_text is not None else None
        context_payload = {
            "project_root": str(root).casefold(),
            "chapter_id": str(chapter_id),
            "editor_hash": editor_hash,
            "file_hashes": file_hashes,
        }
        context_hash = hashlib.sha256(
            json.dumps(context_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return cls(
            project_root=str(root).casefold(),
            chapter_id=str(chapter_id),
            editor_hash=editor_hash,
            context_hash=context_hash,
            file_hashes=file_hashes,
        )

    def matches(
        self,
        project: NovelProject,
        chapter_id: str,
        editor_text: str | None,
    ) -> bool:
        if str(project.root.resolve()).casefold() != self.project_root:
            return False
        if str(chapter_id) != self.chapter_id:
            return False
        if self.editor_hash != (
            _text_hash(editor_text) if editor_text is not None else None
        ):
            return False
        current = AIContextSnapshot.capture(project, chapter_id, editor_text)
        return (
            current.context_hash == self.context_hash
            and current.file_hashes == self.file_hashes
        )


def _file_hash(path: Path) -> str:
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        with _FILE_HASH_LOCK:
            _FILE_HASH_CACHE.pop(str(path.resolve()).casefold(), None)
        return "<missing>"

    key = str(path.resolve()).casefold()
    signature = (stat.st_mtime_ns, stat.st_size)
    with _FILE_HASH_LOCK:
        cached = _FILE_HASH_CACHE.get(key)
        if cached is not None and cached[:2] == signature:
            return cached[2]
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "<unreadable>"
    with _FILE_HASH_LOCK:
        _FILE_HASH_CACHE[key] = (*signature, digest)
    return digest


def _text_hash(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()
