"""Evidence-bound synchronization for the managed section of character cards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable
from uuid import uuid4

from .accepted_memory import AcceptedMemoryView
from .project import NovelProject
from .storage import atomic_write_text


MANAGED_STATE_HEADING = "## 当前剧情状态（Novalist 同步）"
MANAGED_STATE_START = "<!-- novalist:auto-state:v1:start -->"
MANAGED_STATE_END = "<!-- novalist:auto-state:v1:end -->"

FIELD_SPECS = (
    ("state", "当前状态"),
    ("location", "当前位置"),
    ("power_level", "当前战力"),
    ("items", "持有物"),
    ("relations", "当前关系"),
)
FIELD_LABELS = dict(FIELD_SPECS)

_POWER_WORDS = ("能力", "战力", "等级", "境界", "修为", "阶", "级")
_LOCATION_WORDS = ("位置", "地点", "身处", "抵达", "进入", "离开", "前往")


class CharacterCardSyncError(ValueError):
    """Raised when a safe character-card proposal cannot be built or applied."""


@dataclass(frozen=True)
class CharacterCardEvidence:
    chapter_id: str
    fact_id: str
    quote: str
    certainty: str


@dataclass(frozen=True)
class CharacterCardPatch:
    field: str
    label: str
    expected_before: Any
    value: Any
    evidence: tuple[CharacterCardEvidence, ...]
    certainty: str

    @property
    def default_selected(self) -> bool:
        return self.certainty == "explicit"


@dataclass(frozen=True)
class CharacterCardSyncProposal:
    character_id: str
    character_name: str
    card_path: Path
    chapter_ids: tuple[str, ...]
    as_of_chapter: str
    expected_card_hash: str
    patches: tuple[CharacterCardPatch, ...]


@dataclass(frozen=True)
class CharacterCardSyncCommit:
    card_path: Path
    backup_path: Path
    applied_fields: tuple[str, ...]


def text_hash(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def parse_managed_state(card_text: str) -> dict[str, Any]:
    """Parse only the app-owned marker block; author prose remains opaque."""
    block = _managed_block(card_text)
    if block is None:
        return {}
    values: dict[str, Any] = {}
    reverse = {label: field for field, label in FIELD_SPECS}
    for line in block.splitlines():
        match = re.match(r"^\s*-\s*([^：:]+)[：:]\s*(.*?)\s*$", line)
        if not match:
            continue
        label, raw = match.groups()
        field = reverse.get(label.strip())
        if field == "items":
            values[field] = _parse_list(raw)
        elif field == "relations":
            values[field] = _parse_relations(raw)
        elif field:
            values[field] = "" if raw == "未记录" else raw
    as_of = re.search(
        r"^\s*-\s*(?:本次同步截止章节|状态截止章节)[：:]\s*(.*?)\s*$",
        block,
        re.MULTILINE,
    )
    if as_of:
        values["as_of_chapter"] = as_of.group(1).strip()
    return values


def build_character_card_sync_proposal(
    project: NovelProject,
    card_path: Path | str,
    chapter_ids: Iterable[str],
) -> CharacterCardSyncProposal:
    """Build a conservative proposal from verified, author-adopted memories."""
    path = _validated_card_path(project, card_path)
    ordered = _validated_chapter_range(project, chapter_ids)
    card_text = path.read_text(encoding="utf-8")
    summaries = project.load_chapter_summaries()
    view = AcceptedMemoryView(project, summaries)
    invalid = [(chapter_id, view.status(chapter_id)) for chapter_id in ordered if view.status(chapter_id) != "verified"]
    if invalid:
        labels = {
            "unverified": "尚未采用故事记忆",
            "stale": "正文或摘要已变化",
            "missing_source": "章节文件缺失",
            "invalid": "记忆记录无效",
        }
        detail = "、".join(f"{chapter_id}（{labels.get(status, status)}）" for chapter_id, status in invalid)
        raise CharacterCardSyncError(
            "所选范围包含不能作为同步依据的章节："
            + detail
            + "。请先逐章更新并采用故事记忆，或缩小章节范围。"
        )

    names = _character_names(path, card_text)
    endpoint_record = view.records[ordered[-1]]
    coverage = endpoint_record.get("state_through_chapter")
    if type(coverage) is int:
        dependency_hash, dependency_count = view.dependency_signature(coverage)
        if (
            endpoint_record.get("state_dependency_hash") != dependency_hash
            or endpoint_record.get("state_dependency_count") != dependency_count
        ):
            raise CharacterCardSyncError(
                "截止章节的故事状态依赖已经变化，请重新更新并采用该章节的故事记忆。"
            )
    endpoint_state = endpoint_record.get("state", {})
    characters = endpoint_state.get("characters", {}) if isinstance(endpoint_state, dict) else {}
    state_key = _match_character_key(characters, names)
    if state_key is None:
        raise CharacterCardSyncError(
            f"截止章节的已采用故事状态中没有找到角色“{path.stem}”。"
        )
    details = characters.get(state_key)
    if not isinstance(details, dict):
        raise CharacterCardSyncError("截止章节中的角色状态格式无效。")

    evidence_by_field = _collect_evidence(view, ordered, names | {state_key})
    current = parse_managed_state(card_text)
    patches: list[CharacterCardPatch] = []
    for field, label in FIELD_SPECS:
        evidence = evidence_by_field.get(field, ())
        if not evidence:
            continue
        # Missing keys are not equivalent to empty values.  A confirmed
        # removal produced by the memory patch pipeline retains ``items: []``.
        if field not in details:
            continue
        value = _normalize_value(field, details.get(field))
        before = current.get(field, [] if field == "items" else {} if field == "relations" else "")
        if _canonical(before) == _canonical(value):
            continue
        certainty = "explicit" if any(item.certainty == "explicit" for item in evidence) else "inferred"
        patches.append(
            CharacterCardPatch(
                field=field,
                label=label,
                expected_before=before,
                value=value,
                evidence=tuple(evidence),
                certainty=certainty,
            )
        )
    if not patches:
        raise CharacterCardSyncError(
            "所选章节没有产生可安全同步的角色状态变化。只有带角色事实证据的字段才会进入预览。"
        )
    return CharacterCardSyncProposal(
        character_id=path.stem,
        character_name=state_key,
        card_path=path,
        chapter_ids=ordered,
        as_of_chapter=ordered[-1],
        expected_card_hash=text_hash(card_text),
        patches=tuple(patches),
    )


def apply_character_card_sync(
    project: NovelProject,
    proposal: CharacterCardSyncProposal,
    selected_fields: Iterable[str],
) -> CharacterCardSyncCommit:
    """Apply selected patches atomically after checking the card revision."""
    path = _validated_card_path(project, proposal.card_path)
    selected = tuple(dict.fromkeys(str(field) for field in selected_fields))
    allowed = {patch.field: patch for patch in proposal.patches}
    if not selected or any(field not in allowed for field in selected):
        raise CharacterCardSyncError("没有选择有效的角色卡变更。")
    original = path.read_text(encoding="utf-8")
    if text_hash(original) != proposal.expected_card_hash:
        raise CharacterCardSyncError("角色卡在预览生成后已发生变化，请重新生成同步预览。")

    managed = parse_managed_state(original)
    for field in selected:
        patch = allowed[field]
        if _canonical(managed.get(field, [] if field == "items" else {} if field == "relations" else "")) != _canonical(patch.expected_before):
            raise CharacterCardSyncError(f"角色卡字段“{patch.label}”已发生变化，请重新生成同步预览。")
        managed[field] = patch.value
    managed["as_of_chapter"] = proposal.as_of_chapter
    updated = replace_managed_state(original, managed)
    backup = _write_backup(project, proposal, original, selected)
    try:
        atomic_write_text(path, updated, encoding="utf-8")
    except Exception:
        # The original target was never mutated partially: atomic_write_text
        # either replaces it or raises before replacement.
        raise
    return CharacterCardSyncCommit(path, backup, selected)


def replace_managed_state(card_text: str, state: dict[str, Any]) -> str:
    """Replace or append the app-owned state block without touching prose."""
    block = _render_managed_block(state)
    bounds = _managed_bounds(card_text)
    if bounds is not None:
        start, end = bounds
        return card_text[:start] + block + card_text[end:]
    base = card_text.rstrip()
    return f"{base}\n\n{MANAGED_STATE_HEADING}\n\n{block}\n"


def display_value(value: Any) -> str:
    if isinstance(value, dict):
        if not value:
            return "无"
        return "；".join(f"{key}：{item}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value) if value else "无"
    return str(value or "未记录")


def _render_managed_block(state: dict[str, Any]) -> str:
    lines = [MANAGED_STATE_START, f"- 本次同步截止章节：{state.get('as_of_chapter') or '未记录'}"]
    for field, label in FIELD_SPECS:
        lines.append(f"- {label}：{display_value(state.get(field))}")
    lines.append(MANAGED_STATE_END)
    return "\n".join(lines)


def _managed_bounds(card_text: str) -> tuple[int, int] | None:
    if card_text.count(MANAGED_STATE_START) > 1 or card_text.count(MANAGED_STATE_END) > 1:
        raise CharacterCardSyncError("角色卡中存在重复的 Novalist 同步标记，请修复后重试。")
    start = card_text.find(MANAGED_STATE_START)
    end = card_text.find(MANAGED_STATE_END)
    if start < 0 and end < 0:
        return None
    if start < 0 or end < start:
        raise CharacterCardSyncError("角色卡中的 Novalist 同步标记不完整，请修复后重试。")
    return start, end + len(MANAGED_STATE_END)


def _managed_block(card_text: str) -> str | None:
    bounds = _managed_bounds(card_text)
    return card_text[bounds[0] : bounds[1]] if bounds is not None else None


def _validated_card_path(project: NovelProject, card_path: Path | str) -> Path:
    path = Path(card_path)
    if not path.is_absolute():
        path = project.root / path
    path = path.resolve()
    characters_dir = (project.canon_dir / "characters").resolve()
    if path.parent != characters_dir or path.suffix.casefold() != ".md":
        raise CharacterCardSyncError("只能同步当前项目角色目录中的 Markdown 角色卡。")
    if not path.is_file():
        raise CharacterCardSyncError("目标角色卡不存在。")
    return path


def _validated_chapter_range(project: NovelProject, chapter_ids: Iterable[str]) -> tuple[str, ...]:
    requested = tuple(dict.fromkeys(str(item).strip() for item in chapter_ids if str(item).strip()))
    if not requested:
        raise CharacterCardSyncError("请至少选择一个章节。")
    available = [path.stem for path in project.list_chapters()]
    positions = {chapter_id: index for index, chapter_id in enumerate(available)}
    if any(chapter_id not in positions for chapter_id in requested):
        raise CharacterCardSyncError("所选章节不存在或不属于当前项目。")
    indexes = [positions[chapter_id] for chapter_id in requested]
    start, end = min(indexes), max(indexes)
    expected = tuple(available[start : end + 1])
    if requested != expected:
        raise CharacterCardSyncError("角色卡同步目前只支持按项目顺序选择连续章节范围。")
    return requested


def _character_names(path: Path, card_text: str) -> set[str]:
    names = {path.stem}
    title = re.search(r"(?m)^#\s+(.+?)\s*$", card_text)
    if title:
        names.add(title.group(1).strip())
    aliases = re.search(r"(?m)^\s*-?\s*别名[：:]\s*(.*?)\s*$", card_text)
    if aliases:
        names.update(_parse_list(aliases.group(1)))
    return {name.strip() for name in names if name.strip()}


def _match_character_key(characters: object, names: set[str]) -> str | None:
    if not isinstance(characters, dict):
        return None
    folded = {name.casefold() for name in names}
    matches = [str(key) for key in characters if str(key).strip().casefold() in folded]
    if len(matches) > 1:
        raise CharacterCardSyncError("角色卡名称或别名同时匹配多个故事状态角色，请先消除歧义。")
    return matches[0] if matches else None


def _collect_evidence(
    view: AcceptedMemoryView,
    chapter_ids: tuple[str, ...],
    names: set[str],
) -> dict[str, list[CharacterCardEvidence]]:
    result: dict[str, list[CharacterCardEvidence]] = {field: [] for field, _label in FIELD_SPECS}
    for chapter_id in chapter_ids:
        record = view.records.get(chapter_id, {})
        facts = record.get("evidence_facts", []) if isinstance(record, dict) else []
        for fact in facts if isinstance(facts, list) else []:
            if not isinstance(fact, dict) or not _fact_mentions_character(fact, names):
                continue
            certainty = str(fact.get("certainty") or "uncertain")
            if certainty not in {"explicit", "inferred"}:
                continue
            category = str(fact.get("category") or "")
            text = " ".join(str(fact.get(key) or "") for key in ("predicate", "value", "anchor"))
            for field in _fields_for_fact(category, text):
                result[field].append(
                    CharacterCardEvidence(
                        chapter_id=chapter_id,
                        fact_id=str(fact.get("fact_id") or ""),
                        quote=str(fact.get("anchor") or fact.get("value") or "").strip(),
                        certainty=certainty,
                    )
                )
    return result


def _fields_for_fact(category: str, text: str) -> tuple[str, ...]:
    if category == "location":
        return ("location",)
    if category == "item":
        return ("items",)
    if category == "relationship":
        return ("relations",)
    if category != "character_state":
        return ()
    if any(word in text for word in _POWER_WORDS):
        return ("power_level",)
    if any(word in text for word in _LOCATION_WORDS):
        return ("location",)
    return ("state",)


def _fact_mentions_character(fact: dict[str, Any], names: set[str]) -> bool:
    haystack = "\n".join(str(fact.get(key) or "") for key in ("subject", "predicate", "value", "anchor")).casefold()
    return any(name.casefold() in haystack for name in names if name)


def _normalize_value(field: str, value: Any) -> Any:
    if field == "items":
        return [str(item).strip() for item in value] if isinstance(value, (list, tuple)) else _parse_list(str(value or ""))
    if field == "relations":
        return {str(key).strip(): str(item).strip() for key, item in value.items() if str(key).strip()} if isinstance(value, dict) else {}
    return str(value or "").strip()


def _parse_list(value: str) -> list[str]:
    raw = str(value or "").strip()
    if not raw or raw in {"无", "未记录"}:
        return []
    return [item.strip() for item in re.split(r"[、,，;；]", raw) if item.strip()]


def _parse_relations(value: str) -> dict[str, str]:
    raw = str(value or "").strip()
    if not raw or raw in {"无", "未记录"}:
        return {}
    result: dict[str, str] = {}
    for item in re.split(r"[;；]", raw):
        if "：" in item:
            name, relation = item.split("：", 1)
        elif ":" in item:
            name, relation = item.split(":", 1)
        else:
            continue
        if name.strip():
            result[name.strip()] = relation.strip()
    return result


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_backup(
    project: NovelProject,
    proposal: CharacterCardSyncProposal,
    original: str,
    selected_fields: tuple[str, ...],
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup_dir = project.root / ".novalist" / "backups" / f"character-sync-{stamp}-{uuid4().hex[:8]}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    atomic_write_text(backup_dir / "character.md", original, encoding="utf-8")
    manifest = {
        "version": 1,
        "kind": "character_card_sync",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "original_path": proposal.card_path.resolve().relative_to(project.root.resolve()).as_posix(),
        "original_hash": proposal.expected_card_hash,
        "chapter_ids": list(proposal.chapter_ids),
        "as_of_chapter": proposal.as_of_chapter,
        "applied_fields": list(selected_fields),
        "evidence": {
            field: [
                {
                    "chapter_id": item.chapter_id,
                    "fact_id": item.fact_id,
                    "quote": item.quote,
                    "certainty": item.certainty,
                }
                for item in next(patch for patch in proposal.patches if patch.field == field).evidence
            ]
            for field in selected_fields
        },
    }
    atomic_write_text(
        backup_dir / "backup-manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return backup_dir
