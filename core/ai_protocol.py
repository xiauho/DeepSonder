"""Validation helpers for structured DSh task results.

The headless Harness returns assistant text on stdout.  These helpers turn
that text into task-specific results before the UI is allowed to write it to
the project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .json_utils import JSONExtractionError, extract_json


class AIProtocolError(ValueError):
    """Raised when a DSh response does not match the requested task."""


# These values are part of the machine-readable contract.  Keep them stable
# and translate them only when rendering the report for Chinese-speaking
# authors.
CONSISTENCY_SEVERITIES = frozenset({"high", "medium", "low"})
CONSISTENCY_CATEGORIES = frozenset(
    {
        "relationship",
        "character",
        "state",
        "location",
        "power",
        "item",
        "timeline",
        "world",
        "outline",
        "foreshadowing",
        "place",
        "event",
    }
)
CONSISTENCY_KINDS = frozenset(
    {
        "hard_conflict",
        "continuity_risk",
        "sync_gap",
        "outline_deviation",
        "missing_information",
        "suggestion",
    }
)
CONSISTENCY_REPAIR_TARGETS = frozenset(
    {"chapter", "character_card", "story_state", "outline", "canon", "manual"}
)
CONSISTENCY_REPAIRABILITIES = frozenset({"automatic", "choice_required", "manual"})
CONSISTENCY_REPAIR_STATUSES = frozenset(
    {"ready", "choice_required", "not_applicable", "insufficient_context"}
)

CONSISTENCY_SEVERITY_LABELS = {
    "high": "严重",
    "medium": "警告",
    "low": "提示",
}
CONSISTENCY_CATEGORY_LABELS = {
    "relationship": "人物关系",
    "character": "人物设定",
    "state": "人物状态",
    "location": "人物位置",
    "power": "能力与战力",
    "item": "道具与持有物",
    "timeline": "时间线",
    "world": "世界观规则",
    "outline": "大纲与正文",
    "foreshadowing": "伏笔",
    "place": "地点设定",
    "event": "事件衔接",
}
CONSISTENCY_KIND_LABELS = {
    "hard_conflict": "硬冲突",
    "continuity_risk": "连续性风险",
    "sync_gap": "资料未同步",
    "outline_deviation": "大纲偏差",
    "missing_information": "信息不足",
    "suggestion": "优化建议",
}

_CONSISTENCY_SEVERITY_ALIASES = {
    "critical": "high",
    "severe": "high",
    "warning": "medium",
    "info": "low",
    "notice": "low",
    "提示": "low",
    "警告": "medium",
    "严重": "high",
}
_CONSISTENCY_CATEGORY_ALIASES = {
    "relationships": "relationship",
    "relation": "relationship",
    "character_relationship": "relationship",
    "人物关系": "relationship",
    "人物设定": "character",
    "人物状态": "state",
    "人物位置": "location",
    "能力": "power",
    "能力与战力": "power",
    "道具": "item",
    "道具与持有物": "item",
    "时间线": "timeline",
    "世界观": "world",
    "世界观规则": "world",
    "大纲": "outline",
    "大纲与正文": "outline",
    "伏笔": "foreshadowing",
    "地点": "place",
    "事件": "event",
}
_CONSISTENCY_KIND_ALIASES = {
    "conflict": "hard_conflict",
    "hard-conflict": "hard_conflict",
    "continuity": "continuity_risk",
    "sync": "sync_gap",
    "sync-gap": "sync_gap",
    "outline": "outline_deviation",
    "deviation": "outline_deviation",
    "missing": "missing_information",
    "info": "missing_information",
    "硬冲突": "hard_conflict",
    "连续性风险": "continuity_risk",
    "资料未同步": "sync_gap",
    "大纲偏差": "outline_deviation",
    "信息不足": "missing_information",
    "优化建议": "suggestion",
}
_CONSISTENCY_TARGET_ALIASES = {
    # Some models use ``data`` as a generic label for project-side material.
    # It is too ambiguous to route to a character card, story state, outline,
    # or canon automatically, so preserve the issue as a manual action.
    "data": "manual",
    "正文": "chapter",
    "章节正文": "chapter",
    "角色卡": "character_card",
    "人物设定": "character_card",
    "故事状态": "story_state",
    "大纲": "outline",
    "设定": "canon",
    "人工": "manual",
}
_CONSISTENCY_REPAIRABILITY_ALIASES = {
    "auto": "automatic",
    "自动": "automatic",
    "需确认": "choice_required",
    "选择": "choice_required",
    "人工": "manual",
}


@dataclass(frozen=True)
class ContinuationResult:
    text: str
    char_count: int
    length_ok: bool
    used_marker: bool = False
    completion_message: str = "续写任务已完成"
    protocol_warning: str = ""
    plain_text_fallback: bool = False


@dataclass(frozen=True)
class ForeshadowingSuggestion:
    foreshadowing_id: str
    evidence: str
    reason: str


@dataclass(frozen=True)
class ExpansionResult:
    text: str
    char_count: int
    length_ok: bool
    used_marker: bool = False
    completion_message: str = "扩写任务已完成"
    foreshadowing_feedback: tuple[ForeshadowingSuggestion, ...] = ()
    feedback_warning: str = ""
    protocol_warning: str = ""
    plain_text_fallback: bool = False


@dataclass(frozen=True)
class ConsistencyRepairResult:
    chapter_id: str
    issue_id: str
    status: str
    target: str
    expected_original: str
    replacement: str
    explanation: str
    preserved_facts: tuple[str, ...] = ()
    completion_message: str = "一致性修复方案已生成"


def parse_continuation(
    raw: str,
    *,
    min_chars: int = 0,
    max_chars: int | None = None,
) -> ContinuationResult:
    """Extract a continuation only from the explicit continuation contract."""
    return _parse_novel_text(
        raw,
        min_chars=min_chars,
        max_chars=max_chars,
        expected_types={"continuation"},
        default_completion="续写任务已完成",
        task_label="续写",
    )


def parse_expansion(
    raw: str,
    *,
    min_chars: int = 0,
    max_chars: int | None = None,
    expected_chapter_id: str | None = None,
    allowed_foreshadowing_ids: set[str] | None = None,
) -> ExpansionResult:
    """Extract a complete current-chapter draft from the expansion contract."""
    base = _parse_novel_text(
        raw,
        min_chars=min_chars,
        max_chars=max_chars,
        expected_types={"chapter_expansion"},
        default_completion="扩写任务已完成",
        task_label="扩写",
    )
    suggestions, warning = _parse_foreshadowing_feedback(
        str(raw or ""),
        expected_chapter_id=expected_chapter_id,
        allowed_foreshadowing_ids=allowed_foreshadowing_ids,
    )
    return ExpansionResult(
        text=base.text,
        char_count=base.char_count,
        length_ok=base.length_ok,
        used_marker=base.used_marker,
        completion_message=base.completion_message,
        foreshadowing_feedback=suggestions,
        feedback_warning=warning,
        protocol_warning=base.protocol_warning,
        plain_text_fallback=base.plain_text_fallback,
    )


def _parse_novel_text(
    raw: str,
    *,
    min_chars: int,
    max_chars: int | None,
    expected_types: set[str],
    default_completion: str,
    task_label: str,
) -> ContinuationResult:
    text = str(raw or "").strip()
    content = ""
    used_marker = False
    protocol_warning = ""
    plain_text_fallback = False
    completion_message = default_completion

    done_match = re.search(
        r"<NOVALIST_TASK_DONE>\s*([\s\S]*?)\s*</NOVALIST_TASK_DONE>",
        text,
        re.IGNORECASE,
    )
    if done_match and done_match.group(1).strip():
        completion_message = done_match.group(1).strip()

    match = re.search(r"<NOVEL_TEXT>\s*([\s\S]*?)\s*</NOVEL_TEXT>", text, re.IGNORECASE)
    if match:
        content = match.group(1).strip()
        used_marker = True
    else:
        repaired = _extract_repairable_novel_text(text)
        if repaired:
            content = repaired
            used_marker = True
            protocol_warning = "AI 返回的正文标记不完整，系统已安全清理后再用于预览。"
        try:
            value = _extract_json(text) if not content else None
        except AIProtocolError:
            value = None
        if isinstance(value, dict):
            if value.get("type") in expected_types:
                content = str(value.get("content") or "").strip()
                completion_message = str(
                    value.get("completion_message") or completion_message
                ).strip()
        elif not content and not _contains_protocol_artifact(text) and _looks_like_narrative(text):
            # Some valid headless runners return the final answer as plain
            # prose even when the task asks for a marker. Keep this safe
            # fallback, but reject obvious Agent onboarding responses below.
            content = text
            plain_text_fallback = True
            protocol_warning = "AI 未按正文协议返回标记，系统已使用纯正文兼容回退。"

    if not content:
        raise AIProtocolError(f"DSh 返回内容不符合{task_label}协议，未识别出小说正文。")

    char_count = _content_length(content)
    length_ok = char_count >= max(0, int(min_chars)) and (
        max_chars is None or char_count <= int(max_chars)
    )
    return ContinuationResult(
        text=content,
        char_count=char_count,
        length_ok=length_ok,
        used_marker=used_marker,
        completion_message=completion_message,
        protocol_warning=protocol_warning,
        plain_text_fallback=plain_text_fallback,
    )


def parse_consistency_report(
    raw: str | dict[str, Any],
    *,
    expected_chapter_id: str | None = None,
) -> dict[str, Any]:
    value = dict(_as_object(raw, "一致性检查"))
    if value.get("type") != "consistency_report":
        raise AIProtocolError("DSh 返回内容不是一致性检查报告。")
    if expected_chapter_id is not None:
        received_chapter_id = str(value.get("chapter_id") or "").strip()
        if received_chapter_id and received_chapter_id != str(expected_chapter_id):
            raise AIProtocolError(
                "一致性检查报告的章节与当前章节不一致。"
            )
        value["chapter_id"] = str(expected_chapter_id)
    elif value.get("chapter_id") is not None:
        value["chapter_id"] = str(value.get("chapter_id") or "").strip()
    value["completion_message"] = str(
        value.get("completion_message") or "一致性检查任务已完成"
    ).strip()
    status = str(value.get("status") or "").strip().casefold()
    if not status:
        status = "warning" if value.get("issues") else "ok"
    if status not in {"ok", "warning", "error"}:
        raise AIProtocolError(f"一致性检查报告包含无效 status：{status}")
    value["status"] = status
    issues = value.get("issues")
    if not isinstance(issues, list):
        raise AIProtocolError("一致性检查报告缺少 issues 数组。")
    normalized_issues = []
    seen_issue_ids: set[str] = set()
    for issue_index, issue in enumerate(issues, 1):
        if not isinstance(issue, dict):
            raise AIProtocolError("一致性检查报告包含无效问题项。")
        normalized = dict(issue)
        normalized["issue_id"] = str(
            normalized.get("issue_id") or f"issue_{issue_index}"
        ).strip()
        if not normalized["issue_id"]:
            raise AIProtocolError("一致性检查问题缺少 issue_id。")
        if normalized["issue_id"] in seen_issue_ids:
            raise AIProtocolError("一致性检查报告包含重复 issue_id。")
        seen_issue_ids.add(normalized["issue_id"])
        for key in ("description", "evidence"):
            if not str(normalized.get(key) or "").strip():
                raise AIProtocolError(f"一致性检查问题缺少字段：{key}")
            normalized[key] = str(normalized[key]).strip()
        normalized["severity"] = _normalize_consistency_enum(
            normalized.get("severity"),
            _CONSISTENCY_SEVERITY_ALIASES,
            CONSISTENCY_SEVERITIES,
            "severity",
        )
        normalized["category"] = _normalize_consistency_enum(
            normalized.get("category"),
            _CONSISTENCY_CATEGORY_ALIASES,
            CONSISTENCY_CATEGORIES,
            "category",
        )
        # ``kind`` was added after the first protocol version.  Keep old
        # reports readable while ensuring all new reports have the field.
        normalized["kind"] = _normalize_consistency_enum(
            normalized.get("kind") or "continuity_risk",
            _CONSISTENCY_KIND_ALIASES,
            CONSISTENCY_KINDS,
            "kind",
        )
        for key in ("location_hint", "suggestion", "source_hint"):
            if key in normalized and normalized[key] is not None:
                normalized[key] = str(normalized[key]).strip()
        if "chapter_quote" in normalized and normalized["chapter_quote"] is not None:
            normalized["chapter_quote"] = str(normalized["chapter_quote"]).strip()
        default_target = (
            "chapter"
            if normalized["kind"] in {"hard_conflict", "continuity_risk"}
            else "manual"
        )
        normalized["recommended_target"] = _normalize_consistency_enum(
            normalized.get("recommended_target") or default_target,
            _CONSISTENCY_TARGET_ALIASES,
            CONSISTENCY_REPAIR_TARGETS,
            "recommended_target",
        )
        default_repairability = (
            "automatic"
            if normalized["kind"] in {"hard_conflict", "continuity_risk"}
            else "manual"
        )
        normalized["repairability"] = _normalize_consistency_enum(
            normalized.get("repairability") or default_repairability,
            _CONSISTENCY_REPAIRABILITY_ALIASES,
            CONSISTENCY_REPAIRABILITIES,
            "repairability",
        )
        if normalized["repairability"] == "automatic" and (
            normalized["recommended_target"] != "chapter"
            or normalized["kind"] not in {"hard_conflict", "continuity_risk"}
        ):
            # Automatic repair is intentionally limited to a safely anchored
            # chapter edit.  Project-side data and advisory issue kinds always
            # require author review, even if a model labels them automatic.
            normalized["repairability"] = "manual"
        normalized_issues.append(normalized)
    value["issues"] = normalized_issues
    # ``ok`` means no detected issue.  Normalize older model responses that
    # left the status at its example value while still returning issues.
    if value["status"] == "ok" and normalized_issues:
        value["status"] = "warning"
    return value


def parse_consistency_repair(
    raw: str | dict[str, Any],
    *,
    expected_chapter_id: str | None = None,
    expected_issue_id: str | None = None,
    expected_original: str | None = None,
) -> ConsistencyRepairResult:
    """Validate a minimal, one-range consistency repair proposal."""
    value = _as_object(raw, "一致性修复")
    if value.get("type") != "consistency_repair":
        raise AIProtocolError("DSh 返回内容不是一致性修复方案。")
    chapter_id = str(value.get("chapter_id") or "").strip()
    issue_id = str(value.get("issue_id") or "").strip()
    if not chapter_id or not issue_id:
        raise AIProtocolError("一致性修复方案缺少 chapter_id 或 issue_id。")
    if expected_chapter_id is not None and chapter_id != str(expected_chapter_id):
        raise AIProtocolError("一致性修复方案的章节与当前章节不一致。")
    if expected_issue_id is not None and issue_id != str(expected_issue_id):
        raise AIProtocolError("一致性修复方案对应的问题已变化。")
    status = str(value.get("status") or "").strip().casefold()
    if status not in CONSISTENCY_REPAIR_STATUSES:
        raise AIProtocolError(f"一致性修复方案包含无效 status：{status}")
    target = _normalize_consistency_enum(
        value.get("target") or "chapter",
        _CONSISTENCY_TARGET_ALIASES,
        CONSISTENCY_REPAIR_TARGETS,
        "target",
    )
    original = str(value.get("expected_original") or "")
    replacement = str(value.get("replacement") or "")
    explanation = str(value.get("explanation") or "").strip()
    if not explanation:
        raise AIProtocolError("一致性修复方案缺少 explanation。")
    if expected_original is not None and original != str(expected_original):
        raise AIProtocolError("一致性修复方案的原文锚点已变化。")
    if status == "ready":
        if target != "chapter":
            raise AIProtocolError("当前版本只允许对章节正文生成自动修复。")
        if not original.strip() or not replacement.strip():
            raise AIProtocolError("可执行修复必须同时提供 expected_original 和 replacement。")
        if original == replacement:
            raise AIProtocolError("修复前后文本不能完全相同。")
        if _contains_protocol_artifact(replacement):
            raise AIProtocolError("replacement 包含协议标记，已拒绝写回。")
        if len(replacement) > max(10_000, len(original) * 20):
            raise AIProtocolError("replacement 范围异常扩大，已拒绝写回。")
    else:
        replacement = ""
    preserved = value.get("preserved_facts") or []
    if not isinstance(preserved, list) or any(not str(item).strip() for item in preserved):
        raise AIProtocolError("一致性修复方案的 preserved_facts 必须是字符串数组。")
    return ConsistencyRepairResult(
        chapter_id=chapter_id,
        issue_id=issue_id,
        status=status,
        target=target,
        expected_original=original,
        replacement=replacement,
        explanation=explanation,
        preserved_facts=tuple(str(item).strip() for item in preserved),
        completion_message=str(
            value.get("completion_message") or "一致性修复方案已生成"
        ).strip(),
    )


def format_consistency_report(report: dict[str, Any]) -> str:
    """Render a validated report for the existing text-based report views."""
    issues = report.get("issues", [])
    if not issues:
        return "✅ 未发现明显设定冲突。"

    lines = ["⚠️ 发现以下问题："]
    for index, issue in enumerate(issues, 1):
        severity = CONSISTENCY_SEVERITY_LABELS.get(
            str(issue.get("severity") or "").casefold(), "未分级"
        )
        category = CONSISTENCY_CATEGORY_LABELS.get(
            str(issue.get("category") or "").casefold(), "其他问题"
        )
        kind = CONSISTENCY_KIND_LABELS.get(
            str(issue.get("kind") or "").casefold(), "连续性风险"
        )
        lines.append(
            f"{index}. [{severity}] {category}（{kind}）：{issue['description']}"
        )
        lines.append(f"   证据：{issue['evidence']}")
        if issue.get("location_hint"):
            lines.append(f"   位置：{issue['location_hint']}")
        if issue.get("chapter_quote"):
            lines.append(f"   正文原句：{issue['chapter_quote']}")
        if issue.get("source_hint"):
            lines.append(f"   来源：{issue['source_hint']}")
        if issue.get("suggestion"):
            lines.append(f"   建议：{issue['suggestion']}")
    return "\n".join(lines)


def consistency_issue_counts(report: dict[str, Any]) -> dict[str, int]:
    """Count validated issues from structured data for UI summaries."""
    counts = {severity: 0 for severity in CONSISTENCY_SEVERITIES}
    for issue in report.get("issues", []):
        severity = str(issue.get("severity") or "").casefold()
        if severity in counts:
            counts[severity] += 1
    return counts


def _normalize_consistency_enum(
    value: object,
    aliases: dict[str, str],
    allowed: frozenset[str],
    field: str,
) -> str:
    raw = str(value or "").strip().casefold()
    normalized = aliases.get(raw, raw)
    if normalized not in allowed:
        raise AIProtocolError(f"一致性检查问题包含无效 {field}：{value}")
    return normalized


def _as_object(raw: str | dict[str, Any], label: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        value = raw
    else:
        value = _extract_json(str(raw or ""))
    if not isinstance(value, dict):
        raise AIProtocolError(f"DSh 返回内容不是有效的{label}对象。")
    return value


def _extract_json(text: str) -> Any:
    try:
        return extract_json(text)
    except JSONExtractionError as exc:
        raise AIProtocolError("DSh 返回内容不是合法 JSON。") from exc


def _parse_foreshadowing_feedback(
    raw: str,
    *,
    expected_chapter_id: str | None,
    allowed_foreshadowing_ids: set[str] | None,
) -> tuple[tuple[ForeshadowingSuggestion, ...], str]:
    """Parse optional advisory metadata without invalidating valid prose."""
    match = re.search(
        r"<FORESHADOWING_FEEDBACK>\s*([\s\S]*?)\s*</FORESHADOWING_FEEDBACK>",
        raw,
        re.IGNORECASE,
    )
    if match is None:
        warning = (
            "本次选择了伏笔，但 AI 未返回可用的伏笔复核结果；正文仍可正常使用。"
            if allowed_foreshadowing_ids
            else ""
        )
        return (), warning
    try:
        value = _extract_json(match.group(1))
    except AIProtocolError:
        return (), "伏笔反馈不是合法 JSON，已忽略；正文仍可正常使用。"
    if not isinstance(value, dict):
        return (), "伏笔反馈不是有效对象，已忽略；正文仍可正常使用。"

    chapter_id = str(value.get("chapter_id") or "").strip()
    if expected_chapter_id is not None and chapter_id != str(expected_chapter_id):
        return (), "伏笔反馈的章节与当前章节不一致，已忽略。"
    items = value.get("possibly_resolved")
    if not isinstance(items, list):
        return (), "伏笔反馈缺少 possibly_resolved 数组，已忽略。"

    suggestions: list[ForeshadowingSuggestion] = []
    seen: set[str] = set()
    ignored = False
    for item in items:
        if not isinstance(item, dict):
            ignored = True
            continue
        note_id = str(item.get("foreshadowing_id") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if (
            not note_id
            or note_id in seen
            or not evidence
            or not reason
            or (
                allowed_foreshadowing_ids is not None
                and note_id not in allowed_foreshadowing_ids
            )
        ):
            ignored = True
            continue
        seen.add(note_id)
        suggestions.append(ForeshadowingSuggestion(note_id, evidence, reason))
    warning = "伏笔反馈中有无效或非本次选择的条目，已安全忽略。" if ignored else ""
    return tuple(suggestions), warning


def _content_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _extract_repairable_novel_text(text: str) -> str:
    """Salvage prose after an opening marker without leaking protocol tags."""
    opening = re.search(r"<NOVEL_TEXT\s*>", text, re.IGNORECASE)
    if opening is None:
        return ""
    tail = text[opening.end() :]
    boundaries = []
    for pattern in (
        r"</NOVEL_TEXT\s*>",
        r"</?NOVALIST_TASK_DONE\s*>",
        r"<FORESHADOWING_FEEDBACK\s*>",
    ):
        boundary = re.search(pattern, tail, re.IGNORECASE)
        if boundary is not None:
            boundaries.append(boundary.start())
    candidate = tail[: min(boundaries)] if boundaries else tail
    candidate = candidate.strip()
    if not candidate or _contains_protocol_artifact(candidate):
        return ""
    return candidate if _looks_like_narrative(candidate) else ""


def _contains_protocol_artifact(text: str) -> bool:
    return bool(
        re.search(
            r"</?(?:NOVEL_TEXT|NOVALIST_TASK_DONE|FORESHADOWING_FEEDBACK)\b",
            str(text or ""),
            re.IGNORECASE,
        )
    )


def _looks_like_narrative(text: str) -> bool:
    """Accept plain prose while rejecting the known workspace onboarding reply."""
    if not text or not re.search(r"[\u4e00-\u9fff]", text):
        return False
    onboarding_patterns = (
        "我已就绪",
        "当前会话环境",
        "项目现有结构",
        "工作目录",
        "请问这次需要我做什么",
        "直接告诉我任务",
    )
    if any(pattern in text for pattern in onboarding_patterns):
        return False
    if "##" in text and "正文" not in text[:80]:
        return False
    return bool(re.search(r"[。！？；：、“”‘’]", text))
