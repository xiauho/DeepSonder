"""Local chapter-summary selection; no model calls or project writes."""

from dataclasses import dataclass
from dataclasses import replace
import math
import re

from .project import chapter_number_from_id
from .token_budget import DEFAULT_TOKEN_ESTIMATOR


HISTORY_CHAPTERS_MAX = 200
AUTO_HISTORY_CHAPTERS = {"compatible": 3, "balanced": 5, "deep": 8}
HISTORY_BUDGET_PERCENT = {"compatible": 15, "balanced": 20, "deep": 25}


def resolve_history_count(mode: str, count: int, strategy: str) -> int:
    if mode == "auto":
        return AUTO_HISTORY_CHAPTERS.get(strategy, 5)
    return max(0, min(HISTORY_CHAPTERS_MAX, int(count)))


def history_token_budget(input_budget: int, strategy: str) -> int:
    return max(0, int(input_budget)) * HISTORY_BUDGET_PERCENT.get(strategy, 20) // 100


@dataclass(frozen=True)
class HistoryEntry:
    chapter_id: str
    summary: str
    kind: str = "recent"
    status: str = "unverified"
    source_hash: str = ""
    record_hash: str = ""
    fact_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    @property
    def rendered(self) -> str:
        source = (
            f"\n来源：已采用记忆；正文版本 {self.source_hash}；记忆版本 {self.record_hash}"
            + (f"；事实引用 {', '.join(self.fact_ids)}" if self.fact_ids else "")
            if self.status == "verified" else "\n来源：旧摘要，正文版本未确认"
        )
        return f"### {self.chapter_id}{source}\n{self.summary}"


@dataclass(frozen=True)
class HistoryWindow:
    entries: tuple[HistoryEntry, ...]
    requested: int
    in_range: int
    missing: int
    remote: tuple[HistoryEntry, ...] = ()
    stale: int = 0
    unverified: int = 0
    remote_candidates: int = 0
    remote_matched: int = 0
    provenance_error: bool = False


@dataclass(frozen=True)
class HistoryAllocation:
    text: str
    included: int
    excluded_budget: int
    estimated_tokens: int
    token_budget: int
    remote_included: int = 0
    sources: tuple[dict, ...] = ()


def select_history_window(
    summaries: dict, chapter_ids: list[str], current_id: str, count: int,
    *, view=None, query: str = "", remote_enabled: bool = False,
) -> HistoryWindow:
    """Count recent chapter positions, including chapters missing a summary.

    Legacy projects may have summaries without chapter files. Their keys still
    participate in ordering, preserving imported summaries.
    """
    count = max(0, min(HISTORY_CHAPTERS_MAX, int(count)))
    if not count:
        return HistoryWindow((), 0, 0, 0)
    current = chapter_number_from_id(current_id)
    keys = set(chapter_ids) | set(summaries)
    ordered = []
    for key in keys:
        if key == current_id:
            continue
        number = chapter_number_from_id(key)
        if current is not None and number is not None and number >= current:
            continue
        # Without a comparable position, avoid guessing which numbered
        # chapters precede an unnumbered current chapter.
        if current is None:
            continue
        ordered.append((number is not None, number or 0, key))
    ordered.sort()
    recent = ordered[-count:]
    entries = tuple(
        HistoryEntry(key, summaries[key].strip())
        for _, _, key in recent
        if isinstance(summaries.get(key), str) and summaries[key].strip()
    )
    missing = len(recent) - len(entries)
    stale = unverified = 0
    if view is not None:
        checked = []
        for entry in entries:
            status = view.status(entry.chapter_id)
            if status in {"stale", "missing_source", "invalid"}:
                stale += 1
                continue
            if status == "unverified":
                unverified += 1
                # Missing/corrupt provenance must not silently become trusted.
                if view.error:
                    continue
                checked.append(entry)
            else:
                record = view.records[entry.chapter_id]
                checked.append(replace(entry, status=status, source_hash=record["source_hash"], record_hash=record["record_hash"]))
        entries = tuple(checked)
    remote, candidates, matched, remote_stale = (), 0, 0, 0
    if remote_enabled and view is not None and current is not None:
        remote, candidates, matched, remote_stale = retrieve_remote(
            view, current, {key for _, _, key in recent}, query,
        )
    return HistoryWindow(
        entries, count, len(recent), missing, remote, stale + remote_stale,
        unverified, candidates, matched, bool(view and view.error),
    )


def allocate_history(
    window: HistoryWindow, char_budget: int, token_budget: int,
) -> HistoryAllocation:
    """Keep complete entries, preferring recent chapters; never slice prose."""
    char_budget, token_budget = max(0, char_budget), max(0, token_budget)
    chosen: list[HistoryEntry] = []
    for entry in [*reversed(window.entries), *window.remote]:
        candidate = "\n\n".join(item.rendered for item in [*chosen, entry])
        if (
            len(candidate) <= char_budget
            and DEFAULT_TOKEN_ESTIMATOR.estimate(candidate) <= token_budget
        ):
            chosen.append(entry)
    chosen.sort(key=lambda item: (chapter_number_from_id(item.chapter_id) or 0, item.chapter_id))
    text = "\n\n".join(item.rendered for item in chosen)
    recent_count = sum(item.kind == "recent" for item in chosen)
    return HistoryAllocation(
        text, recent_count, len(window.entries) + len(window.remote) - len(chosen),
        DEFAULT_TOKEN_ESTIMATOR.estimate(text), token_budget,
        len(chosen) - recent_count,
        tuple({
            "chapter_id": item.chapter_id, "kind": item.kind, "status": item.status,
            "source_hash": item.source_hash, "record_hash": item.record_hash,
            "fact_ids": item.fact_ids, "reasons": item.reasons,
        } for item in chosen),
    )


def _terms(query: str) -> set[str]:
    terms = set(re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}|[\u3400-\u9fff]{2,}", query.casefold()))
    for run in list(terms):
        if re.fullmatch(r"[\u3400-\u9fff]+", run):
            for size in (2, 3, 4):
                terms.update(run[i:i + size] for i in range(len(run) - size + 1))
    return terms - {"主角", "本章", "章节", "故事", "然后", "他们", "这里", "一个", "没有", "这个", "可以", "已经", "任务", "摘要", "人物"}


def retrieve_remote(view, current: int, excluded: set[str], query: str):
    """Search all earlier adopted digests; score locally and validate sources on hits."""
    terms = _terms(query)
    if not terms:
        return (), 0, 0, 0
    documents = []
    for key, record in sorted(view.records.items()):
        number = chapter_number_from_id(key)
        if number is None or number >= current or key in excluded or not isinstance(record, dict):
            continue
        digest = record.get("digest")
        if not isinstance(digest, dict):
            continue
        pieces = []
        for field in ("key_events", "character_changes", "location_changes", "item_changes", "relationship_changes", "timeline_changes"):
            for item in digest.get(field, []) if isinstance(digest.get(field), list) else []:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    ids = item.get("fact_ids", [])
                    pieces.append((item["text"], tuple(i for i in ids if isinstance(i, str)) if isinstance(ids, list) else ()))
        if not pieces and isinstance(digest.get("summary"), str):
            pieces = [(digest["summary"], ())]
        text = "\n".join(piece[0] for piece in pieces).casefold()
        hits = {term for term in terms if term in text}
        documents.append((key, record, pieces, hits))
    # Explicit character/place names and aliases carry more weight than n-grams.
    entities = set()
    for path in [*view.project.list_characters(), *view.project.list_world()]:
        if len(path.stem) >= 2 and path.stem.casefold() in query.casefold():
            entities.add(path.stem.casefold())
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for line in body.splitlines():
            match = re.match(r"\s*(?:[-*]\s*)?(?:别名|关键词|标签)\s*[：:]\s*(.+)", line)
            if match:
                aliases = re.split(r"[,，、;；|\s]+", match.group(1).casefold())
                if any(len(alias) >= 2 and alias in query.casefold() for alias in aliases):
                    entities.update([path.stem.casefold(), *(a for a in aliases if len(a) >= 2)])
    frequencies = {term: sum(term in d[3] for d in documents) for term in terms}
    ranked = []
    for key, record, pieces, hits in documents:
        full = "\n".join(p[0] for p in pieces).casefold()
        entity_hits = {entity for entity in entities if entity in full}
        useful = {t for t in hits if frequencies[t] <= max(2, len(documents) // 4)}
        if not entity_hits and not (any(len(t) >= 3 for t in useful) or len(useful) >= 2):
            continue
        score = 20 * len(entity_hits) + sum(math.log(1 + len(documents) / frequencies[t]) for t in useful)
        selected = [(text, ids) for text, ids in pieces if any(t in text.casefold() for t in useful | entity_hits)]
        selected = list(dict.fromkeys(selected))[:6]
        excerpt = "\n".join(f"- {text}" for text, _ in selected)
        if not excerpt:
            continue
        ranked.append((score, key, record, excerpt, tuple(sorted({i for _, ids in selected for i in ids})),
                       ("entity_match",) if entity_hits else ("keyword_match",)))
    ranked.sort(key=lambda item: (-item[0], -(chapter_number_from_id(item[1]) or 0), item[1]))
    selected_entries, stale = [], 0
    for _, key, record, excerpt, ids, reasons in ranked:
        if view.status(key) != "verified":
            stale += 1
            continue
        selected_entries.append(HistoryEntry(key, excerpt, "remote", "verified", record["source_hash"], record["record_hash"], ids, reasons))
        if len(selected_entries) >= 20:
            break
    return tuple(selected_entries), len(documents), len(ranked), stale
