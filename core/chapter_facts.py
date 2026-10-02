"""Chunk-level fact extraction, validation, deduplication and local caching."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .app_paths import app_cache_dir
from .context_report import PromptBundle, PromptContextReport, SectionUsage
from .json_utils import JSONExtractionError, extract_json
from .memory_progress import MemoryProgress, ParallelMemoryPhase, ProgressCallback, memory_phase, publish_progress
from .memory_task import MemoryTaskSession, memory_config_fingerprint
from .project import NovelProject
from .prompt_transport import estimate_task_input_tokens
from .storage import atomic_write_text
from .task_controller import AITaskCancelled
from .text_chunking import (
    ChapterChunk, bind_memory_chunk_context, chapter_content_hash, chunk_chapter,
    stable_memory_chunks,
)
from .token_budget import (
    DEFAULT_CHUNK_OVERLAP_TOKENS,
    DEFAULT_CHUNK_TOKEN_BUDGET,
    DEFAULT_INPUT_TOKEN_BUDGET,
    DEFAULT_TOKEN_ESTIMATOR,
    ConservativeTokenEstimator,
)


FACT_LEDGER_SCHEMA_VERSION = 1
FACT_PROMPT_VERSION = 4
FACT_CACHE_SCHEMA_VERSION = 2
FACT_CATEGORIES = frozenset(
    {
        "event",
        "character_state",
        "relationship",
        "location",
        "item",
        "foreshadowing",
        "timeline",
        "world_rule",
    }
)
FACT_CERTAINTIES = frozenset({"explicit", "inferred", "uncertain"})


class FactProtocolError(ValueError):
    """Raised when a chunk fact response cannot be trusted or cached."""


@dataclass(frozen=True)
class FactRecord:
    fact_id: str
    category: str
    subject: str
    predicate: str
    value: str
    anchor: str
    certainty: str

    def to_dict(self) -> dict[str, str]:
        return {
            "fact_id": self.fact_id,
            "category": self.category,
            "subject": self.subject,
            "predicate": self.predicate,
            "value": self.value,
            "anchor": self.anchor,
            "certainty": self.certainty,
        }


@dataclass(frozen=True)
class UnknownRecord:
    description: str
    anchor: str

    def to_dict(self) -> dict[str, str]:
        return {"description": self.description, "anchor": self.anchor}


@dataclass(frozen=True)
class ChunkFacts:
    chapter_id: str
    chunk_id: str
    source_hash: str
    chunk_summary: str
    facts: tuple[FactRecord, ...]
    unknowns: tuple[UnknownRecord, ...]
    completion_message: str = "分块事实提取完成"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "chapter_chunk_facts",
            "schema_version": FACT_LEDGER_SCHEMA_VERSION,
            "chapter_id": self.chapter_id,
            "chunk_id": self.chunk_id,
            "source_hash": self.source_hash,
            "chunk_summary": self.chunk_summary,
            "facts": [item.to_dict() for item in self.facts],
            "unknowns": [item.to_dict() for item in self.unknowns],
            "completion_message": self.completion_message,
        }


@dataclass(frozen=True)
class ChapterFactLedger:
    chapter_id: str
    chapter_hash: str
    chunks: tuple[ChunkFacts, ...]
    facts: tuple[FactRecord, ...]
    unknowns: tuple[UnknownRecord, ...]
    cache_hits: int
    extracted_chunks: int
    schema_version: int = FACT_LEDGER_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "chapter_fact_ledger",
            "schema_version": self.schema_version,
            "chapter_id": self.chapter_id,
            "chapter_hash": self.chapter_hash,
            "chunk_count": len(self.chunks),
            "cache_hits": self.cache_hits,
            "extracted_chunks": self.extracted_chunks,
            "facts": [item.to_dict() for item in self.facts],
            "unknowns": [item.to_dict() for item in self.unknowns],
        }


class FactLedgerCache:
    """Disposable cache containing facts, never raw chapter chunk text."""

    def __init__(self, project: NovelProject, root: Path | None = None):
        project_key = hashlib.sha256(
            str(project.root.resolve()).casefold().encode("utf-8")
        ).hexdigest()[:24]
        self.root = Path(root) if root is not None else app_cache_dir() / "ai-facts-v2"
        self.project_dir = self.root / f"v{FACT_CACHE_SCHEMA_VERSION}" / project_key

    def load(self, chunk: ChapterChunk, *, request_context: str = "") -> ChunkFacts | None:
        path = self._path(chunk, request_context)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("schema_version") != FACT_CACHE_SCHEMA_VERSION:
                return None
            identity = self._identity(chunk, request_context)
            if value.get("identity") != identity or value.get("cache_key") != path.stem:
                return None
            payload = value["result"]
            if _cache_hash(payload) != value.get("result_hash"):
                return None
            # Free text is never rewritten. If it mentions source positions,
            # only the identical original position is allowed to load it.
            if value.get("position_bound") and value.get("original_chunk_id") != chunk.chunk_id:
                return None
            if value.get("position_bound") != _position_sensitive(payload, chunk.chapter_id):
                return None
            current = dict(payload)
            current.update(type="chapter_chunk_facts", schema_version=FACT_LEDGER_SCHEMA_VERSION,
                chapter_id=chunk.chapter_id, chunk_id=chunk.chunk_id, source_hash=chunk.content_hash)
            for key in ("facts", "unknowns"):
                current[key] = [self._restore_evidence(item, chunk) for item in payload[key]]
            # Recompute local fact IDs and validate every mapped anchor in the
            # current range. Stored IDs/absolute anchors are never authoritative.
            return parse_chunk_facts(current, chunk)
        except (OSError, UnicodeError, json.JSONDecodeError, FactProtocolError,
                KeyError, TypeError, ValueError):
            return None

    def save(self, chunk: ChapterChunk, result: ChunkFacts, *, request_context: str = "") -> None:
        validated = parse_chunk_facts(result.to_dict(), chunk)
        payload = {"chunk_summary": validated.chunk_summary,
            "completion_message": validated.completion_message,
            "facts": [self._relative_evidence(item.to_dict(), chunk) for item in validated.facts],
            "unknowns": [self._relative_evidence(item.to_dict(), chunk) for item in validated.unknowns]}
        path = self._path(chunk, request_context)
        value = {"schema_version": FACT_CACHE_SCHEMA_VERSION, "cache_key": path.stem,
            "identity": self._identity(chunk, request_context), "result": payload,
            "result_hash": _cache_hash(payload), "original_chunk_id": chunk.chunk_id,
            "position_bound": _position_sensitive(payload, chunk.chapter_id)}
        atomic_write_text(
            path, json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _identity(self, chunk: ChapterChunk, request_context: str) -> dict[str, Any]:
        return {"prompt_version": FACT_PROMPT_VERSION, "strategy": chunk.chunk_strategy,
            "chapter_id": chunk.chapter_id, "source_hash": chunk.content_hash,
            "evidence_hashes": list(chunk.evidence_hashes), "context_hashes": list(chunk.context_hashes),
            "context_transport_hash": _cache_hash([chunk.context_before, chunk.context_after]),
            "scope": chunk.reuse_scope if chunk.evidence_hashes else chunk.chunk_id,
            "request_context": request_context}

    def _path(self, chunk: ChapterChunk, request_context: str = "") -> Path:
        cache_key = _cache_hash(self._identity(chunk, request_context))
        chapter_key = hashlib.sha256(
            chunk.chapter_id.casefold().encode("utf-8")
        ).hexdigest()[:20]
        return self.project_dir / chapter_key / (cache_key + ".json")

    @staticmethod
    def _relative_evidence(item: dict, chunk: ChapterChunk) -> dict:
        item = dict(item)
        anchor = _validate_anchor(item.pop("anchor"), chunk)
        item.pop("fact_id", None)
        match = re.fullmatch(r".+:p(\d{4})(?:-p(\d{4}))?", anchor)
        start, end = int(match[1]), int(match[2] or match[1])
        item["relative_anchor"] = [start - chunk.start_paragraph, end - chunk.start_paragraph]
        item["anchor_is_range"] = match[2] is not None
        return item

    @staticmethod
    def _restore_evidence(item: dict, chunk: ChapterChunk) -> dict:
        item = dict(item)
        offsets = item.pop("relative_anchor")
        is_range = item.pop("anchor_is_range")
        if (not isinstance(offsets, list) or len(offsets) != 2
                or any(type(n) is not int for n in offsets) or type(is_range) is not bool):
            raise FactProtocolError("缓存的相对证据位置无效。")
        start, end = offsets
        count = chunk.end_paragraph - chunk.start_paragraph + 1
        if start < 0 or start > end or end >= count or (not is_range and start != end):
            raise FactProtocolError("缓存的相对证据位置超出当前分块。")
        # Entire ordered paragraph hashes are part of the checked identity.
        if chunk.evidence_hashes and len(chunk.evidence_hashes) != count:
            raise FactProtocolError("缓存的段落证据无法无歧义重映射。")
        item["anchor"] = f"{chunk.chapter_id}:p{chunk.start_paragraph + start:04d}"
        if is_range:
            item["anchor"] += f"-p{chunk.start_paragraph + end:04d}"
        return item


def _cache_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _position_sensitive(payload: dict, chapter_id: str) -> bool:
    text = json.dumps(payload, ensure_ascii=False)
    return bool(re.search(
        rf"{re.escape(chapter_id)}:[pc]\d{{4}}|(?<![A-Za-z0-9_])[pc]\d{{4}}(?![A-Za-z0-9_])",
        text, re.IGNORECASE))


def build_chunk_facts_prompt(
    chapter_title: str,
    chunk: ChapterChunk,
    *,
    input_token_budget: int = DEFAULT_INPUT_TOKEN_BUDGET,
    estimator: ConservativeTokenEstimator = DEFAULT_TOKEN_ESTIMATOR,
) -> PromptBundle:
    """Build one source-only extraction prompt with no existing story memory."""
    system_prompt = """
你是 DeepSonder 的章节事实提取器。只提取给定正文中能够定位的事实，不续写、不补全、
不依据常识推测。只输出合法 JSON，不输出 Markdown 代码围栏或额外解释。
""".strip()
    user_prompt = f"""
任务类型：chapter_chunk_facts
协议版本：{FACT_LEDGER_SCHEMA_VERSION}

请分析下列章节分块。每条事实必须使用本分块范围内的段落锚点，确定性只能是
explicit、inferred 或 uncertain；只有正文直接陈述或明确发生的内容才能标为 explicit。
location 类事实的 value 应优先写正文能够直接支持的简洁规范地点名，环境氛围和
停留动作写入 predicate，不要把未明确出现的“背风”“扎营”等修饰补入地点名。
群体移动时，subject 写正文中的群体称谓；若正文同时明确点名成员在场，可另提取
带角色名的 event 或 character_state 事实，便于安全更新个人位置。

输出要精炼：chunk_summary 建议不超过 120 字；subject、predicate、value 使用简洁表述。
优先保留事件、人物状态、位置、物品、关系、时间、世界规则和未解决线索的实质变化。
同一锚点的同一事实不要换词重复，不要把同一变化拆成多条空泛描述。
不得为追求简短遗漏独立事实、不同时间的状态变化、矛盾证据，或角色到达地点的依据。
事实较多时仍须完整保留，不按条数截断；氛围和修辞不单独列为事实。
邻接上下文仅用于消解本分块中的指代，不从上下文单独提取事实或锚点。
上下文可能是邻段的片段，不能确定指代时保留为 uncertain 或 unknowns，不补全。
摘要及事实文本不要包含分块编号或来源锚点；证据位置仅放在 anchor 字段。

章节：{str(chapter_title or chunk.chapter_id).strip()}
章节 ID：{chunk.chapter_id}
分块 ID：{chunk.chunk_id}
来源哈希：{chunk.content_hash}
允许锚点范围：p{chunk.start_paragraph:04d} 至 p{chunk.end_paragraph:04d}

【前邻段上下文，仅供指代消解】
{chunk.context_before or "（无）"}

【章节正文分块】
{chunk.annotated_text}

【后邻段上下文，仅供指代消解】
{chunk.context_after or "（无）"}

返回格式：
{{
  "type": "chapter_chunk_facts",
  "schema_version": {FACT_LEDGER_SCHEMA_VERSION},
  "chapter_id": "{chunk.chapter_id}",
  "chunk_id": "{chunk.chunk_id}",
  "source_hash": "{chunk.content_hash}",
  "chunk_summary": "只概括本分块发生的内容",
  "facts": [
    {{
      "category": "event",
      "subject": "事实主体",
      "predicate": "动作、状态或关系",
      "value": "事实值或结果",
      "anchor": "{chunk.chapter_id}:p{chunk.start_paragraph:04d}",
      "certainty": "explicit"
    }}
  ],
  "unknowns": [
    {{
      "description": "正文明确留下但没有答案的问题",
      "anchor": "{chunk.chapter_id}:p{chunk.start_paragraph:04d}"
    }}
  ],
  "completion_message": "分块事实提取完成"
}}

category 仅允许：{", ".join(sorted(FACT_CATEGORIES))}。
如果没有事实或未知项，返回空数组；不要为了填充格式制造内容。
""".strip()
    estimated = estimate_task_input_tokens(system_prompt, user_prompt, estimator)
    budget = max(0, int(input_token_budget))
    neighbor_chars = len(chunk.context_before) + len(chunk.context_after)
    context_chars = len(chunk.annotated_text) + neighbor_chars
    report = PromptContextReport(
        schema_version=1,
        task_kind="chapter_chunk_facts",
        chapter_id=chunk.chapter_id,
        prompt_budget=len(system_prompt) + len(user_prompt),
        context_budget=context_chars,
        overhead_chars=(
            len(system_prompt) + len(user_prompt) - context_chars
        ),
        system_prompt_chars=len(system_prompt),
        user_prompt_chars=len(user_prompt),
        total_prompt_chars=len(system_prompt) + len(user_prompt),
        sections=(
            SectionUsage(
                key="content",
                source_chars=len(chunk.text),
                sent_chars=len(chunk.annotated_text),
                status="full",
                priority=0,
                keep="head",
            ),
            SectionUsage(key="neighbor_context", source_chars=neighbor_chars,
                sent_chars=neighbor_chars, status="full", priority=0, keep="head"),
        ),
        input_token_budget=budget,
        estimated_input_tokens=estimated,
        token_estimator=estimator.name,
    )
    if estimated > budget:
        raise RuntimeError(
            "分块事实提示词超过 token 预算"
            f"（估算 {estimated} > {budget}）。请降低分块大小后重试。"
        )
    return PromptBundle(system_prompt, user_prompt, report)


def memory_chunks(
    chapter_id: str,
    content: str,
    chapter_title: str,
    *,
    chunk_token_budget: int,
    overlap_tokens: int,
    input_token_budget: int,
    estimator: ConservativeTokenEstimator,
) -> tuple[ChapterChunk, ...]:
    """Fit a medium chapter in one bounded request; never enlarge custom chunks.

    Use at most half the business input budget for chapter content.
    The exact prompt is also checked with transport wrapper headroom.
    Longer chapters use content-defined, bounded paragraph boundaries.
    """
    target = chunk_token_budget
    ceiling = min(8_000, input_token_budget // 2)
    if (
        target == DEFAULT_CHUNK_TOKEN_BUDGET
        and ceiling > target
        and estimator.estimate(content) <= ceiling
    ):
        candidates = chunk_chapter(chapter_id, content, target_tokens=ceiling,
                                   overlap_tokens=overlap_tokens, estimator=estimator)
        candidates = bind_memory_chunk_context(candidates, content, estimator=estimator)
        if len(candidates) == 1:
            try:
                build_chunk_facts_prompt(chapter_title, candidates[0],
                    input_token_budget=input_token_budget, estimator=estimator)
            except RuntimeError:
                pass
            else:
                return candidates
    # Preflight every chunk before spending an AI request. Reduce content only
    # by splitting it further, never by truncating the chapter.
    while True:
        chunks = stable_memory_chunks(chapter_id, content, target_tokens=target,
                               overlap_tokens=overlap_tokens, estimator=estimator)
        try:
            for chunk in chunks:
                build_chunk_facts_prompt(chapter_title, chunk,
                    input_token_budget=input_token_budget, estimator=estimator)
        except RuntimeError:
            if target <= 256:
                raise RuntimeError("事实提取的协议与传输包装超过可用预算，请增加输入空间。") from None
            target = max(256, target // 2)
        else:
            return chunks


def extract_chapter_fact_ledger(
    project: NovelProject,
    chapter_id: str,
    dsh,
    *,
    input_token_budget: int = DEFAULT_INPUT_TOKEN_BUDGET,
    chunk_token_budget: int = DEFAULT_CHUNK_TOKEN_BUDGET,
    overlap_tokens: int = DEFAULT_CHUNK_OVERLAP_TOKENS,
    estimator: ConservativeTokenEstimator = DEFAULT_TOKEN_ESTIMATOR,
    cache: FactLedgerCache | None = None,
    cancel_event: threading.Event | None = None,
    progress_callback: ProgressCallback | None = None,
    force_refresh: bool = False,
) -> ChapterFactLedger:
    """Extract every chapter chunk or load its validated cached result."""
    if cancel_event is not None and cancel_event.is_set():
        raise AITaskCancelled()
    chapter = project.load_chapter(chapter_id)
    content = chapter.content
    with memory_phase(progress_callback, "chunking"):
        chunks = memory_chunks(
            chapter_id, content, chapter.title,
            chunk_token_budget=chunk_token_budget,
            overlap_tokens=overlap_tokens,
            input_token_budget=input_token_budget,
            estimator=estimator,
        )
    ledger_cache = cache or FactLedgerCache(project)
    request_context = _cache_hash([str(chapter.title or chapter_id).strip(), memory_config_fingerprint(dsh)])
    results: list[ChunkFacts] = []
    cache_hits = 0
    extracted = 0
    if isinstance(dsh, MemoryTaskSession) and dsh.parallel_enabled and len(chunks) > 1:
        results, cache_hits, extracted = _extract_parallel_facts(dsh, chunks, chapter.title,
            ledger_cache, force_refresh, input_token_budget, estimator, cancel_event,
            progress_callback, request_context)
    else:
        for index, chunk in enumerate(chunks, 1):
            _check_fact_task(dsh, cancel_event)
            result = None if force_refresh else ledger_cache.load(chunk, request_context=request_context)
            _check_fact_task(dsh, cancel_event)
            if result is not None:
                cache_hits += 1
                results.append(result)
                publish_progress(progress_callback, MemoryProgress(
                    "facts", state="cached", current=index, total=len(chunks), cache_hits=cache_hits,
                    cache_reason="hit"
                ))
                continue
            prompt = build_chunk_facts_prompt(
                chapter.title,
                chunk,
                input_token_budget=input_token_budget,
                estimator=estimator,
            )
            options = {"cancel_event": cancel_event} if cancel_event is not None else {}
            with memory_phase(progress_callback, "facts", current=index,
                              total=len(chunks), cache_hits=cache_hits,
                              cache_reason="refresh" if force_refresh else "unmatched"):
                raw = dsh.generate_json(
                    prompt.system_prompt,
                    prompt.user_prompt,
                    context_report=replace(prompt.report, source_unit=chunk.chunk_id),
                    **options,
                )
                _check_fact_task(dsh, cancel_event)
                result = parse_chunk_facts(raw, chunk)
                _check_fact_task(dsh, cancel_event)
                ledger_cache.save(chunk, result, request_context=request_context)
            extracted += 1
            results.append(result)
    with memory_phase(progress_callback, "merge"):
        _check_fact_task(dsh, cancel_event)
        facts, unknowns = merge_chunk_facts(results)
    return ChapterFactLedger(
        chapter_id=chapter_id,
        chapter_hash=chapter_content_hash(content),
        chunks=tuple(results),
        facts=facts,
        unknowns=unknowns,
        cache_hits=cache_hits,
        extracted_chunks=extracted,
    )


def _extract_parallel_facts(dsh, chunks, title, cache, refresh, budget, estimator, cancel_event,
                            callback, request_context):
    results, jobs = {}, []
    hits = extracted = 0
    phase = ParallelMemoryPhase(callback, "facts", len(chunks))
    for index, chunk in enumerate(chunks):
        _check_fact_task(dsh, cancel_event)
        cached = None if refresh else cache.load(chunk, request_context=request_context)
        _check_fact_task(dsh, cancel_event)
        if cached is not None:
            results[index] = cached
            hits += 1
            phase.emit("cached", cache_hits=1, cache_reason="hit")
        else:
            prompt = build_chunk_facts_prompt(title, chunk, input_token_budget=budget, estimator=estimator)
            jobs.append((index, chunk, prompt))

    def work(worker, job):
        _index, chunk, prompt = job
        raw = worker.generate_json(prompt.system_prompt, prompt.user_prompt,
            context_report=replace(prompt.report, source_unit=chunk.chunk_id), cancel_event=cancel_event)
        return parse_chunk_facts(raw, chunk)

    def accept(job, result):
        nonlocal extracted
        index, chunk, _prompt = job
        _check_fact_task(dsh, cancel_event)
        cache.save(chunk, result, request_context=request_context)
        results[index] = result
        extracted += 1
        phase.emit("done", cache_reason="refresh" if refresh else "unmatched")

    if jobs:
        phase.emit("running")
        try:
            dsh.run_jobs(jobs, work, accept)
        except Exception:
            phase.emit("interrupted")
            raise
    return [results[index] for index in range(len(chunks))], hits, extracted


def _check_fact_task(dsh, cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise AITaskCancelled()
    check = getattr(dsh, "check_task_deadline", None)
    if callable(check):
        check()


def parse_chunk_facts(raw: str | dict[str, Any], chunk: ChapterChunk) -> ChunkFacts:
    if isinstance(raw, dict):
        value = dict(raw)
    else:
        try:
            extracted = extract_json(str(raw or ""))
        except JSONExtractionError as exc:
            raise FactProtocolError("分块事实返回内容不是合法 JSON。") from exc
        if not isinstance(extracted, dict):
            raise FactProtocolError("分块事实返回内容不是 JSON 对象。")
        value = dict(extracted)
    if value.get("type") != "chapter_chunk_facts":
        raise FactProtocolError("返回内容不是章节分块事实。")
    if value.get("schema_version") != FACT_LEDGER_SCHEMA_VERSION:
        raise FactProtocolError("分块事实协议版本不匹配。")
    for key, expected in (
        ("chapter_id", chunk.chapter_id),
        ("chunk_id", chunk.chunk_id),
        ("source_hash", chunk.content_hash),
    ):
        if str(value.get(key) or "") != expected:
            raise FactProtocolError(f"分块事实的 {key} 与当前任务不一致。")
    summary = _bounded_text(value.get("chunk_summary"), "chunk_summary", 2_000)
    raw_facts = value.get("facts")
    raw_unknowns = value.get("unknowns")
    if not isinstance(raw_facts, list) or not isinstance(raw_unknowns, list):
        raise FactProtocolError("分块事实缺少 facts 或 unknowns 数组。")
    if len(raw_facts) > 200 or len(raw_unknowns) > 50:
        raise FactProtocolError("分块事实返回条目异常过多。")

    facts: list[FactRecord] = []
    for item in raw_facts:
        if not isinstance(item, dict):
            raise FactProtocolError("facts 包含无效条目。")
        category = str(item.get("category") or "").strip().casefold()
        certainty = str(item.get("certainty") or "").strip().casefold()
        if category not in FACT_CATEGORIES:
            raise FactProtocolError(f"分块事实包含无效 category：{category}")
        if certainty not in FACT_CERTAINTIES:
            raise FactProtocolError(f"分块事实包含无效 certainty：{certainty}")
        subject = _bounded_text(item.get("subject"), "subject", 200)
        predicate = _bounded_text(item.get("predicate"), "predicate", 240)
        fact_value = _bounded_text(item.get("value"), "value", 2_000)
        anchor = _validate_anchor(item.get("anchor"), chunk)
        fact_id = _fact_id(
            category,
            subject,
            predicate,
            fact_value,
            anchor,
        )
        facts.append(
            FactRecord(
                fact_id,
                category,
                subject,
                predicate,
                fact_value,
                anchor,
                certainty,
            )
        )

    unknowns: list[UnknownRecord] = []
    for item in raw_unknowns:
        if not isinstance(item, dict):
            raise FactProtocolError("unknowns 包含无效条目。")
        unknowns.append(
            UnknownRecord(
                _bounded_text(item.get("description"), "description", 1_000),
                _validate_anchor(item.get("anchor"), chunk),
            )
        )
    return ChunkFacts(
        chapter_id=chunk.chapter_id,
        chunk_id=chunk.chunk_id,
        source_hash=chunk.content_hash,
        chunk_summary=summary,
        facts=tuple(_dedupe_facts(facts)),
        unknowns=tuple(_dedupe_unknowns(unknowns)),
        completion_message=_bounded_text(
            value.get("completion_message") or "分块事实提取完成",
            "completion_message",
            500,
        ),
    )


def merge_chunk_facts(
    results: list[ChunkFacts] | tuple[ChunkFacts, ...],
) -> tuple[tuple[FactRecord, ...], tuple[UnknownRecord, ...]]:
    facts = _dedupe_facts([fact for result in results for fact in result.facts])
    unknowns = _dedupe_unknowns(
        [unknown for result in results for unknown in result.unknowns]
    )
    return tuple(facts), tuple(unknowns)


def _bounded_text(value: object, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise FactProtocolError(f"分块事实缺少 {label}。")
    if len(text) > maximum:
        raise FactProtocolError(f"分块事实字段 {label} 超过安全长度。")
    return text


def _validate_anchor(value: object, chunk: ChapterChunk) -> str:
    anchor = str(value or "").strip()
    pattern = re.compile(
        rf"^{re.escape(chunk.chapter_id)}:p(\d{{4}})(?:-p(\d{{4}}))?$"
    )
    match = pattern.fullmatch(anchor)
    if match is None:
        raise FactProtocolError("分块事实包含无效来源锚点。")
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if start > end or start < chunk.start_paragraph or end > chunk.end_paragraph:
        raise FactProtocolError("分块事实来源锚点超出当前分块范围。")
    return anchor


def _fact_id(*parts: str) -> str:
    canonical = "|".join(str(part).strip().casefold() for part in parts)
    return "fact_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def _dedupe_facts(values: list[FactRecord]) -> list[FactRecord]:
    result: list[FactRecord] = []
    positions: dict[str, int] = {}
    certainty_rank = {"uncertain": 1, "inferred": 2, "explicit": 3}
    for value in values:
        position = positions.get(value.fact_id)
        if position is None:
            positions[value.fact_id] = len(result)
            result.append(value)
        elif certainty_rank[value.certainty] > certainty_rank[result[position].certainty]:
            # Preserve deterministic ordering but retain the strongest
            # evidence classification seen in overlapping chunks.
            result[position] = value
    return result


def _dedupe_unknowns(values: list[UnknownRecord]) -> list[UnknownRecord]:
    result: list[UnknownRecord] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        key = (value.description.casefold(), value.anchor)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
