"""Snapshots used to prevent stale AI results from being written."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .project import NovelProject


def context_paths(
    project: NovelProject,
    chapter_id: str,
    *,
    task_kind: str | None = None,
) -> list[Path]:
    """Return files that can contribute to one task's AI prompt.

    Expansion uses the same world/power canon profile as the prompt builder.
    Checks and memory updates retain the complete project fingerprint because
    their prompts inspect the complete canon.
    """
    paths = [
        project.root / "project.json",
        project.chapters_dir / f"{chapter_id}.md",
        project.outline_dir / "main_arc.md",
        project.outline_dir / "future_plan.md",
        project.canon_dir / "timeline.md",
        project.system_registry_path,
        project.memory_dir / "story_state.json",
        project.memory_dir / "chapter_summaries.json",
        project.memory_dir / "foreshadowing.json",
    ]
    if task_kind == "expand":
        paths.append(project.style_guide_path)
        chapter = project.load_chapter(chapter_id)
        query = "\n".join(
            part for part in (chapter.title, chapter.outline, chapter.plot_brief) if part
        )
        paths.extend(
            path for path in project.list_characters() if path.stem and path.stem in query
        )
        paths.extend(project.list_world())
        paths.extend(project.list_power())
    else:
        paths.extend(project.list_characters())
        paths.extend(project.list_world())
        paths.extend(project.list_power())
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
    task_context_hash: str = ""
    task_kind: str = ""

    @classmethod
    def capture(
        cls,
        project: NovelProject,
        chapter_id: str,
        editor_text: str | None,
        task_context: object | None = None,
        task_kind: str | None = None,
    ) -> "AIContextSnapshot":
        root = project.root.resolve()
        hashes = []
        for path in context_paths(project, chapter_id, task_kind=task_kind):
            try:
                key = str(path.resolve().relative_to(root)).casefold()
            except ValueError:
                key = str(path.resolve()).casefold()
            hashes.append((key, _file_revision(path)))
        file_hashes = tuple(hashes)
        editor_hash = _text_hash(editor_text) if editor_text is not None else None
        normalized_task_kind = str(task_kind or "")
        context_payload = {
            "project_root": str(root).casefold(),
            "chapter_id": str(chapter_id),
            "editor_hash": editor_hash,
            "file_hashes": file_hashes,
            "task_context_hash": _object_hash(task_context),
            "task_kind": normalized_task_kind,
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
            task_context_hash=context_payload["task_context_hash"],
            task_kind=normalized_task_kind,
        )

    def matches(
        self,
        project: NovelProject,
        chapter_id: str,
        editor_text: str | None,
        task_context: object | None = None,
        task_kind: str | None = None,
    ) -> bool:
        if str(project.root.resolve()).casefold() != self.project_root:
            return False
        if str(chapter_id) != self.chapter_id:
            return False
        if self.editor_hash != (
            _text_hash(editor_text) if editor_text is not None else None
        ):
            return False
        effective_task_kind = self.task_kind if task_kind is None else task_kind
        current = AIContextSnapshot.capture(
            project,
            chapter_id,
            editor_text,
            task_context=task_context,
            task_kind=effective_task_kind,
        )
        return (
            current.context_hash == self.context_hash
            and current.file_hashes == self.file_hashes
        )


def _file_revision(path: Path) -> str:
    """Return a cheap file revision marker for UI-thread stale checks.

    The old implementation read every file and calculated SHA-256 during task
    start and result confirmation.  ``mtime + ctime + size`` catches normal
    edits and file replacement without reading file contents.  The task itself
    still reads the authoritative content in its worker thread.
    """
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return "<missing>"
    return f"{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_size}"


def _text_hash(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _object_hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
