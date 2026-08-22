"""Validation helpers for structured DSh task results.

The headless Harness returns assistant text on stdout.  These helpers turn
that text into task-specific results before the UI is allowed to write it to
the project.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


class AIProtocolError(ValueError):
    """Raised when a DSh response does not match the requested task."""


@dataclass(frozen=True)
class ContinuationResult:
    text: str
    char_count: int
    length_ok: bool
    used_marker: bool = False
    completion_message: str = "续写任务已完成"


@dataclass(frozen=True)
class SummaryResult:
    text: str
    completion_message: str


@dataclass(frozen=True)
class StoryStateResult:
    state: dict[str, Any]
    completion_message: str


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
) -> ContinuationResult:
    """Extract a complete current-chapter draft from the expansion contract."""
    return _parse_novel_text(
        raw,
        min_chars=min_chars,
        max_chars=max_chars,
        expected_types={"chapter_expansion"},
        default_completion="扩写任务已完成",
        task_label="扩写",
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
        try:
            value = _extract_json(text)
        except AIProtocolError:
            value = None
        if isinstance(value, dict):
            if value.get("type") in expected_types:
                content = str(value.get("content") or "").strip()
                completion_message = str(
                    value.get("completion_message") or completion_message
                ).strip()
        elif _looks_like_narrative(text):
            # Some valid headless runners return the final answer as plain
            # prose even when the task asks for a marker. Keep this safe
            # fallback, but reject obvious Agent onboarding responses below.
            content = text

    if not content:
        raise AIProtocolError(f"DSh 返回内容不符合{task_label}协议，未识别出小说正文。")

    char_count = _content_length(content)
    length_ok = char_count >= max(0, int(min_chars)) and (
        max_chars is None or char_count <= int(max_chars)
    )
    return ContinuationResult(
        content,
        char_count,
        length_ok,
        used_marker,
        completion_message,
    )


def parse_consistency_report(raw: str | dict[str, Any]) -> dict[str, Any]:
    value = _as_object(raw, "一致性检查")
    if value.get("type") != "consistency_report":
        raise AIProtocolError("DSh 返回内容不是一致性检查报告。")
    value["completion_message"] = str(
        value.get("completion_message") or "一致性检查任务已完成"
    ).strip()
    issues = value.get("issues")
    if not isinstance(issues, list):
        raise AIProtocolError("一致性检查报告缺少 issues 数组。")
    for issue in issues:
        if not isinstance(issue, dict):
            raise AIProtocolError("一致性检查报告包含无效问题项。")
        for key in ("severity", "category", "description", "evidence"):
            if not str(issue.get(key) or "").strip():
                raise AIProtocolError(f"一致性检查问题缺少字段：{key}")
    return value


def parse_summary_result(raw: str | dict[str, Any]) -> SummaryResult:
    value = _as_object(raw, "章节摘要")
    if value.get("type") != "chapter_summary":
        raise AIProtocolError("DSh 返回内容不是章节摘要。")
    summary = str(value.get("summary") or "").strip()
    if not summary:
        raise AIProtocolError("章节摘要为空。")
    return SummaryResult(
        summary,
        str(value.get("completion_message") or "章节摘要任务已完成").strip(),
    )


def parse_summary(raw: str | dict[str, Any]) -> str:
    return parse_summary_result(raw).text


def parse_story_state_result(raw: str | dict[str, Any]) -> StoryStateResult:
    value = _as_object(raw, "故事状态")
    if value.get("type") != "story_state_update":
        raise AIProtocolError("DSh 返回内容不是故事状态更新。")
    if not isinstance(value.get("characters"), dict):
        raise AIProtocolError("故事状态缺少 characters 对象。")
    if not isinstance(value.get("foreshadowing"), list):
        raise AIProtocolError("故事状态缺少 foreshadowing 数组。")
    if not isinstance(value.get("current_chapter"), (int, float)):
        raise AIProtocolError("故事状态缺少有效的 current_chapter。")
    result = dict(value)
    result.pop("type", None)
    completion_message = str(
        result.pop("completion_message", None) or "故事状态更新任务已完成"
    ).strip()
    result["current_chapter"] = int(result["current_chapter"])
    return StoryStateResult(result, completion_message)


def parse_story_state(raw: str | dict[str, Any]) -> dict[str, Any]:
    return parse_story_state_result(raw).state


def format_consistency_report(report: dict[str, Any]) -> str:
    """Render a validated report for the existing text-based report views."""
    issues = report.get("issues", [])
    if not issues:
        return "✅ 未发现明显设定冲突。"

    lines = ["⚠️ 发现以下问题："]
    for index, issue in enumerate(issues, 1):
        lines.append(
            f"{index}. [{issue['severity']}] {issue['category']}：{issue['description']}"
        )
        lines.append(f"   证据：{issue['evidence']}")
        if issue.get("location_hint"):
            lines.append(f"   位置：{issue['location_hint']}")
        if issue.get("suggestion"):
            lines.append(f"   建议：{issue['suggestion']}")
    return "\n".join(lines)


def _as_object(raw: str | dict[str, Any], label: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        value = raw
    else:
        value = _extract_json(str(raw or ""))
    if not isinstance(value, dict):
        raise AIProtocolError(f"DSh 返回内容不是有效的{label}对象。")
    return value


def _extract_json(text: str) -> Any:
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character not in "[{":
                continue
            try:
                value, _end = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            return value
    raise AIProtocolError("DSh 返回内容不是合法 JSON。")


def _content_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


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
