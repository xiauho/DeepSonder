"""Prompt builders for the task-oriented dsh writing features."""

from __future__ import annotations

import json

from .context_budget import (
    AIContext,
    CONTINUATION_SUMMARY_COUNT,
    DEFAULT_PROMPT_BUDGET,
    EXPANSION_SUMMARY_COUNT,
    allocate,
    build_ai_context,
    build_task_context,
    gather_sections,
    render_selected_foreshadowing,
)
from .project import NovelProject, chapter_number_from_id
from .text_anchor import render_anchor_context

COMMON_RULES = """
你是 Novalist 的小说创作 AI。

当前只执行指定的任务类型，不要执行其他任务。

通用规则：
1. 不要介绍工作目录、项目结构或当前会话。
2. 不要询问用户还需要做什么。
3. 不要输出“我已准备好”“以下是结果”等开场白。
4. 不要修改、创建或删除任何文件。
5. 只能使用下方提供的故事资料。
6. 不得擅自改变角色身份、世界观、时间线和能力规则。
7. 必须严格遵守当前任务的输出格式。
8. 任务完成后必须返回完成标记，不要把完成说明写进小说正文。
""".strip()


def build_expansion_prompt(
    project: NovelProject,
    chapter_id: str,
    target_chars: int = 2000,
    *,
    summary_count: int = EXPANSION_SUMMARY_COUNT,
    selected_foreshadowing: list[dict] | tuple[dict, ...] | None = None,
    selected_power: list[str] | tuple[str, ...] | None = None,
    context: AIContext | None = None,
    prompt_budget: int = DEFAULT_PROMPT_BUDGET,
) -> tuple[str, str]:
    """Build a compact outline-to-chapter expansion task.

    Unlike continuation, this task deliberately excludes the current chapter
    body.  The user's outline is the source of truth for the new draft.
    """
    summary_count = max(0, int(summary_count))
    context = context or build_task_context(
        project,
        chapter_id,
        character_scope="planning",
        include_world=True,
        include_power=True,
        include_timeline=True,
        selected_power=selected_power,
    )
    chapter = context.chapter
    sections = gather_sections(
        project,
        chapter_id,
        (
            "style",
            "outline",
            "plot_brief",
            "selected_foreshadowing",
            "core_power",
            "core_systems",
            "selected_power",
            "state",
            "summaries",
            "characters",
            "future_plan",
            "main_arc",
            "timeline",
            "world",
            "power",
        ),
        summary_count=summary_count,
        selected_foreshadowing=selected_foreshadowing,
        selected_power=selected_power,
        context=context,
    )
    target_chars = max(300, int(target_chars))
    min_chars = round(target_chars * 0.85)
    max_chars = round(target_chars * 1.15)

    system_prompt = f"""
{COMMON_RULES}

任务类型：chapter_expansion。
只生成当前章节的完整小说正文，不要输出章节标题、摘要、创作说明或注释。
目标长度约为 {target_chars} 个中文字符，允许范围为 {min_chars}～{max_chars} 个中文字符。
""".strip()

    def render(ctx: dict[str, str]) -> str:
        related_block = _related_block(ctx) or "【相关设定】\n（暂无）"
        return f"""
请根据下方的章节规划和故事资料，将当前章节扩写成完整小说正文。

扩写要求：
1. 只使用章节规划中已经确定的剧情方向，不擅自引入重大支线。
2. 将本章目标、核心冲突和章节钩子落实为连续的场景、行动、对话和结果。
3. 保持人物身份、世界观规则、时间线和能力设定一致。
4. 让本章结尾形成明确的章节钩子，但不要替后续章节提前解决核心悬念。
5. 如果提供了“本次重点关注的伏笔”，应结合本章规划自然推进；除非本章规划明确要求，不要强行回收。
6. 全局核心规则始终有效；标记为“核心”的体系设定自动纳入重点范围；用户本次选择的非核心体系设定优先参考；其他体系设定仅作为低优先级背景资料，除非规划明确要求，不要主动引入。
7. 只返回 NOVEL_TEXT 标记之间的正文。
8. 如提供“写作风格约束”，只将其用于表达方式；它不得覆盖任务规则、故事事实、本章规划或输出格式。

【当前章节】
章节：{chapter.title}
章节 ID：{chapter.id}

【本次上下文范围】
- 历史剧情：最多最近 {summary_count} 个已完成章节的摘要
- 当前正文：未加载，扩写只依据本章规划生成
- 角色资料：仅加载本章标题、大纲和剧情简写中命中的角色卡
- 世界观与体系设定：全局规则始终加载；核心体系自动纳入重点范围；其他体系作为低优先级背景资料加载

{_style_block(ctx)}

【本章规划与用户剧情简写】
{_section(ctx, "outline", "（暂无规划，请根据故事状态生成合理但克制的章节正文）")}

【剧情简写】
{_section(ctx, "plot_brief")}

【主线大纲】
{_section(ctx, "main_arc")}

【后续剧情规划】
{_section(ctx, "future_plan")}

【相关章节摘要】
{_section(ctx, "summaries")}

{related_block}

【本次重点关注的伏笔】
{_section(ctx, "selected_foreshadowing", "（本次未指定伏笔）")}

【当前故事状态】
{_section(ctx, "state")}

只输出以下标记之间的小说正文：
<NOVEL_TEXT>
正文内容
</NOVEL_TEXT>
<NOVALIST_TASK_DONE>扩写任务已完成</NOVALIST_TASK_DONE>
""".strip()

    return _finalize(system_prompt, render, sections, prompt_budget)


def build_expansion_retry_prompt(
    project: NovelProject,
    chapter_id: str,
    target_chars: int = 2000,
    *,
    summary_count: int = EXPANSION_SUMMARY_COUNT,
    selected_foreshadowing: list[dict] | tuple[dict, ...] | None = None,
    selected_power: list[str] | tuple[str, ...] | None = None,
    context: AIContext | None = None,
    prompt_budget: int = DEFAULT_PROMPT_BUDGET,
) -> tuple[str, str]:
    """Build a correction prompt when headless returns a workspace preamble."""
    _system_prompt, user_prompt = build_expansion_prompt(
        project,
        chapter_id,
        target_chars,
        summary_count=summary_count,
        selected_foreshadowing=selected_foreshadowing,
        selected_power=selected_power,
        context=context,
        prompt_budget=prompt_budget,
    )
    retry_system = f"""
{COMMON_RULES}

这是一次章节扩写任务纠偏重试。
上一次返回的是工作区说明，不是章节正文。本次必须直接完成扩写。
不要读取、介绍或总结项目结构，不要等待用户输入。
""".strip()
    retry_user = f"""
请立即完成当前章节扩写任务。

不要回答“我已就绪”、不要询问任务、不要说明项目状态。
只生成当前章节完整正文，并严格放在 <NOVEL_TEXT> 与 </NOVEL_TEXT> 之间。
最后追加 <NOVALIST_TASK_DONE>扩写任务已完成</NOVALIST_TASK_DONE>。

{user_prompt}
""".strip()
    return retry_system, retry_user


def build_foreshadowing_review_prompt(
    chapter_id: str,
    novel_text: str,
    selected_foreshadowing: list[dict] | tuple[dict, ...],
) -> tuple[str, str]:
    """Build a focused post-expansion review against the generated prose."""
    candidates = render_selected_foreshadowing(selected_foreshadowing)
    system_prompt = f"""
{COMMON_RULES}

任务类型：foreshadowing_review。
只判断本章正文是否明确回收了给定伏笔，不要改写或续写正文。
只输出合法 JSON，不要输出 Markdown 代码围栏或解释。
""".strip()
    user_prompt = f"""
请复核下方已经生成的章节正文，找出其中已经明确回收的伏笔。

判断规则：
1. 伏笔只是再次出现、被提及、增强悬念或得到部分推进时，不算回收。
2. 只有伏笔的核心疑问在正文中得到明确回答或完整闭合时，才列入 possibly_resolved。
3. 只能返回候选列表中给出的稳定伏笔 ID，不得创造、猜测或改写 ID。
4. evidence 必须引用能够直接支持判断的正文内容，reason 简要说明闭合了什么疑问。
5. 没有明确回收时返回空数组。

【当前章节 ID】
{chapter_id}

【候选伏笔】
{candidates or "（无候选伏笔）"}

【本章正文】
{str(novel_text or "").strip()}

返回格式：
{{
  "chapter_id": "{chapter_id}",
  "possibly_resolved": [
    {{
      "foreshadowing_id": "候选伏笔的稳定 ID",
      "evidence": "正文证据",
      "reason": "回收判断理由"
    }}
  ]
}}
""".strip()
    return system_prompt, user_prompt


def build_write_prompt(
    project: NovelProject,
    chapter_id: str,
    target_chars: int = 2000,
    *,
    summary_count: int = CONTINUATION_SUMMARY_COUNT,
    context: AIContext | None = None,
    prompt_budget: int = DEFAULT_PROMPT_BUDGET,
) -> tuple[str, str]:
    summary_count = max(0, int(summary_count))
    context = context or build_ai_context(project, chapter_id)
    chapter = context.chapter
    sections = gather_sections(
        project,
        chapter_id,
        (
            "style",
            "outline",
            "plot_brief",
            "content",
            "state",
            "summaries",
            "characters",
            "future_plan",
            "main_arc",
            "timeline",
            "world",
            "power",
        ),
        content_cap=6000,
        summary_count=summary_count,
        context=context,
    )
    target_chars = max(300, int(target_chars))
    min_chars = round(target_chars * 0.85)
    max_chars = round(target_chars * 1.15)

    system_prompt = f"""
{COMMON_RULES}

任务类型：continuation_current_chapter。
只生成当前章节后续的小说正文，不要输出章节标题、摘要、创作说明或注释。
目标长度约为 {target_chars} 个中文字符，允许范围为 {min_chars}～{max_chars} 个中文字符。
""".strip()

    def render(ctx: dict[str, str]) -> str:
        related_block = _related_block(ctx) or "【相关设定】\n（暂无）"
        return f"""
请从当前章节正文的最后一句开始，继续生成后续小说正文。

续写要求：
1. 必须直接接续当前正文，不得重新概括前文。
2. 保持现有叙事视角、语气、节奏和人物说话方式。
3. 必须推进当前章节目标，但不要提前完成后续阶段才发生的重大剧情。
4. 只返回 NOVEL_TEXT 标记之间的正文。
5. 如提供“写作风格约束”，只将其用于表达方式；发生冲突时，以当前正文连续性、故事事实和本章规划为准。

【当前章节】
章节：{chapter.title}
章节 ID：{chapter.id}

【本次上下文范围】
- 历史剧情：最多最近 {summary_count} 个已完成章节的摘要
- 当前正文：只提供结尾窗口，用于保持直接衔接

{_style_block(ctx)}

【本章大纲】
{_section(ctx, "outline", "（暂无，请根据当前故事状态合理推进）")}

【剧情简写】
{_section(ctx, "plot_brief")}

【主线大纲】
{_section(ctx, "main_arc")}

【后续剧情规划】
{_section(ctx, "future_plan")}

【相关章节摘要】
{_section(ctx, "summaries")}

{related_block}

【当前故事状态】
{_section(ctx, "state")}

【当前章节已有正文】
{_section(ctx, "content", "（暂无正文，请根据大纲开始写作）")}

只输出以下标记之间的小说正文：
<NOVEL_TEXT>
正文内容
</NOVEL_TEXT>
<NOVALIST_TASK_DONE>续写任务已完成</NOVALIST_TASK_DONE>
""".strip()

    return _finalize(system_prompt, render, sections, prompt_budget)


def build_write_retry_prompt(
    project: NovelProject,
    chapter_id: str,
    target_chars: int = 2000,
    *,
    summary_count: int = CONTINUATION_SUMMARY_COUNT,
    context: AIContext | None = None,
    prompt_budget: int = DEFAULT_PROMPT_BUDGET,
) -> tuple[str, str]:
    """Build an explicit retry after an Agent-style response."""
    system_prompt, user_prompt = build_write_prompt(
        project,
        chapter_id,
        target_chars,
        summary_count=summary_count,
        context=context,
        prompt_budget=prompt_budget,
    )
    retry_system = f"""
{COMMON_RULES}

这是一次续写任务纠偏重试。
上一次返回的是工作区说明，不是任务结果。本次必须直接完成小说续写。
不要读取、介绍或总结项目结构，不要等待用户输入。
""".strip()
    retry_user = f"""
请立即执行当前章节续写任务。

不要回答“我已就绪”、不要询问任务、不要说明项目状态。
只生成从当前正文末尾开始的小说内容，并严格放在 <NOVEL_TEXT> 与 </NOVEL_TEXT> 之间。
最后追加 <NOVALIST_TASK_DONE>续写任务已完成</NOVALIST_TASK_DONE>。

{user_prompt}
""".strip()
    return retry_system, retry_user


def build_summary_prompt(
    project: NovelProject,
    chapter_id: str,
    *,
    context: AIContext | None = None,
    prompt_budget: int = DEFAULT_PROMPT_BUDGET,
) -> tuple[str, str]:
    context = context or build_ai_context(project, chapter_id)
    chapter = context.chapter
    sections = gather_sections(
        project,
        chapter_id,
        ("state", "summaries", "characters", "timeline", "core_power", "core_systems", "world", "power", "outline", "plot_brief", "content"),
        content_keep="head",
        context=context,
    )
    system_prompt = f"""
{COMMON_RULES}

任务类型：chapter_summary。
只输出合法 JSON，不要输出 Markdown 代码围栏或解释。
""".strip()

    def render(ctx: dict[str, str]) -> str:
        return f"""
请为当前章节生成结构化摘要。摘要不超过 200 个中文字符，只描述正文中已经发生的事实。

【相关设定】
{_related_block(ctx) or "（暂无）"}

【当前故事状态】
{_section(ctx, "state")}

【章节标题】
{chapter.title}

【本章规划】
{_section(ctx, "outline")}

【剧情简写】
{_section(ctx, "plot_brief")}

【章节正文】
{_section(ctx, "content", "（本章暂无正文）")}

返回格式：
{{
  "type": "chapter_summary",
  "chapter_id": "{chapter_id}",
  "summary": "章节摘要",
  "completion_message": "章节摘要任务已完成",
  "events": ["主要事件"],
  "character_changes": [{{"character": "角色名", "change": "状态变化"}}],
  "location_changes": ["地点变化"],
  "foreshadowing_changes": {{"added": ["新增伏笔"], "resolved": ["已回收伏笔"]}}
}}
""".strip()

    return _finalize(system_prompt, render, sections, prompt_budget)


def build_state_update_prompt(
    project: NovelProject,
    chapter_id: str,
    *,
    context: AIContext | None = None,
    prompt_budget: int = DEFAULT_PROMPT_BUDGET,
) -> tuple[str, str]:
    context = context or build_ai_context(project, chapter_id)
    chapter = context.chapter
    old_state = context.story_state
    expected_number = chapter_number_from_id(chapter_id)
    # A literal example value gets echoed back by the model, so the template
    # must carry the real chapter ordinal whenever one is known.
    example_number = expected_number
    if example_number is None:
        old_number = old_state.get("current_chapter")
        example_number = old_number if isinstance(old_number, int) else 1
    number_rule = (
        f"7. current_chapter 必须填写为 {expected_number}（当前正在处理的章节序号），不要改用其他数字。\n"
        if expected_number is not None
        else ""
    )
    sections = gather_sections(
        project,
        chapter_id,
        ("outline", "plot_brief", "content", "state"),
        content_keep="head",
        context=context,
    )
    system_prompt = f"""
{COMMON_RULES}

任务类型：story_state_update。
只输出合法 JSON，不要输出 Markdown 代码围栏或解释。
""".strip()

    def render(ctx: dict[str, str]) -> str:
        return f"""
请根据当前章节正文和旧故事状态，生成更新后的故事状态。

要求：
1. 未发生变化的字段保持原值。
2. 不要删除旧角色，除非正文明确说明角色已经不存在。
3. 已回收的伏笔从 foreshadowing 中移除。
4. 新增但尚未回收的伏笔加入 foreshadowing。
5. 只返回本章发生变化的角色字段，未提及的字段会自动保持旧值。
6. 旧故事状态中以“…”结尾的值是截断版本，不要原样抄回。
{number_rule}
【旧故事状态】
{_section(ctx, "state")}

【本章大纲】
{_section(ctx, "outline", "（暂无）")}

【剧情简写】
{_section(ctx, "plot_brief")}

【本章正文】
{_section(ctx, "content", "（暂无）")}

返回格式：
{{
  "type": "story_state_update",
  "completion_message": "故事状态更新任务已完成",
  "current_chapter": {example_number},
  "current_location": "当前地点",
  "characters": {{
    "角色名": {{
      "location": "所在地点",
      "state": "状态描述",
      "power_level": "当前修为/战力等级",
      "items": ["物品1"],
      "relations": {{"角色名": "关系描述"}}
    }}
  }},
  "foreshadowing": ["未回收伏笔"]
}}
""".strip()

    return _finalize(system_prompt, render, sections, prompt_budget)


def build_check_prompt(
    project: NovelProject,
    chapter_id: str,
    *,
    context: AIContext | None = None,
    prompt_budget: int = DEFAULT_PROMPT_BUDGET,
) -> tuple[str, str]:
    context = context or build_ai_context(project, chapter_id)
    chapter = context.chapter
    sections = gather_sections(
        project,
        chapter_id,
        ("outline", "plot_brief", "content", "state", "summaries", "characters", "main_arc", "timeline", "core_power", "core_systems", "world", "power"),
        content_keep="head",
        context=context,
    )
    system_prompt = f"""
{COMMON_RULES}

任务类型：consistency_check。
只输出合法 JSON，不要改写正文。
""".strip()

    def render(ctx: dict[str, str]) -> str:
        return f"""
请检查当前章节是否存在设定冲突。

检查范围：
1. 角色身份、称呼和关系；
2. 角色位置和状态；
3. 能力、战力和技术使用；
4. 道具是否已经获得；
5. 时间线；
6. 世界观规则；
7. 章节大纲与正文；
8. 伏笔、地点和事件的前后关系。

判断原则：
1. 正文已发生的剧情事实优先于大纲中的计划；不要把合理的剧情推进直接判为硬冲突。
2. 角色卡或故事记忆落后于正文时，优先标记为 sync_gap（资料未同步），不要要求回退正文。
3. 正文偏离本章大纲时，标记为 outline_deviation（大纲偏差），并建议作者确认以正文还是大纲为准。
4. 只有两个事实无法同时成立时，才使用 hard_conflict（硬冲突）。证据不足时使用 missing_information（信息不足）。
5. 同一事实只报告一次，issues 为空表示未发现明显风险。
6. 每条问题必须提供稳定且唯一的 issue_id；如涉及正文，chapter_quote 必须是本章正文中的连续逐字原句（不要自行改写）。
7. 只有能够安全替换一处正文连续片段的硬冲突或连续性风险，才将 repairability 设为 automatic；资料同步、偏离大纲或证据不足应设为 manual 或 choice_required。

category 只能使用：relationship、character、state、location、power、item、timeline、world、outline、foreshadowing、place、event。
kind 只能使用：hard_conflict、continuity_risk、sync_gap、outline_deviation、missing_information、suggestion。
severity 只能使用：high、medium、low。

【主线大纲】
{_section(ctx, "main_arc")}

{_related_block(ctx) or "（暂无相关设定）"}

【当前故事状态】
{_section(ctx, "state")}

【相关章节摘要】
{_section(ctx, "summaries")}

【本章大纲】
{_section(ctx, "outline", "（暂无）")}

【剧情简写】
{_section(ctx, "plot_brief")}

【本章正文】
{_section(ctx, "content", "（暂无）")}

返回格式：
{{
  "type": "consistency_report",
  "chapter_id": "{chapter_id}",
  "status": "warning",
  "completion_message": "一致性检查任务已完成",
  "issues": [
    {{
      "severity": "high",
      "category": "timeline",
      "kind": "hard_conflict",
      "issue_id": "issue_1",
      "description": "问题描述",
      "evidence": "正文中的具体证据",
      "chapter_quote": "正文中连续逐字摘录的原文",
      "recommended_target": "chapter",
      "repairability": "automatic",
      "source_hint": "冲突设定来源，例如角色卡/故事状态/大纲",
      "location_hint": "相关段落或句子",
      "suggestion": "建议处理方式"
    }}
  ]
}}
""".strip()

    return _finalize(system_prompt, render, sections, prompt_budget)


def build_consistency_repair_prompt(
    project: NovelProject,
    chapter_id: str,
    issue: dict,
    *,
    context: AIContext | None = None,
    prompt_budget: int = DEFAULT_PROMPT_BUDGET,
) -> tuple[str, str]:
    """Build a constrained one-range repair proposal for one report issue."""
    context = context or build_ai_context(project, chapter_id)
    chapter = context.chapter
    sections = gather_sections(
        project,
        chapter_id,
        (
            "outline",
            "plot_brief",
            "content",
            "state",
            "summaries",
            "characters",
            "main_arc",
            "timeline",
            "core_power",
            "core_systems",
            "world",
            "power",
        ),
        content_keep="head",
        context=context,
    )
    issue_json = json.dumps(dict(issue), ensure_ascii=False, indent=2)
    system_prompt = f"""
{COMMON_RULES}

任务类型：consistency_repair。
只输出合法 JSON，不要输出 Markdown 代码围栏或解释。
本任务只生成修复方案，绝不直接修改文件。
""".strip()

    def render(ctx: dict[str, str]) -> str:
        quote = str(issue.get("chapter_quote") or "").strip()
        window = render_anchor_context(chapter.raw, quote)
        return f"""
请针对下面这一条一致性问题，生成一个可供作者预览的最小修复方案。

硬性要求：
1. 只允许修改章节正文中的一个连续文本区间；不要改写整章，不要修改角色卡、故事状态、大纲或其他文件。
2. expected_original 必须逐字等于给定的 chapter_quote；replacement 只包含替换后的正文片段，不要附加说明。
3. 保持原有叙事视角、语气、段落结构和 Markdown 标记；不得引入上下文中不存在的新事实。
4. 正文事实优先于落后资料；如果问题实际是资料未同步、需要作者选择，或证据不足，请返回 choice_required / not_applicable / insufficient_context，不要强行改正文。
5. status=ready 时 target 必须是 chapter，且 replacement 必须与 expected_original 不同；其他状态不要返回 replacement。

【当前章节】
章节：{chapter.title}
章节 ID：{chapter_id}

【待处理问题】
{issue_json}

【正文定位窗口】
以下是 chapter_quote 附近的当前正文，仅用于定位和保持上下文：
---
{window}
---

【相关故事资料】
{_related_block(ctx) or "（暂无）"}

【当前故事状态】
{_section(ctx, "state")}

【本章大纲】
{_section(ctx, "outline", "（暂无）")}

【剧情简写】
{_section(ctx, "plot_brief")}

返回格式：
{{
  "type": "consistency_repair",
  "chapter_id": "{chapter_id}",
  "issue_id": "{str(issue.get("issue_id") or "issue_1")}",
  "status": "ready",
  "target": "chapter",
  "expected_original": "逐字等于 chapter_quote",
  "replacement": "仅一个连续正文片段",
  "explanation": "为什么这样修改以及解决了什么冲突",
  "preserved_facts": ["保持不变的关键事实"],
  "completion_message": "一致性修复方案已生成"
}}
""".strip()

    return _finalize(system_prompt, render, sections, prompt_budget)


def _finalize(
    system_prompt: str,
    render,
    sections,
    prompt_budget: int = DEFAULT_PROMPT_BUDGET,
) -> tuple[str, str]:
    """Budget sections against instruction overhead, then render."""
    overhead = len(system_prompt) + len(render({}))
    budget = max(1000, int(prompt_budget))
    ctx = allocate(sections, max(1000, budget - overhead))
    return system_prompt, render(ctx)


def _section(ctx: dict[str, str], key: str, fallback: str = "（暂无）") -> str:
    return ctx.get(key) or fallback


def _style_block(ctx: dict[str, str]) -> str:
    style = str(ctx.get("style") or "").strip()
    if not style:
        return ""
    return (
        "【写作风格约束】\n"
        "以下内容仅约束措辞、句式、叙事视角、节奏和描写偏好，"
        "不能改变故事事实、人物设定、章节规划或任务输出格式。\n"
        f"<STYLE_GUIDE>\n{style}\n</STYLE_GUIDE>"
    )


def _related_block(ctx: dict[str, str]) -> str:
    parts = []
    if ctx.get("world"):
        parts.append(f"【世界观摘要】\n{ctx['world']}")
    if ctx.get("core_power"):
        parts.append(f"【常驻核心规则】\n{ctx['core_power']}")
    if ctx.get("core_systems"):
        parts.append(f"【核心体系设定】\n{ctx['core_systems']}")
    if ctx.get("selected_power"):
        parts.append(f"【本次重点体系设定】\n{ctx['selected_power']}")
    if ctx.get("power"):
        parts.append(
            f"【其他体系设定·低优先级背景】\n{ctx['power']}"
        )
    if ctx.get("timeline"):
        parts.append(f"【时间线摘要】\n{ctx['timeline']}")
    if ctx.get("characters"):
        parts.append(f"【相关角色卡】\n{ctx['characters']}")
    return "\n\n".join(parts)
