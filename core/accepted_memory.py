"""Portable provenance for author-adopted chapter memory, never proposal caches."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

from .project import chapter_number_from_id
from .storage import atomic_write_text
from .text_chunking import chapter_content_hash


ACCEPTED_MEMORY_SCHEMA = 1


def memory_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def accepted_memory_path(project):
    return project.memory_dir / "accepted_chapter_memory.json"


def load_accepted_memory(project) -> dict:
    path = accepted_memory_path(project)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema_version") != ACCEPTED_MEMORY_SCHEMA or not isinstance(value.get("chapters"), dict):
            raise ValueError("unsupported schema")
        return value["chapters"]
    except (OSError, UnicodeError, ValueError, AttributeError) as exc:
        raise ValueError("已采用章节记忆记录无法读取，请检查 memory/accepted_chapter_memory.json。") from exc


def save_accepted_memory(project, records: dict) -> None:
    atomic_write_text(accepted_memory_path(project), json.dumps({
        "schema_version": ACCEPTED_MEMORY_SCHEMA, "chapters": records,
    }, ensure_ascii=False, indent=2))


def make_accepted_record(project, proposal, state: dict, base_state: dict) -> dict:
    """Called only at explicit adoption. Keep state dependencies for time-scoped reads."""
    ordinal = chapter_number_from_id(proposal.chapter_id)
    base_ordinal = base_state.get("current_chapter")
    coverage = max(ordinal, base_ordinal) if (
        ordinal is not None and type(base_ordinal) is int
    ) else None
    dependencies = []
    if coverage is not None:
        for path in project.list_chapters():
            number = chapter_number_from_id(path.stem)
            if number is not None and number <= coverage:
                dependencies.append((
                    number,
                    path.stem,
                    chapter_content_hash(project.load_chapter(path.stem).content),
                ))
        dependencies.sort()
    base_coverage = max(0, ordinal - 1) if ordinal is not None else None
    base_dependencies = []
    if base_coverage is not None:
        for path in project.list_chapters():
            number = chapter_number_from_id(path.stem)
            if number is not None and number <= base_coverage:
                base_dependencies.append((
                    number,
                    path.stem,
                    chapter_content_hash(project.load_chapter(path.stem).content),
                ))
        base_dependencies.sort()
    record = {
        "schema_version": ACCEPTED_MEMORY_SCHEMA,
        "chapter_id": proposal.chapter_id,
        "source_hash": proposal.chapter_hash,
        "summary_hash": memory_hash(proposal.summary),
        "digest": proposal.digest.to_dict(),
        "evidence_facts": [fact.to_dict() for fact in proposal.evidence_facts],
        "context_hash": proposal.context_hash,
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "state": deepcopy(state),
        # Preserve the actual pre-chapter state so chapter 1 and projects with
        # gaps can safely replace an already-adopted version of the same chapter.
        "base_state": deepcopy(base_state),
        "base_state_scope": str(getattr(proposal, "base_state_scope", "") or ""),
        "base_state_through_chapter": base_coverage,
        "base_dependency_hash": (
            memory_hash(base_dependencies) if base_coverage is not None else ""
        ),
        "base_dependency_count": len(base_dependencies),
        "state_through_chapter": coverage,
        # Store a compact ordered fingerprint instead of every preceding hash in
        # every record; this keeps adopted-memory storage linear in chapter count.
        "state_dependency_hash": memory_hash(dependencies) if coverage is not None else "",
        "state_dependency_count": len(dependencies),
    }
    record["record_hash"] = memory_hash(record)
    return record


class AcceptedMemoryView:
    """Task-local rebuildable index. Source validation is lazy and memoized."""

    def __init__(self, project, summaries: dict):
        self.project = project
        self.summaries = summaries
        self.error = False
        try:
            self.records = load_accepted_memory(project)
        except ValueError:
            self.records = {}
            self.error = True
        self.paths = {path.stem: path for path in project.list_chapters()}
        self._hashes = {}
        self._dependency_signatures = {}

    def source_hash(self, chapter_id):
        if chapter_id not in self._hashes:
            try:
                path = self.paths[chapter_id]
                if path.resolve().parent != self.project.chapters_dir.resolve():
                    raise ValueError("outside chapter directory")
                self._hashes[chapter_id] = chapter_content_hash(self.project.load_chapter(chapter_id).content)
            except (KeyError, OSError, UnicodeError, ValueError):
                self._hashes[chapter_id] = None
        return self._hashes[chapter_id]

    def status(self, chapter_id: str) -> str:
        record = self.records.get(chapter_id)
        if record is None:
            return "unverified"
        if not self._record_integrity_valid(chapter_id, record):
            return "invalid"
        if self.source_hash(chapter_id) is None:
            return "missing_source"
        if (record.get("source_hash") != self.source_hash(chapter_id)
                or record.get("summary_hash") != memory_hash(self.summaries.get(chapter_id))):
            return "stale"
        return "verified"

    @staticmethod
    def _record_integrity_valid(chapter_id: str, record: object) -> bool:
        if not isinstance(record, dict):
            return False
        payload = {key: value for key, value in record.items() if key != "record_hash"}
        return bool(
            record.get("schema_version") == ACCEPTED_MEMORY_SCHEMA
            and record.get("chapter_id") == chapter_id
            and record.get("record_hash") == memory_hash(payload)
            and isinstance(record.get("digest"), dict)
            and isinstance(record.get("state"), dict)
            and isinstance(record.get("state_dependency_hash"), str)
            and type(record.get("state_dependency_count")) is int
        )

    def dependency_signature(self, coverage: int) -> tuple[str, int]:
        if coverage not in self._dependency_signatures:
            dependencies = []
            for key in self.paths:
                number = chapter_number_from_id(key)
                if number is not None and number <= coverage:
                    dependencies.append((number, key, self.source_hash(key)))
            dependencies.sort()
            self._dependency_signatures[coverage] = (
                memory_hash(dependencies), len(dependencies),
            )
        return self._dependency_signatures[coverage]

    def state_for(self, current_id: str, latest: dict) -> tuple[dict, str]:
        target = chapter_number_from_id(current_id)
        if target is None:
            return {}, "unknown_position"
        candidates = sorted(
            ((r.get("state_through_chapter"), key, r) for key, r in self.records.items()
             if isinstance(r, dict) and type(r.get("state_through_chapter")) is int
             and r["state_through_chapter"] < target), reverse=True,
        )
        for _, key, record in candidates:
            if self.status(key) != "verified":
                continue
            dependency_hash, dependency_count = self.dependency_signature(
                record["state_through_chapter"]
            )
            if (record.get("state_dependency_hash") != dependency_hash
                    or record.get("state_dependency_count") != dependency_count):
                continue
            return deepcopy(record["state"]), f"snapshot:{key}"
        latest_ordinal = latest.get("current_chapter")
        if not self.records and not self.error and type(latest_ordinal) is int and latest_ordinal < target:
            return deepcopy(latest), "legacy_unverified"
        return {}, "future_or_unverified_state_omitted"

    def base_state_for(self, current_id: str, latest: dict) -> tuple[dict, str]:
        """Return state strictly before ``current_id`` for memory replacement.

        Normal context reads only need an earlier verified endpoint. Memory
        replacement additionally needs the recorded pre-state for chapter 1 or
        sparse/legacy projects where no earlier accepted endpoint exists.
        """
        state, scope = self.state_for(current_id, latest)
        if scope.startswith("snapshot:") or scope == "legacy_unverified":
            return state, scope

        target = chapter_number_from_id(current_id)
        record = self.records.get(current_id)
        if target is not None and self._record_integrity_valid(current_id, record):
            assert isinstance(record, dict)
            recorded_base = record.get("base_state")
            base_coverage = record.get("base_state_through_chapter")
            if (
                isinstance(recorded_base, dict)
                and type(base_coverage) is int
                and isinstance(record.get("base_dependency_hash"), str)
                and type(record.get("base_dependency_count")) is int
            ):
                dependency_hash, dependency_count = self.dependency_signature(
                    base_coverage
                )
                if (
                    record["base_dependency_hash"] == dependency_hash
                    and record["base_dependency_count"] == dependency_count
                ):
                    return deepcopy(recorded_base), f"recorded_base:{current_id}"

        latest_ordinal = latest.get("current_chapter")
        if (
            target is not None
            and current_id not in self.records
            and type(latest_ordinal) is int
            and latest_ordinal <= target
        ):
            return deepcopy(latest), "legacy_current"
        return {}, "future_or_unverified_state_omitted"


def memory_base_state_for(project, chapter_id: str, latest: dict | None = None) -> tuple[dict, str]:
    """Resolve the immutable pre-chapter state used by memory generation/commit."""
    latest_state = deepcopy(latest if isinstance(latest, dict) else project.load_story_state())
    summaries = project.load_chapter_summaries()
    return AcceptedMemoryView(project, summaries).base_state_for(chapter_id, latest_state)
