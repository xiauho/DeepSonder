"""Chapter-level reduction, evidence-bound memory patches and conflicts."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .app_paths import app_cache_dir
from .chapter_facts import ChapterFactLedger, FactRecord
from .context_budget import compact_story_state
from .context_report import PromptBundle, PromptContextReport, SectionUsage
from .json_utils import JSONExtractionError, extract_json
from .project import NovelProject, chapter_number_from_id
from .storage import atomic_write_text
from .task_controller import AITaskCancelled
from .token_budget import (
    DEFAULT_INPUT_TOKEN_BUDGET,
    DEFAULT_TOKEN_ESTIMATOR,
    ConservativeTokenEstimator,
)


MEMORY_SUGGESTION_SCHEMA_VERSION = 2
MEMORY_PROPOSAL_PROMPT_VERSION = 4
MEMORY_CACHE_SCHEMA_VERSION = 1
DIGEST_SHARD_SCHEMA_VERSION = 1
DEFAULT_REDUCE_BATCH_TOKENS = 8_000
MAX_REDUCE_ROUNDS = 10

DIGEST_FIELDS = (
    "key_events",
    "character_changes",
    "location_changes",
    "item_changes",
    "relationship_changes",
    "timeline_changes",
)
PATCH_KINDS = frozenset(
    {
        "set_current_location",
        "set_character_field",
        "add_character_item",
        "remove_character_item",
        "set_character_relation",
    }
)
CHARACTER_FIELDS = frozenset({"location", "state", "power_level"})
CONFLICT_SEVERITIES = frozenset({"info", "warning", "blocker"})
MODEL_CONFLICT_KINDS = frozenset(
    {
        "canon_conflict",
        "state_conflict",
        "fact_conflict",
        "timeline_conflict",
        "uncertain_change",
    }
)


class MemoryProposalError(ValueError):
    """Raised when a memory proposal cannot be trusted or applied."""


class MemoryProposalBudgetError(RuntimeError):
    """Raised when hierarchical reduction cannot fit the configured budget."""


@dataclass(frozen=True)
class DigestEntry:
    text: str
    fact_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "fact_ids": list(self.fact_ids)}


@dataclass(frozen=True)
class ChapterDigest:
    summary: str
    key_events: tuple[DigestEntry, ...] = ()
    character_changes: tuple[DigestEntry, ...] = ()
    location_changes: tuple[DigestEntry, ...] = ()
    item_changes: tuple[DigestEntry, ...] = ()
    relationship_changes: tuple[DigestEntry, ...] = ()
    timeline_changes: tuple[DigestEntry, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            **{
                field: [item.to_dict() for item in getattr(self, field)]
                for field in DIGEST_FIELDS
            },
        }


@dataclass(frozen=True)
class MemoryPatch:
    op_id: str
    kind: str
    subject: str
    field: str
    expected_before: Any
    value: Any
    evidence_fact_ids: tuple[str, ...]
    certainty: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id,
            "kind": self.kind,
            "subject": self.subject,
            "field": self.field,
            "expected_before": self.expected_before,
            "value": self.value,
            "evidence_fact_ids": list(self.evidence_fact_ids),
            "certainty": self.certainty,
        }


@dataclass(frozen=True)
class MemoryConflict:
    conflict_id: str
    kind: str
    severity: str
    target: str
    description: str
    evidence_fact_ids: tuple[str, ...] = ()
    op_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "kind": self.kind,
            "severity": self.severity,
            "target": self.target,
            "description": self.description,
            "evidence_fact_ids": list(self.evidence_fact_ids),
            "op_ids": list(self.op_ids),
        }


@dataclass(frozen=True)
class MemoryPatchSuggestion:
    """Untrusted model suggestion before local preconditions are attached."""

    kind: str
    subject: str
    field: str
    value: Any
    evidence_fact_ids: tuple[str, ...]
    certainty: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "field": self.field,
            "value": self.value,
            "evidence_fact_ids": list(self.evidence_fact_ids),
            "certainty": self.certainty,
        }


@dataclass(frozen=True)
class ChapterMemorySuggestion:
    """Minimal V2 model payload before local trust boundaries are applied."""

    request_id: str
    digest: ChapterDigest
    changes: tuple[MemoryPatchSuggestion, ...]
    conflicts: tuple[MemoryConflict, ...]


@dataclass(frozen=True)
class ChapterMemoryProposal:
    chapter_id: str
    chapter_hash: str
    base_state_hash: str
    context_hash: str
    digest: ChapterDigest
    patches: tuple[MemoryPatch, ...]
    conflicts: tuple[MemoryConflict, ...]
    resulting_state: dict[str, Any]
    completion_message: str = "章节记忆提案已生成"
    cache_hit: bool = False
    reduction_calls: int = 0
    evidence_facts: tuple[FactRecord, ...] = ()
    base_state_scope: str = ""
    source_state_hash: str = ""

    @property
    def summary(self) -> str:
        return self.digest.summary

    @property
    def has_blockers(self) -> bool:
        return any(item.severity == "blocker" for item in self.conflicts)

    def to_cache_dict(self) -> dict[str, Any]:
        """Serialize a trusted proposal independently from the model protocol."""
        return {
            "type": "chapter_memory_proposal_cache",
            "cache_schema_version": MEMORY_CACHE_SCHEMA_VERSION,
            "source_protocol_version": MEMORY_SUGGESTION_SCHEMA_VERSION,
            "chapter_id": self.chapter_id,
            "chapter_hash": self.chapter_hash,
            "base_state_hash": self.base_state_hash,
            "context_hash": self.context_hash,
            "digest": self.digest.to_dict(),
            "changes": [
                MemoryPatchSuggestion(
                    item.kind,
                    item.subject,
                    item.field,
                    item.value,
                    item.evidence_fact_ids,
                    item.certainty,
                ).to_dict()
                for item in self.patches
            ],
            "conflicts": [
                {
                    "kind": item.kind,
                    "severity": item.severity,
                    "target": item.target,
                    "description": item.description,
                    "evidence_fact_ids": list(item.evidence_fact_ids),
                }
                for item in self.conflicts
                if item.kind in MODEL_CONFLICT_KINDS
            ],
        }

    def preview_text(self) -> str:
        lines: list[str] = []
        if self.patches:
            lines.append("状态变更：")
            for patch in self.patches:
                lines.append(
                    f"- {_patch_target(patch)}："
                    f"{_display_value(patch.expected_before)} → {_display_value(patch.value)}"
                )
        else:
            lines.append("状态变更：无")
        if self.conflicts:
            lines.append("\n冲突与警告：")
            for conflict in self.conflicts:
                lines.append(
                    f"- [{conflict.severity}] {conflict.description}"
                )
        return "\n".join(lines)

    def conflict_evidence_text(self) -> str:
        """Render cited facts for human review without exposing chapter prose."""
        fact_map = {item.fact_id: item for item in self.evidence_facts}
        lines: list[str] = []
        for conflict in self.conflicts:
            if not conflict.evidence_fact_ids:
                continue
            lines.append(f"[{conflict.severity}] {conflict.target or conflict.kind}")
            lines.append(conflict.description)
            for fact_id in conflict.evidence_fact_ids:
                fact = fact_map.get(fact_id)
                if fact is None:
                    lines.append(f"- {fact_id}（引用事实不可用）")
                    continue
                lines.append(
                    f"- {fact.fact_id} · {fact.category} · {fact.anchor}\n"
                    f"  {fact.subject}｜{fact.predicate}｜{fact.value}"
                )
            lines.append("")
        return "\n".join(lines).strip() or "没有可显示的引用事实。"


@dataclass(frozen=True)
class DigestShard:
    summary: str
    claims: tuple[DigestEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "chapter_digest_shard",
            "schema_version": DIGEST_SHARD_SCHEMA_VERSION,
            "summary": self.summary,
            "claims": [item.to_dict() for item in self.claims],
        }


class ChapterMemoryCache:
    """Disposable derived proposal cache; never stores raw chapter text."""

    def __init__(self, project: NovelProject, root: Path | None = None):
        project_key = hashlib.sha256(
            str(project.root.resolve()).casefold().encode("utf-8")
        ).hexdigest()[:24]
        self.root = Path(root) if root is not None else app_cache_dir() / "ai-memory-v1"
        self.project_dir = self.root / project_key

    def load(
        self,
        ledger: ChapterFactLedger,
        base_state: dict[str, Any],
        context_hash: str,
    ) -> ChapterMemoryProposal | None:
        path = self._path(ledger, canonical_hash(base_state), context_hash)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            proposal = parse_cached_memory_proposal(
                value,
                ledger,
                base_state,
                expected_context_hash=context_hash,
            )
            return _replace_proposal(proposal, cache_hit=True)
        except (OSError, UnicodeError, json.JSONDecodeError, MemoryProposalError):
            return None

    def save(self, proposal: ChapterMemoryProposal) -> None:
        path = self._path_values(
            proposal.chapter_id,
            proposal.chapter_hash,
            proposal.base_state_hash,
            proposal.context_hash,
        )
        atomic_write_text(
            path,
            json.dumps(proposal.to_cache_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def load_reduction(self, ledger: ChapterFactLedger) -> dict[str, Any] | None:
        try:
            value = json.loads(self._reduction_path(ledger).read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("mode") != "digest_shards":
                return None
            raw_shards = value.get("shards")
            if not isinstance(raw_shards, list) or not raw_shards:
                return None
            allowed = frozenset(item.fact_id for item in ledger.facts)
            shards = [parse_digest_shard(item, allowed) for item in raw_shards]
            return {
                "mode": "digest_shards",
                "shards": [item.to_dict() for item in shards],
            }
        except (OSError, UnicodeError, json.JSONDecodeError, MemoryProposalError):
            return None

    def save_reduction(
        self,
        ledger: ChapterFactLedger,
        source: dict[str, Any],
    ) -> None:
        if source.get("mode") != "digest_shards":
            return
        value = {
            "mode": "digest_shards",
            "shards": source.get("shards", []),
        }
        atomic_write_text(
            self._reduction_path(ledger),
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _path(
        self,
        ledger: ChapterFactLedger,
        state_hash: str,
        context_hash: str,
    ) -> Path:
        return self._path_values(
            ledger.chapter_id,
            ledger.chapter_hash,
            state_hash,
            context_hash,
        )

    def _path_values(
        self,
        chapter_id: str,
        chapter_hash: str,
        state_hash: str,
        context_hash: str,
    ) -> Path:
        key = canonical_hash(
            {
                "prompt_version": MEMORY_PROPOSAL_PROMPT_VERSION,
                "chapter_id": chapter_id,
                "chapter_hash": chapter_hash,
                "state_hash": state_hash,
                "context_hash": context_hash,
            }
        )
        chapter_key = hashlib.sha256(chapter_id.casefold().encode("utf-8")).hexdigest()[:20]
        return self.project_dir / chapter_key / f"{key}.json"

    def _reduction_path(self, ledger: ChapterFactLedger) -> Path:
        ledger_fingerprint = canonical_hash(
            {
                "prompt_version": MEMORY_PROPOSAL_PROMPT_VERSION,
                "chapter_hash": ledger.chapter_hash,
                "chunks": [
                    [item.chunk_id, item.source_hash, item.chunk_summary]
                    for item in ledger.chunks
                ],
                "facts": [item.to_dict() for item in ledger.facts],
                "unknowns": [item.to_dict() for item in ledger.unknowns],
            }
        )
        chapter_key = hashlib.sha256(
            ledger.chapter_id.casefold().encode("utf-8")
        ).hexdigest()[:20]
        return self.project_dir / chapter_key / f"reduction-{ledger_fingerprint}.json"


def canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def memory_request_id(
    chapter_id: str,
    chapter_hash: str,
    base_state_hash: str,
    context_hash: str,
) -> str:
    """Bind one compact model response to the complete local source snapshot."""
    return "memory_" + canonical_hash(
        {
            "protocol_version": MEMORY_SUGGESTION_SCHEMA_VERSION,
            "chapter_id": chapter_id,
            "chapter_hash": chapter_hash,
            "base_state_hash": base_state_hash,
            "context_hash": context_hash,
        }
    )[:24]


def generate_chapter_memory_proposal(
    project: NovelProject,
    ledger: ChapterFactLedger,
    dsh,
    *,
    base_state: dict[str, Any],
    canon_context: str = "",
    input_token_budget: int = DEFAULT_INPUT_TOKEN_BUDGET,
    reduce_batch_tokens: int = DEFAULT_REDUCE_BATCH_TOKENS,
    estimator: ConservativeTokenEstimator = DEFAULT_TOKEN_ESTIMATOR,
    cache: ChapterMemoryCache | None = None,
    cancel_event: threading.Event | None = None,
    base_state_scope: str = "",
    source_state_hash: str = "",
    force_refresh: bool = False,
) -> ChapterMemoryProposal:
    """Reduce a fact ledger and produce one locally validated memory proposal."""
    full_canon_context = str(canon_context or "")
    context_hash = canonical_hash(full_canon_context)
    canon_context = full_canon_context[:12_000]
    proposal_cache = cache or ChapterMemoryCache(project)
    cached = None if force_refresh else proposal_cache.load(ledger, base_state, context_hash)
    if cached is not None:
        return _replace_proposal(
            cached,
            base_state_scope=base_state_scope,
            source_state_hash=source_state_hash,
        )

    relevant_state = _relevant_story_state(base_state, ledger)
    prompt_state = relevant_state
    prompt_canon = canon_context
    base_state_hash = canonical_hash(base_state)
    cached_reduction = proposal_cache.load_reduction(ledger)
    source: dict[str, Any] = (
        {**cached_reduction, "base_state_hash": base_state_hash}
        if cached_reduction is not None
        else {
            "mode": "fact_ledger",
            "base_state_hash": base_state_hash,
            **_ledger_payload(ledger),
        }
    )
    reduction_calls = 0
    prompt = _try_build_memory_prompt(
        ledger,
        source,
        prompt_state,
        prompt_canon,
        input_token_budget,
        estimator,
        context_hash,
    )
    round_number = 0
    while prompt is None:
        round_number += 1
        if round_number > MAX_REDUCE_ROUNDS:
            raise MemoryProposalBudgetError("章节事实经过多轮归并后仍超过 token 预算。")
        _check_cancel(cancel_event)
        items = _source_items(source)
        batches = _pack_items(
            items,
            min(reduce_batch_tokens, max(1_000, input_token_budget // 2)),
            estimator,
        )
        if len(items) <= 1 or not batches:
            if prompt_canon:
                prompt_canon = prompt_canon[: len(prompt_canon) // 2]
            elif _character_count(prompt_state) > 5:
                prompt_state = _trim_state_characters(prompt_state)
            else:
                raise MemoryProposalBudgetError(
                    "章节记忆提案的固定上下文超过 token 预算。"
                )
            prompt = _try_build_memory_prompt(
                ledger,
                source,
                prompt_state,
                prompt_canon,
                input_token_budget,
                estimator,
                context_hash,
            )
            continue
        shards: list[DigestShard] = []
        for batch_index, batch in enumerate(batches, 1):
            _check_cancel(cancel_event)
            shard_prompt, allowed_ids = build_digest_shard_prompt(
                ledger,
                batch,
                batch_index=batch_index,
                batch_count=len(batches),
                input_token_budget=input_token_budget,
                estimator=estimator,
            )
            options = {"cancel_event": cancel_event} if cancel_event is not None else {}
            raw = dsh.generate_json(
                shard_prompt.system_prompt,
                shard_prompt.user_prompt,
                context_report=shard_prompt.report,
                **options,
            )
            shards.append(parse_digest_shard(raw, allowed_ids))
            reduction_calls += 1
        source = {
            "mode": "digest_shards",
            "base_state_hash": base_state_hash,
            "shards": [item.to_dict() for item in shards],
        }
        prompt = _try_build_memory_prompt(
            ledger,
            source,
            prompt_state,
            prompt_canon,
            input_token_budget,
            estimator,
            context_hash,
        )

    _check_cancel(cancel_event)
    if source.get("mode") == "digest_shards" and reduction_calls:
        proposal_cache.save_reduction(ledger, source)
    options = {"cancel_event": cancel_event} if cancel_event is not None else {}
    raw = dsh.generate_json(
        prompt.system_prompt,
        prompt.user_prompt,
        context_report=prompt.report,
        **options,
    )
    proposal = parse_memory_proposal(
        raw,
        ledger,
        base_state,
        expected_context_hash=context_hash,
        base_state_scope=base_state_scope,
        source_state_hash=source_state_hash,
    )
    proposal = _replace_proposal(proposal, reduction_calls=reduction_calls)
    proposal_cache.save(proposal)
    return proposal


def build_memory_proposal_prompt(
    ledger: ChapterFactLedger,
    source: dict[str, Any],
    relevant_state: dict[str, Any],
    canon_context: str,
    *,
    context_hash: str,
    input_token_budget: int = DEFAULT_INPUT_TOKEN_BUDGET,
    estimator: ConservativeTokenEstimator = DEFAULT_TOKEN_ESTIMATOR,
) -> PromptBundle:
    # Full source hashes remain local. The model only echoes one compact request
    # id, which binds its response to the complete local snapshot.
    base_state_hash = str(source.get("base_state_hash") or "")
    if not base_state_hash:
        raise MemoryProposalError("记忆提案缺少完整旧状态哈希。")
    request_id = memory_request_id(
        ledger.chapter_id,
        ledger.chapter_hash,
        base_state_hash,
        context_hash,
    )
    system_prompt = (
        "你是 DeepSonder 的章节记忆归并器。只能依据给定事实生成摘要、状态 Patch 和冲突候选。"
        "不得补写剧情，不得返回完整故事状态。只输出合法 JSON。"
    )
    model_source = {
        key: value
        for key, value in source.items()
        if key not in {"base_state_hash", "chapter_hash", "chapter_id"}
    }
    source_json = json.dumps(model_source, ensure_ascii=False, separators=(",", ":"))
    state_json = json.dumps(relevant_state, ensure_ascii=False, separators=(",", ":"))
    canon_text = canon_context or "（无相关设定）"
    user_prompt = f"""
任务类型：chapter_memory_suggestion
协议版本：{MEMORY_SUGGESTION_SCHEMA_VERSION}
请求 ID：{request_id}

只生成一份章节摘要，唯一权威字段是 digest.summary，不超过 300 个中文字符。
不要在顶层重复输出 summary。digest 中除 summary 外的每项都必须引用 fact_ids。
changes 只能使用以下 kind：{", ".join(sorted(PATCH_KINDS))}。
set_character_field 的 field 只能是：{", ".join(sorted(CHARACTER_FIELDS))}。
不得修改 current_chapter、foreshadowing，不得删除角色，不得创建任意字段。
每条 change 只描述目标和新值；旧值及写入前置条件由本地程序从旧状态计算。
每条 change 必须带 value、evidence_fact_ids 和 certainty；
certainty 只能是 explicit 或 inferred。缺少依据时不要生成 change，应生成冲突候选。
“相关旧故事状态”严格表示本章开始前的状态；正文明确写出的变化属于正常状态转移，
不能仅因新旧值不同就报告冲突。只有正文内部互相矛盾，或变化违背给定设定时才报告冲突。
change 参数约束：
- set_current_location：subject 和 field 为空，value 为新地点。
- set_character_field：subject 为角色名，value 为新字段值。
- add_character_item：field 为空，value 为新增物品名。
- remove_character_item：field 为空，value 为移除物品名。
- set_character_relation：subject 为角色名，field 为对方角色名，value 为新关系。
位置 Patch 还必须遵守：
- 群体地点事实可直接生成 set_current_location。
- set_character_field/location 必须同时引用地点事实，以及明确提到该角色在该地点或随队到达的事实；
  两类依据可以是不同 fact。不能只凭“一行人”“众人”等匿名群体事实批量复制个人位置。
- value 只写引用事实能够直接支持的简洁地点名，不得加入未被事实支持的“背风”、
  “歇息”等修饰或动作。
旧状态中以“…”结尾的值是截断展示，不要据此生成覆盖 Patch。

【章节事实或归并片段】
{source_json}

【相关旧故事状态】
{state_json}

【相关角色卡、世界观和时间线】
{canon_text}

返回格式：
{{
  "type": "chapter_memory_suggestion",
  "schema_version": {MEMORY_SUGGESTION_SCHEMA_VERSION},
  "request_id": "{request_id}",
  "digest": {{
    "summary": "章节摘要",
    "key_events": [{{"text": "事件", "fact_ids": ["fact_xxx"]}}],
    "character_changes": [],
    "location_changes": [],
    "item_changes": [],
    "relationship_changes": [],
    "timeline_changes": []
  }},
  "changes": [{{
    "kind": "set_character_field",
    "subject": "角色名",
    "field": "location",
    "value": "新地点",
    "evidence_fact_ids": ["fact_xxx"],
    "certainty": "explicit"
  }}],
  "conflicts": [{{
    "kind": "state_conflict",
    "severity": "warning",
    "target": "characters.角色名.location",
    "description": "冲突说明",
    "evidence_fact_ids": ["fact_xxx"]
  }}]
}}
""".strip()
    estimated = estimator.estimate_pair(system_prompt, user_prompt)
    budget = max(2_000, int(input_token_budget))
    report = PromptContextReport(
        schema_version=1,
        task_kind="chapter_memory_proposal",
        chapter_id=ledger.chapter_id,
        prompt_budget=len(system_prompt) + len(user_prompt),
        context_budget=len(source_json) + len(state_json) + len(canon_text),
        overhead_chars=(
            len(system_prompt)
            + len(user_prompt)
            - len(source_json)
            - len(state_json)
            - len(canon_text)
        ),
        system_prompt_chars=len(system_prompt),
        user_prompt_chars=len(user_prompt),
        total_prompt_chars=len(system_prompt) + len(user_prompt),
        sections=(
            SectionUsage("facts", len(source_json), len(source_json), "full", 0, "head"),
            SectionUsage("state", len(state_json), len(state_json), "full", 0, "head"),
            SectionUsage("canon", len(canon_text), len(canon_text), "full", 1, "head"),
        ),
        input_token_budget=budget,
        estimated_input_tokens=estimated,
        token_estimator=estimator.name,
    )
    if estimated > budget:
        raise MemoryProposalBudgetError(
            f"章节记忆提案超过 token 预算（估算 {estimated} > {budget}）。"
        )
    return PromptBundle(system_prompt, user_prompt, report)


def build_digest_shard_prompt(
    ledger: ChapterFactLedger,
    items: list[dict[str, Any]],
    *,
    batch_index: int,
    batch_count: int,
    input_token_budget: int,
    estimator: ConservativeTokenEstimator,
) -> tuple[PromptBundle, frozenset[str]]:
    allowed_ids = frozenset(_collect_fact_ids(items))
    system_prompt = (
        "你是 DeepSonder 的事实归并器。只压缩输入事实，不增加新事实。只输出合法 JSON。"
    )
    payload = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    user_prompt = f"""
任务类型：chapter_digest_shard
章节 ID：{ledger.chapter_id}
章节正文哈希：{ledger.chapter_hash}
批次：{batch_index}/{batch_count}

将下列事实或归并片段压缩为短摘要和关键 claims。每条 claim 必须引用输入中存在的
fact_ids；优先保留人物状态、地点、物品、关系、时间线和世界规则变化。

【输入】
{payload}

返回格式：
{{
  "type": "chapter_digest_shard",
  "schema_version": {DIGEST_SHARD_SCHEMA_VERSION},
  "summary": "批次摘要",
  "claims": [{{"text": "关键事实", "fact_ids": ["fact_xxx"]}}]
}}
""".strip()
    estimated = estimator.estimate_pair(system_prompt, user_prompt)
    budget = max(2_000, int(input_token_budget))
    if estimated > budget:
        raise MemoryProposalBudgetError("单个事实归并批次超过 token 预算。")
    report = PromptContextReport(
        schema_version=1,
        task_kind="chapter_digest_shard",
        chapter_id=ledger.chapter_id,
        prompt_budget=len(system_prompt) + len(user_prompt),
        context_budget=len(payload),
        overhead_chars=len(system_prompt) + len(user_prompt) - len(payload),
        system_prompt_chars=len(system_prompt),
        user_prompt_chars=len(user_prompt),
        total_prompt_chars=len(system_prompt) + len(user_prompt),
        sections=(SectionUsage("facts", len(payload), len(payload), "full", 0, "head"),),
        input_token_budget=budget,
        estimated_input_tokens=estimated,
        token_estimator=estimator.name,
    )
    return PromptBundle(system_prompt, user_prompt, report), allowed_ids


def parse_digest_shard(raw: str | dict[str, Any], allowed_ids: Iterable[str]) -> DigestShard:
    value = _as_object(raw, "章节事实归并")
    if value.get("type") != "chapter_digest_shard":
        raise MemoryProposalError("返回内容不是章节事实归并片段。")
    if value.get("schema_version") != DIGEST_SHARD_SCHEMA_VERSION:
        raise MemoryProposalError("章节事实归并协议版本不匹配。")
    allowed = frozenset(str(item) for item in allowed_ids)
    summary = _bounded_text(value.get("summary"), "summary", 2_000)
    claims = _parse_digest_entries(value.get("claims"), allowed, "claims", maximum=40)
    result = DigestShard(summary, claims)
    if len(json.dumps(result.to_dict(), ensure_ascii=False)) > 6_000:
        raise MemoryProposalError("章节事实归并片段超过安全长度。")
    return result


def parse_memory_proposal(
    raw: str | dict[str, Any],
    ledger: ChapterFactLedger,
    base_state: dict[str, Any],
    *,
    expected_context_hash: str,
    base_state_scope: str = "",
    source_state_hash: str = "",
) -> ChapterMemoryProposal:
    value = _as_object(raw, "章节记忆提案")
    if value.get("schema_version") != MEMORY_SUGGESTION_SCHEMA_VERSION:
        raise MemoryProposalError(
            "记忆任务返回了已停用或不受支持的协议版本，请重新运行“更新记忆”。"
        )
    base_state_hash = canonical_hash(base_state)
    request_id = memory_request_id(
        ledger.chapter_id,
        ledger.chapter_hash,
        base_state_hash,
        expected_context_hash,
    )
    fact_map = {item.fact_id: item for item in ledger.facts}
    allowed_ids = frozenset(fact_map)
    suggestion = parse_memory_suggestion(
        value,
        allowed_ids=allowed_ids,
        expected_request_id=request_id,
    )
    patches = _materialize_patches(suggestion.changes, base_state)
    resulting_state, local_conflicts = apply_memory_patches(
        base_state,
        patches,
        fact_map,
        ledger.chapter_id,
    )
    fact_conflicts = _detect_fact_conflicts(ledger.facts)
    conflicts = _dedupe_conflicts(
        [*suggestion.conflicts, *fact_conflicts, *local_conflicts]
    )
    return ChapterMemoryProposal(
        chapter_id=ledger.chapter_id,
        chapter_hash=ledger.chapter_hash,
        base_state_hash=base_state_hash,
        context_hash=expected_context_hash,
        digest=suggestion.digest,
        patches=patches,
        conflicts=conflicts,
        resulting_state=resulting_state,
        evidence_facts=ledger.facts,
        base_state_scope=str(base_state_scope or ""),
        source_state_hash=str(source_state_hash or ""),
    )


def parse_memory_suggestion(
    raw: str | dict[str, Any],
    *,
    allowed_ids: frozenset[str],
    expected_request_id: str,
) -> ChapterMemorySuggestion:
    """Parse only the minimal untrusted V2 model response."""
    value = _as_object(raw, "章节记忆建议")
    if value.get("type") != "chapter_memory_suggestion":
        raise MemoryProposalError("返回内容不是章节记忆建议。")
    if value.get("schema_version") != MEMORY_SUGGESTION_SCHEMA_VERSION:
        raise MemoryProposalError("章节记忆建议协议版本不匹配。")
    if str(value.get("request_id") or "") != expected_request_id:
        raise MemoryProposalError("章节记忆建议的 request_id 与当前任务不一致。")

    digest_value = value.get("digest")
    if not isinstance(digest_value, dict):
        raise MemoryProposalError("章节记忆建议缺少 digest 对象。")
    digest_summary = _bounded_text(digest_value.get("summary"), "digest.summary", 500)
    digest_fields = {
        field: _parse_digest_entries(
            digest_value.get(field, []),
            allowed_ids,
            field,
            maximum=100,
        )
        for field in DIGEST_FIELDS
    }
    digest = ChapterDigest(summary=digest_summary, **digest_fields)

    raw_changes = value.get("changes")
    if not isinstance(raw_changes, list) or len(raw_changes) > 100:
        raise MemoryProposalError("章节记忆建议的 changes 无效或过多。")
    changes = _parse_patch_suggestions(raw_changes, allowed_ids)

    raw_conflicts = value.get("conflicts")
    if not isinstance(raw_conflicts, list) or len(raw_conflicts) > 100:
        raise MemoryProposalError("章节记忆建议的 conflicts 无效或过多。")
    model_conflicts = _parse_model_conflicts(raw_conflicts, allowed_ids)
    return ChapterMemorySuggestion(
        request_id=expected_request_id,
        digest=digest,
        changes=changes,
        conflicts=model_conflicts,
    )


def parse_cached_memory_proposal(
    raw: str | dict[str, Any],
    ledger: ChapterFactLedger,
    base_state: dict[str, Any],
    *,
    expected_context_hash: str,
) -> ChapterMemoryProposal:
    """Load the trusted local cache without treating it as a model response."""
    value = _as_object(raw, "章节记忆缓存")
    if value.get("type") != "chapter_memory_proposal_cache":
        raise MemoryProposalError("返回内容不是章节记忆缓存。")
    if value.get("cache_schema_version") != MEMORY_CACHE_SCHEMA_VERSION:
        raise MemoryProposalError("章节记忆缓存版本不匹配。")
    if value.get("source_protocol_version") != MEMORY_SUGGESTION_SCHEMA_VERSION:
        raise MemoryProposalError("章节记忆缓存来源协议版本不匹配。")
    base_state_hash = canonical_hash(base_state)
    expected = {
        "chapter_id": ledger.chapter_id,
        "chapter_hash": ledger.chapter_hash,
        "base_state_hash": base_state_hash,
        "context_hash": expected_context_hash,
    }
    for key, expected_value in expected.items():
        if str(value.get(key) or "") != expected_value:
            raise MemoryProposalError(f"章节记忆缓存的 {key} 与当前任务不一致。")
    request_id = memory_request_id(
        ledger.chapter_id,
        ledger.chapter_hash,
        base_state_hash,
        expected_context_hash,
    )
    return parse_memory_proposal(
        {
            "type": "chapter_memory_suggestion",
            "schema_version": MEMORY_SUGGESTION_SCHEMA_VERSION,
            "request_id": request_id,
            "digest": value.get("digest"),
            "changes": value.get("changes"),
            "conflicts": value.get("conflicts"),
        },
        ledger,
        base_state,
        expected_context_hash=expected_context_hash,
    )


def apply_memory_patches(
    base_state: dict[str, Any],
    patches: tuple[MemoryPatch, ...],
    facts: dict[str, FactRecord],
    chapter_id: str,
) -> tuple[dict[str, Any], tuple[MemoryConflict, ...]]:
    state = copy.deepcopy(base_state if isinstance(base_state, dict) else {})
    characters = state.get("characters")
    if not isinstance(characters, dict):
        characters = {}
        state["characters"] = characters
    conflicts: list[MemoryConflict] = []
    targets: dict[str, MemoryPatch] = {}

    for patch in patches:
        target = _patch_target(patch)
        previous = targets.get(target)
        if previous is not None and (
            previous.kind != patch.kind
            or canonical_hash(previous.value) != canonical_hash(patch.value)
        ):
            conflicts.append(
                _local_conflict(
                    "patch_collision",
                    "blocker",
                    target,
                    "多条记忆 Patch 尝试为同一目标写入不同结果。",
                    (*previous.evidence_fact_ids, *patch.evidence_fact_ids),
                    (previous.op_id, patch.op_id),
                )
            )
            continue
        targets[target] = patch

        evidence = [facts[item] for item in patch.evidence_fact_ids if item in facts]
        if not evidence:
            conflicts.append(
                _local_conflict(
                    "unsupported_patch",
                    "blocker",
                    target,
                    "记忆 Patch 没有可验证的章节事实依据。",
                    patch.evidence_fact_ids,
                    (patch.op_id,),
                )
            )
            continue
        supported, value_supported = _evidence_supports_patch(patch, evidence)
        if not supported:
            conflicts.append(
                _local_conflict(
                    "unsupported_patch",
                    "blocker",
                    target,
                    "记忆 Patch 引用的事实与目标角色或字段类别不匹配。",
                    patch.evidence_fact_ids,
                    (patch.op_id,),
                )
            )
            continue
        if not value_supported:
            conflicts.append(
                _local_conflict(
                    "evidence_value_mismatch",
                    "warning",
                    target,
                    "Patch 的新值没有直接出现在引用事实中，需要人工确认。",
                    patch.evidence_fact_ids,
                    (patch.op_id,),
                )
            )
        if patch.certainty == "inferred" or any(item.certainty != "explicit" for item in evidence):
            conflicts.append(
                _local_conflict(
                    "inferred_patch",
                    "warning",
                    target,
                    "该状态变更包含推断性依据，需要人工确认。",
                    patch.evidence_fact_ids,
                    (patch.op_id,),
                )
            )

        current = _current_patch_value(state, patch)
        if current != patch.expected_before:
            conflicts.append(
                _local_conflict(
                    "state_precondition",
                    "blocker",
                    target,
                    "记忆 Patch 的 expected_before 与当前故事状态不一致。",
                    patch.evidence_fact_ids,
                    (patch.op_id,),
                )
            )
            continue
        try:
            _apply_patch_value(state, patch)
        except MemoryProposalError as exc:
            conflicts.append(
                _local_conflict(
                    "invalid_transition",
                    "blocker",
                    target,
                    str(exc),
                    patch.evidence_fact_ids,
                    (patch.op_id,),
                )
            )

    expected_chapter = chapter_number_from_id(chapter_id)
    if expected_chapter is not None:
        state["current_chapter"] = expected_chapter
    # This field is author-owned; even a malformed state object cannot be
    # changed through the proposal protocol.
    if "foreshadowing" in base_state:
        state["foreshadowing"] = copy.deepcopy(base_state["foreshadowing"])
    return state, _dedupe_conflicts(conflicts)


def _evidence_supports_patch(
    patch: MemoryPatch,
    evidence: list[FactRecord],
) -> tuple[bool, bool]:
    category_map = {
        "set_current_location": {"location", "event"},
        "set_character_field": {"character_state", "location", "event", "world_rule"},
        "add_character_item": {"item", "event", "character_state"},
        "remove_character_item": {"item", "event", "character_state"},
        "set_character_relation": {"relationship", "event", "character_state"},
    }
    subject = patch.subject.casefold()
    category_candidates = [
        fact
        for fact in evidence
        if fact.category in category_map[patch.kind]
    ]
    if patch.kind == "set_current_location":
        candidates = category_candidates
    elif patch.kind == "set_character_field" and patch.field == "location":
        # A destination can be expressed as a group location while a second
        # fact proves that the named character is part of that scene. Requiring
        # both properties on one fact rejected legitimate group movements.
        subject_candidates = [
            fact for fact in category_candidates if _fact_mentions_subject(fact, subject)
        ]
        location_candidates = [
            fact
            for fact in category_candidates
            if fact.category == "location"
            or _fact_mentions_location_change(fact)
        ]
        if not subject_candidates or not location_candidates:
            return False, False
        candidates = category_candidates
    else:
        candidates = [
            fact
            for fact in category_candidates
            if _fact_mentions_subject(fact, subject)
        ]
    if not candidates:
        return False, False
    expected_text = str(patch.value or "").strip().casefold()
    if not expected_text:
        return True, True
    value_supported = any(
        expected_text in fact.value.casefold()
        or expected_text in fact.predicate.casefold()
        for fact in candidates
    )
    return True, value_supported


def _fact_mentions_subject(fact: FactRecord, subject: str) -> bool:
    if not subject:
        return False
    fact_subject = fact.subject.casefold()
    return bool(
        fact_subject == subject
        or subject in fact_subject
        or subject in fact.predicate.casefold()
        or subject in fact.value.casefold()
    )


def _fact_mentions_location_change(fact: FactRecord) -> bool:
    text = f"{fact.predicate} {fact.value}".casefold()
    return any(
        marker in text
        for marker in (
            "抵达",
            "到达",
            "来到",
            "进入",
            "深入",
            "撤至",
            "退至",
            "迁往",
            "移至",
            "停下",
            "扎营",
            "驻扎",
            "休整",
        )
    )


def _try_build_memory_prompt(
    ledger: ChapterFactLedger,
    source: dict[str, Any],
    relevant_state: dict[str, Any],
    canon_context: str,
    input_token_budget: int,
    estimator: ConservativeTokenEstimator,
    context_hash: str,
) -> PromptBundle | None:
    enriched = {**source, "base_state_hash": source.get("base_state_hash")}
    if not enriched.get("base_state_hash"):
        # The caller adds this after constructing its compact projection.
        raise MemoryProposalError("归并来源缺少 base_state_hash。")
    try:
        return build_memory_proposal_prompt(
            ledger,
            enriched,
            relevant_state,
            canon_context,
            context_hash=context_hash,
            input_token_budget=input_token_budget,
            estimator=estimator,
        )
    except MemoryProposalBudgetError:
        return None


def _ledger_payload(ledger: ChapterFactLedger) -> dict[str, Any]:
    return {
        "chapter_id": ledger.chapter_id,
        "chapter_hash": ledger.chapter_hash,
        "chunks": [
            {"chunk_id": item.chunk_id, "summary": item.chunk_summary}
            for item in ledger.chunks
        ],
        "facts": [item.to_dict() for item in ledger.facts],
        "unknowns": [item.to_dict() for item in ledger.unknowns],
    }


def _source_items(source: dict[str, Any]) -> list[dict[str, Any]]:
    if source.get("mode") == "digest_shards":
        return [dict(item) for item in source.get("shards", []) if isinstance(item, dict)]
    items: list[dict[str, Any]] = []
    items.extend(
        {"kind": "chunk_summary", **item}
        for item in source.get("chunks", [])
        if isinstance(item, dict)
    )
    items.extend(
        {"kind": "fact", **item}
        for item in source.get("facts", [])
        if isinstance(item, dict)
    )
    items.extend(
        {"kind": "unknown", **item}
        for item in source.get("unknowns", [])
        if isinstance(item, dict)
    )
    return items


def _pack_items(
    items: list[dict[str, Any]],
    target_tokens: int,
    estimator: ConservativeTokenEstimator,
) -> list[list[dict[str, Any]]]:
    target = max(1_000, int(target_tokens))
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for item in items:
        candidate = [*current, item]
        rendered = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
        if current and estimator.estimate(rendered) > target:
            batches.append(current)
            current = [item]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def _relevant_story_state(
    base_state: dict[str, Any],
    ledger: ChapterFactLedger,
) -> dict[str, Any]:
    compacted = compact_story_state(base_state, value_cap=300, max_list_items=20)
    characters = compacted.get("characters")
    if not isinstance(characters, dict):
        return compacted
    subjects = {item.subject.casefold() for item in ledger.facts if item.subject.strip()}
    selected: dict[str, Any] = {}
    for name, value in characters.items():
        normalized = str(name).casefold()
        if normalized in subjects or any(normalized in subject for subject in subjects):
            selected[str(name)] = value
        if len(selected) >= 50:
            break
    compacted["characters"] = selected
    compacted.pop("foreshadowing", None)
    return compacted


def _character_count(state: dict[str, Any]) -> int:
    characters = state.get("characters") if isinstance(state, dict) else None
    return len(characters) if isinstance(characters, dict) else 0


def _trim_state_characters(state: dict[str, Any]) -> dict[str, Any]:
    trimmed = copy.deepcopy(state)
    characters = trimmed.get("characters")
    if not isinstance(characters, dict):
        return trimmed
    keep = max(5, len(characters) // 2)
    trimmed["characters"] = dict(list(characters.items())[:keep])
    return trimmed


def _parse_digest_entries(
    value: object,
    allowed_ids: frozenset[str],
    label: str,
    *,
    maximum: int,
) -> tuple[DigestEntry, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise MemoryProposalError(f"{label} 必须是受限数组。")
    result: list[DigestEntry] = []
    for item in value:
        if not isinstance(item, dict):
            raise MemoryProposalError(f"{label} 包含无效条目。")
        text = _bounded_text(item.get("text"), f"{label}.text", 500)
        ids = _parse_fact_ids(item.get("fact_ids"), allowed_ids, allow_empty=False)
        result.append(DigestEntry(text, ids))
    return tuple(result)


def _parse_patch_suggestions(
    values: list[object],
    allowed_ids: frozenset[str],
) -> tuple[MemoryPatchSuggestion, ...]:
    result: list[MemoryPatchSuggestion] = []
    for item in values:
        if not isinstance(item, dict):
            raise MemoryProposalError("changes 包含无效条目。")
        kind = str(item.get("kind") or "").strip()
        if kind not in PATCH_KINDS:
            raise MemoryProposalError(f"不支持的记忆 Patch：{kind}")
        subject = str(item.get("subject") or "").strip()
        field = str(item.get("field") or "").strip()
        patch_value = item.get("value")
        evidence = _parse_fact_ids(
            item.get("evidence_fact_ids"),
            allowed_ids,
            allow_empty=False,
        )
        certainty = str(item.get("certainty") or "").strip().casefold()
        if certainty not in {"explicit", "inferred"}:
            raise MemoryProposalError("记忆 Patch 包含无效 certainty。")
        result.append(
            MemoryPatchSuggestion(
                kind,
                subject,
                field,
                patch_value,
                evidence,
                certainty,
            )
        )
    return tuple(result)


def _materialize_patches(
    suggestions: tuple[MemoryPatchSuggestion, ...],
    base_state: dict[str, Any],
) -> tuple[MemoryPatch, ...]:
    """Attach local compare-and-set preconditions and stable operation ids."""
    result: list[MemoryPatch] = []
    seen: set[str] = set()
    for item in suggestions:
        probe = MemoryPatch(
            "",
            item.kind,
            item.subject,
            item.field,
            None,
            item.value,
            item.evidence_fact_ids,
            item.certainty,
        )
        expected_before = _current_patch_value(base_state, probe)
        _validate_patch_shape(
            item.kind,
            item.subject,
            item.field,
            expected_before,
            item.value,
        )
        op_id = "op_" + canonical_hash(
            {
                "kind": item.kind,
                "subject": item.subject,
                "field": item.field,
                "expected_before": expected_before,
                "value": item.value,
                "evidence": item.evidence_fact_ids,
            }
        )[:20]
        if op_id in seen:
            continue
        seen.add(op_id)
        result.append(
            MemoryPatch(
                op_id,
                item.kind,
                item.subject,
                item.field,
                expected_before,
                item.value,
                item.evidence_fact_ids,
                item.certainty,
            )
        )
    return tuple(result)


def _validate_patch_shape(
    kind: str,
    subject: str,
    field: str,
    expected_before: Any,
    value: Any,
) -> None:
    if len(subject) > 200 or len(field) > 200:
        raise MemoryProposalError("记忆 Patch 的目标字段过长。")
    if len(json.dumps([expected_before, value], ensure_ascii=False, default=str)) > 4_000:
        raise MemoryProposalError("记忆 Patch 的值超过安全长度。")
    if kind == "set_current_location":
        if subject or field or not isinstance(value, str) or len(value) > 300:
            raise MemoryProposalError("set_current_location 参数无效。")
        if expected_before is not None and not isinstance(expected_before, str):
            raise MemoryProposalError("set_current_location 的 expected_before 无效。")
        return
    if not subject:
        raise MemoryProposalError("角色记忆 Patch 缺少 subject。")
    if kind == "set_character_field":
        if field not in CHARACTER_FIELDS or not isinstance(value, str) or len(value) > 2_000:
            raise MemoryProposalError("set_character_field 参数无效。")
        if expected_before is not None and not isinstance(expected_before, str):
            raise MemoryProposalError("set_character_field 的 expected_before 无效。")
    elif kind in {"add_character_item", "remove_character_item"}:
        if field or not isinstance(value, str) or not value.strip() or len(value) > 500:
            raise MemoryProposalError("角色物品 Patch 参数无效。")
        if not isinstance(expected_before, bool):
            raise MemoryProposalError("角色物品 Patch 的 expected_before 必须是布尔值。")
    elif kind == "set_character_relation":
        if not field or not isinstance(value, str) or len(value) > 500:
            raise MemoryProposalError("角色关系 Patch 参数无效。")
        if expected_before is not None and not isinstance(expected_before, str):
            raise MemoryProposalError("角色关系 Patch 的 expected_before 无效。")


def _parse_model_conflicts(
    values: list[object],
    allowed_ids: frozenset[str],
) -> tuple[MemoryConflict, ...]:
    result: list[MemoryConflict] = []
    for item in values:
        if not isinstance(item, dict):
            raise MemoryProposalError("conflicts 包含无效条目。")
        kind = str(item.get("kind") or "").strip()
        severity = str(item.get("severity") or "").strip().casefold()
        if kind not in MODEL_CONFLICT_KINDS or severity not in CONFLICT_SEVERITIES:
            raise MemoryProposalError("记忆冲突类型或等级无效。")
        target = _bounded_text(item.get("target"), "conflict.target", 500)
        description = _bounded_text(
            item.get("description"), "conflict.description", 1_000
        )
        ids = _parse_fact_ids(
            item.get("evidence_fact_ids", []),
            allowed_ids,
            allow_empty=True,
        )
        conflict_id = "conflict_" + canonical_hash(
            [kind, severity, target, description, ids]
        )[:20]
        result.append(
            MemoryConflict(conflict_id, kind, severity, target, description, ids)
        )
    return tuple(result)


def _parse_fact_ids(
    value: object,
    allowed_ids: frozenset[str],
    *,
    allow_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 40:
        raise MemoryProposalError("事实引用必须是受限数组。")
    result: list[str] = []
    for item in value:
        fact_id = str(item or "").strip()
        if fact_id not in allowed_ids:
            raise MemoryProposalError("提案引用了当前事实账本中不存在的 fact_id。")
        if fact_id not in result:
            result.append(fact_id)
    if not allow_empty and not result:
        raise MemoryProposalError("提案缺少事实依据。")
    return tuple(result)


def _current_patch_value(state: dict[str, Any], patch: MemoryPatch) -> Any:
    if patch.kind == "set_current_location":
        return state.get("current_location")
    characters = state.get("characters", {})
    character = characters.get(patch.subject, {}) if isinstance(characters, dict) else {}
    if not isinstance(character, dict):
        character = {}
    if patch.kind == "set_character_field":
        return character.get(patch.field)
    if patch.kind in {"add_character_item", "remove_character_item"}:
        items = character.get("items", [])
        return isinstance(items, list) and patch.value in items
    relations = character.get("relations", {})
    return relations.get(patch.field) if isinstance(relations, dict) else None


def _apply_patch_value(state: dict[str, Any], patch: MemoryPatch) -> None:
    if patch.kind == "set_current_location":
        state["current_location"] = patch.value
        return
    characters = state.setdefault("characters", {})
    if not isinstance(characters, dict):
        raise MemoryProposalError("旧故事状态的 characters 不是对象。")
    character = characters.setdefault(patch.subject, {})
    if not isinstance(character, dict):
        raise MemoryProposalError("目标角色的旧状态不是对象。")
    if patch.kind == "set_character_field":
        character[patch.field] = patch.value
    elif patch.kind in {"add_character_item", "remove_character_item"}:
        items = character.setdefault("items", [])
        if not isinstance(items, list):
            raise MemoryProposalError("目标角色的 items 不是数组。")
        if patch.kind == "add_character_item":
            if patch.value not in items:
                items.append(patch.value)
        elif patch.value in items:
            items.remove(patch.value)
    elif patch.kind == "set_character_relation":
        relations = character.setdefault("relations", {})
        if not isinstance(relations, dict):
            raise MemoryProposalError("目标角色的 relations 不是对象。")
        relations[patch.field] = patch.value


def _patch_target(patch: MemoryPatch) -> str:
    if patch.kind == "set_current_location":
        return "current_location"
    if patch.kind == "set_character_field":
        return f"characters.{patch.subject}.{patch.field}"
    if patch.kind in {"add_character_item", "remove_character_item"}:
        return f"characters.{patch.subject}.items[{patch.value}]"
    return f"characters.{patch.subject}.relations.{patch.field}"


def _local_conflict(
    kind: str,
    severity: str,
    target: str,
    description: str,
    fact_ids: Iterable[str],
    op_ids: Iterable[str],
) -> MemoryConflict:
    facts = tuple(dict.fromkeys(str(item) for item in fact_ids if str(item)))
    operations = tuple(dict.fromkeys(str(item) for item in op_ids if str(item)))
    conflict_id = "conflict_" + canonical_hash(
        [kind, severity, target, description, facts, operations]
    )[:20]
    return MemoryConflict(
        conflict_id,
        kind,
        severity,
        target,
        description,
        facts,
        operations,
    )


def _detect_fact_conflicts(
    facts: tuple[FactRecord, ...],
) -> tuple[MemoryConflict, ...]:
    groups: dict[tuple[str, str, str, str], list[FactRecord]] = {}
    for fact in facts:
        key = (
            fact.category,
            fact.subject.casefold(),
            fact.predicate.casefold(),
            fact.anchor,
        )
        groups.setdefault(key, []).append(fact)
    conflicts: list[MemoryConflict] = []
    for values in groups.values():
        distinct = {item.value.casefold() for item in values}
        if len(distinct) <= 1:
            continue
        first = values[0]
        conflicts.append(
            _local_conflict(
                "fact_collision",
                "blocker",
                f"facts.{first.anchor}.{first.subject}.{first.predicate}",
                "同一正文锚点产生了互不相同的事实值。",
                (item.fact_id for item in values),
                (),
            )
        )
    return tuple(conflicts)


def _dedupe_conflicts(values: Iterable[MemoryConflict]) -> tuple[MemoryConflict, ...]:
    result: list[MemoryConflict] = []
    seen: set[str] = set()
    for value in values:
        if value.conflict_id in seen:
            continue
        seen.add(value.conflict_id)
        result.append(value)
    return tuple(result)


def _collect_fact_ids(value: object) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "fact_id" and str(item).startswith("fact_"):
                result.append(str(item))
            elif key in {"fact_ids", "evidence_fact_ids"} and isinstance(item, list):
                result.extend(str(entry) for entry in item if str(entry).startswith("fact_"))
            else:
                result.extend(_collect_fact_ids(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_collect_fact_ids(item))
    return list(dict.fromkeys(result))


def _as_object(raw: str | dict[str, Any], label: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = extract_json(str(raw or ""))
    except JSONExtractionError as exc:
        raise MemoryProposalError(f"{label}不是合法 JSON。") from exc
    if not isinstance(value, dict):
        raise MemoryProposalError(f"{label}不是 JSON 对象。")
    return dict(value)


def _bounded_text(value: object, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise MemoryProposalError(f"章节记忆提案缺少 {label}。")
    if len(text) > maximum:
        raise MemoryProposalError(f"章节记忆提案字段 {label} 超过安全长度。")
    return text


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise AITaskCancelled()


def _replace_proposal(
    proposal: ChapterMemoryProposal,
    *,
    cache_hit: bool | None = None,
    reduction_calls: int | None = None,
    base_state_scope: str | None = None,
    source_state_hash: str | None = None,
) -> ChapterMemoryProposal:
    return ChapterMemoryProposal(
        chapter_id=proposal.chapter_id,
        chapter_hash=proposal.chapter_hash,
        base_state_hash=proposal.base_state_hash,
        context_hash=proposal.context_hash,
        digest=proposal.digest,
        patches=proposal.patches,
        conflicts=proposal.conflicts,
        resulting_state=proposal.resulting_state,
        completion_message=proposal.completion_message,
        evidence_facts=proposal.evidence_facts,
        cache_hit=proposal.cache_hit if cache_hit is None else cache_hit,
        reduction_calls=(
            proposal.reduction_calls if reduction_calls is None else reduction_calls
        ),
        base_state_scope=(
            proposal.base_state_scope
            if base_state_scope is None
            else str(base_state_scope or "")
        ),
        source_state_hash=(
            proposal.source_state_hash
            if source_state_hash is None
            else str(source_state_hash or "")
        ),
    )


def _display_value(value: object) -> str:
    if value is None:
        return "（未设置）"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)
