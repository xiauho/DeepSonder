"""Prompt builders for the task-oriented dsh writing features."""

from __future__ import annotations

import json
from dataclasses import replace

from .context_budget import (
    AIContext,
    CONTINUATION_SUMMARY_COUNT,
    DEFAULT_PROMPT_BUDGET,
    EXPANSION_SUMMARY_COUNT,
    DROPPED_PLACEHOLDER,
    allocate_with_report,
    build_ai_context,
    build_task_context,
    gather_sections,
    render_selected_foreshadowing,
)
from .context_report import PromptBundle, PromptContextReport, SectionUsage
from .context_selection import CanonSelectionStat
from .context_profiles import (
    CONSISTENCY_CONTEXT_PROFILE,
    CONTINUATION_CONTEXT_PROFILE,
    EXPANSION_CONTEXT_PROFILE,
    REPAIR_CONTEXT_PROFILE,
)
from .project import NovelProject
from .text_anchor import render_anchor_context
from .history_context import history_token_budget
from .length_policy import assess_length
from .text_metrics import count_content_chars
from .token_budget import DEFAULT_TOKEN_SAFETY_FACTOR

COMMON_RULES = """
你是 DeepSonder 的小说创作 AI。

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
    target_chars: int,
    *,
    summary_count: int = EXPANSION_SUMMARY_COUNT,
    selected_foreshadowing: list[dict] | tuple[dict, ...] | None = None,
    selected_power: list[str] | tuple[str, ...] | None = None,
    context: AIContext | None = None,
    prompt_budget: int = DEFAULT_PROMPT_BUDGET,
    history_token_limit: int | None = None,
) -> PromptBundle:
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
        profile=EXPANSION_CONTEXT_PROFILE,
        relevance_query=json.dumps(
            list(selected_foreshadowing or ()),
            ensure_ascii=False,
            default=str,
        ),
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
    assessment = assess_length(target_chars, target_chars)
    min_chars = assessment.preferred_min
    max_chars = assessment.preferred_max

    system_prompt = f"""
{COMMON_RULES}

任务类型：chapter_expansion。
只生成当前章节的完整小说正文，不要输出章节标题、摘要、创作说明或注释。
{_render_length_contract(target_chars, min_chars, max_chars, subject="完整正文")}
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
9. 在内部将本章已有剧情节点拆成 4～6 个连续场景，并为动作、对话、心理、环境和结果分配篇幅；不要输出场景规划。
10. 剧情完整不等于长度达标。写完后在内部检查篇幅，正文未达到 {min_chars} 字时继续展开已有场景，不要提前输出完成标记。

【当前章节】
章节：{chapter.title}
章节 ID：{chapter.id}

【本次上下文范围】
- 历史剧情：最多最近 {summary_count} 个已完成章节的摘要
- 如提供更早的已采用记忆，仅将其作为关联事实参考；来源版本未确认的旧摘要不视为已验证事实。
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

    return _finalize(
        system_prompt,
        render,
        sections,
        prompt_budget,
        task_kind="chapter_expansion",
        history_token_limit=history_token_limit,
        chapter_id=chapter_id,
        history_requested=summary_count,
        state_scope=context.state_scope,
        style_root=context.project_root,
        selection_stats=context.related.selection,
    )


def build_expansion_retry_prompt(
    project: NovelProject,
    chapter_id: str,
    target_chars: int,
    *,
    summary_count: int = EXPANSION_SUMMARY_COUNT,
    selected_foreshadowing: list[dict] | tuple[dict, ...] | None = None,
    selected_power: list[str] | tuple[str, ...] | None = None,
    context: AIContext | None = None,
    prompt_budget: int = DEFAULT_PROMPT_BUDGET,
    history_token_limit: int | None = None,
) -> PromptBundle:
    """Build a correction prompt when headless returns a workspace preamble."""
    base = build_expansion_prompt(
        project,
        chapter_id,
        target_chars,
        summary_count=summary_count,
        selected_foreshadowing=selected_foreshadowing,
        selected_power=selected_power,
        context=context,
        prompt_budget=prompt_budget,
        history_token_limit=history_token_limit,
    )
    user_prompt = base.user_prompt
    retry_system = f"""
{base.system_prompt}

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
    return _rebundle(base, retry_system, retry_user, "chapter_expansion_retry")


def build_expansion_length_retry_prompt(
    base: PromptBundle,
    chapter_id: str,
    candidate_text: str,
    target_chars: int,
) -> PromptBundle:
    """Build a full-draft retry when an expansion is severely under length."""
    source = str(candidate_text or "").strip()
    actual = count_content_chars(source)
    assessment = assess_length(actual, target_chars)
    system_prompt = f"""
{base.system_prompt}

这是一次完整正文篇幅纠偏。上一版正文结构有效，但明显低于最低审阅范围。
必须返回一版完整正文；不得只返回新增片段，不得删减上一版已经发生的剧情和事实。
""".strip()
    user_prompt = f"""
{base.user_prompt}

【上一版候选正文】
上一版实际长度：{actual} 字
距离目标还差：{assessment.missing_to_target} 字
请保留其剧情顺序、人物行为和已写事实，通过展开已有场景的动作、对话、心理、环境、转折过程和结果，重新生成完整正文。
不得新增章节规划之外的角色、身世、物品来源、能力或重大事件。

<PREVIOUS_DRAFT>
{source}
</PREVIOUS_DRAFT>
""".strip()
    return _rebundle(
        base,
        system_prompt,
        user_prompt,
        "chapter_expansion_length_retry",
    )


def build_expansion_supplement_prompt(
    chapter_id: str,
    novel_text: str,
    target_chars: int,
    *,
    min_chars: int,
    max_chars: int,
) -> PromptBundle:
    """Backward-compatible wrapper for the shared prose supplement prompt."""
    return build_prose_supplement_prompt(
        chapter_id,
        novel_text,
        target_chars,
        min_chars=min_chars,
        max_chars=max_chars,
        task_kind="chapter_expansion_supplement",
        protocol_type="chapter_expansion_supplement",
    )


def build_prose_supplement_prompt(
    chapter_id: str,
    novel_text: str,
    target_chars: int,
    *,
    min_chars: int | None = None,
    max_chars: int | None = None,
    task_kind: str = "prose_length_supplement",
    protocol_type: str = "prose_length_supplement",
    insertion_points: tuple[dict[str, object], ...] = (),
    story_constraints: str = "",
) -> PromptBundle:
    """Build a compact insertion-only task for any under-length prose."""
    source = str(novel_text or "").strip()
    current_chars = count_content_chars(source)
    target_chars = max(current_chars + 1, int(target_chars))
    missing_chars = max(1, target_chars - current_chars)
    min_chars = int(min_chars) if min_chars is not None else round(target_chars * 0.95)
    max_chars = int(max_chars) if max_chars is not None else round(target_chars * 1.05)
    point_lines = "\n".join(
        f"- {str(point.get('anchor_id') or '')}：{str(point.get('preview') or '')}"
        for point in insertion_points
        if str(point.get("anchor_id") or "").strip()
    )
    system_prompt = f"""
{COMMON_RULES}

任务类型：{task_kind}。
当前候选正文长度不足。只能通过插入新段落补充细节，不得删除、替换、概括或重写原正文。
只输出合法 JSON，不要输出 Markdown 代码围栏、小说正文标记或解释。
""".strip()
    user_prompt = f"""
请为下方章节正文生成少量、精确的插入补丁，使最终正文接近用户设置的目标长度。

【本次固定字数目标】
- 目标正文：{target_chars} 字
- 允许范围：{min_chars}～{max_chars} 字
- 当前正文：{current_chars} 字
- 建议新增：约 {missing_chars} 字

补写要求：
1. 只能扩充原文已有场景中的动作、环境、感官、心理或对话，不得新增重大事件、角色、设定或支线。
2. 只能使用“可用插入位置”中列出的 anchor_id；不要复制或改写原文作为锚点。
3. text 只包含要插入的小说正文；同一个 anchor_id 最多使用一次。
4. 返回 1～4 个插入项，新增正文总量应接近“建议新增”字数。
5. 不得包含章节标题、说明、JSON、Markdown 或协议标记。
6. 严格遵守故事约束，不得自行补充人物身世、物品来源、能力设定或重大事件。

【当前章节 ID】
{chapter_id}

【故事约束】
{str(story_constraints or '').strip() or '（只允许展开当前正文已经出现的事实）'}

【可用插入位置】
{point_lines or '（无可用位置）'}

【当前正文】
{source}

返回格式：
{{
  "type": "{protocol_type}",
  "chapter_id": "{chapter_id}",
  "insertions": [
    {{
      "anchor_id": "P001",
      "text": "需要插入的补写正文"
    }}
  ]
}}
""".strip()
    return _direct_bundle(
        system_prompt,
        user_prompt,
        task_kind=task_kind,
        chapter_id=chapter_id,
        sections=(
            SectionUsage(
                "generated_content",
                len(source),
                len(source),
                "full",
                0,
                "head",
            ),
        ),
    )


def build_foreshadowing_review_prompt(
    chapter_id: str,
    novel_text: str,
    selected_foreshadowing: list[dict] | tuple[dict, ...],
) -> PromptBundle:
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
    return _direct_bundle(
        system_prompt,
        user_prompt,
        task_kind="foreshadowing_review",
        chapter_id=chapter_id,
        sections=(
            SectionUsage(
                "selected_foreshadowing",
                len(candidates),
                len(candidates),
                "full",
                1,
                "head",
            ),
            SectionUsage(
                "generated_content",
                len(str(novel_text or "").strip()),
                len(str(novel_text or "").strip()),
                "full",
                0,
                "head",
            ),
        ),
    )


def build_write_prompt(
    project: NovelProject,
    chapter_id: str,
    target_chars: int = 2000,
    *,
    summary_count: int = CONTINUATION_SUMMARY_COUNT,
    context: AIContext | None = None,
    prompt_budget: int = DEFAULT_PROMPT_BUDGET,
    history_token_limit: int | None = None,
) -> PromptBundle:
    summary_count = max(0, int(summary_count))
    context = context or build_ai_context(
        project,
        chapter_id,
        profile=CONTINUATION_CONTEXT_PROFILE,
    )
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
            "core_power",
            "core_systems",
            "selected_power",
            "power",
        ),
        content_keep="head_tail",
        content_cap=min(16_000, max(6_000, int(prompt_budget) // 4)),
        summary_count=summary_count,
        context=context,
    )
    target_chars = max(300, int(target_chars))
    assessment = assess_length(target_chars, target_chars)
    min_chars = assessment.preferred_min
    max_chars = assessment.preferred_max

    system_prompt = f"""
{COMMON_RULES}

任务类型：continuation_current_chapter。
只生成当前章节后续的小说正文，不要输出章节标题、摘要、创作说明或注释。
{_render_length_contract(target_chars, min_chars, max_chars, subject="本次新增正文")}
""".strip()

    def render(ctx: dict[str, str]) -> str:
        related_block = _related_block(ctx) or "【相关设定】\n（暂无）"
        return f"""
请从当前章节正文的最后一句之后开始，继续生成后续小说正文，不要重复最后一句。

续写要求：
1. 必须直接接续当前正文，不得重新概括前文。
2. 保持现有叙事视角、语气、节奏和人物说话方式。
3. 必须推进当前章节目标，但不要提前完成后续阶段才发生的重大剧情。
4. 只返回 NOVEL_TEXT 标记之间的正文。
5. 在内部按本次需要推进的动作、对话、心理、环境和结果分配篇幅；新增正文未达到 {min_chars} 字时继续展开已有情节，不要提前输出完成标记。
5. 如提供“写作风格约束”，只将其用于表达方式；发生冲突时，以当前正文连续性、故事事实和本章规划为准。

【当前章节】
章节：{chapter.title}
章节 ID：{chapter.id}

【本次上下文范围】
- 历史剧情：最多最近 {summary_count} 个已完成章节的摘要
- 如提供更早的已采用记忆，仅将其作为关联事实参考；来源版本未确认的旧摘要不视为已验证事实。
- 当前正文：保留少量章节开头和更长的最近结尾，用于锁定视角并保持直接衔接

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

    return _finalize(
        system_prompt,
        render,
        sections,
        prompt_budget,
        task_kind="continuation_current_chapter",
        history_token_limit=history_token_limit,
        chapter_id=chapter_id,
        history_requested=summary_count,
        state_scope=context.state_scope,
        style_root=context.project_root,
        selection_stats=context.related.selection,
    )


def build_write_retry_prompt(
    project: NovelProject,
    chapter_id: str,
    target_chars: int = 2000,
    *,
    summary_count: int = CONTINUATION_SUMMARY_COUNT,
    context: AIContext | None = None,
    prompt_budget: int = DEFAULT_PROMPT_BUDGET,
    history_token_limit: int | None = None,
) -> PromptBundle:
    """Build an explicit retry after an Agent-style response."""
    base = build_write_prompt(
        project,
        chapter_id,
        target_chars,
        summary_count=summary_count,
        context=context,
        prompt_budget=prompt_budget,
        history_token_limit=history_token_limit,
    )
    user_prompt = base.user_prompt
    retry_system = f"""
{base.system_prompt}

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
    return _rebundle(base, retry_system, retry_user, "continuation_retry")


def build_continuation_length_retry_prompt(
    base: PromptBundle,
    chapter_id: str,
    candidate_text: str,
    target_chars: int,
) -> PromptBundle:
    """Build a complete continuation-fragment retry after severe undershoot."""
    source = str(candidate_text or "").strip()
    actual = count_content_chars(source)
    assessment = assess_length(actual, target_chars)
    system_prompt = f"""
{base.system_prompt}

这是一次续写片段篇幅纠偏。上一版片段结构有效，但明显低于最低审阅范围。
必须返回一版完整的续写片段，不得只返回补丁，不得重复当前章节已有正文。
""".strip()
    user_prompt = f"""
{base.user_prompt}

【上一版续写候选】
上一版实际长度：{actual} 字
距离本次新增目标还差：{assessment.missing_to_target} 字
请保留其剧情顺序和已写事实，通过展开已有情节重新生成完整续写片段。
不得新增规划之外的角色、身世、物品来源、能力或重大事件。

<PREVIOUS_DRAFT>
{source}
</PREVIOUS_DRAFT>
""".strip()
    return _rebundle(
        base,
        system_prompt,
        user_prompt,
        "continuation_length_retry",
    )


def build_check_prompt(
    project: NovelProject,
    chapter_id: str,
    *,
    context: AIContext | None = None,
    prompt_budget: int = DEFAULT_PROMPT_BUDGET,
) -> PromptBundle:
    context = context or build_ai_context(
        project,
        chapter_id,
        profile=CONSISTENCY_CONTEXT_PROFILE,
    )
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
recommended_target 只能使用：chapter、character_card、story_state、outline、canon、manual。
repairability 只能使用：automatic、choice_required、manual。
禁止使用 data 或其他未列出值作为 recommended_target；无法确定具体资料目标时使用 manual。
只有 recommended_target 为 chapter，且 kind 为 hard_conflict 或 continuity_risk 时，repairability 才可以为 automatic。

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

    return _finalize(
        system_prompt,
        render,
        sections,
        prompt_budget,
        task_kind="consistency_check",
        chapter_id=chapter_id,
        history_requested=EXPANSION_SUMMARY_COUNT,
        state_scope=context.state_scope,
        style_root=context.project_root,
        selection_stats=context.related.selection,
    )


def build_consistency_repair_prompt(
    project: NovelProject,
    chapter_id: str,
    issue: dict,
    *,
    context: AIContext | None = None,
    prompt_budget: int = DEFAULT_PROMPT_BUDGET,
) -> PromptBundle:
    """Build a constrained one-range repair proposal for one report issue."""
    context = context or build_ai_context(
        project,
        chapter_id,
        profile=REPAIR_CONTEXT_PROFILE,
        relevance_query=json.dumps(dict(issue), ensure_ascii=False, default=str),
    )
    chapter = context.chapter
    sections = gather_sections(
        project,
        chapter_id,
        (
            "outline",
            "plot_brief",
            "state",
            "characters",
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

    return _finalize(
        system_prompt,
        render,
        sections,
        prompt_budget,
        task_kind="consistency_repair",
        chapter_id=chapter_id,
        selection_stats=context.related.selection,
        history_requested=EXPANSION_SUMMARY_COUNT,
    )


def _finalize(
    system_prompt: str,
    render,
    sections,
    prompt_budget: int = DEFAULT_PROMPT_BUDGET,
    *,
    task_kind: str = "unknown",
    chapter_id: str = "",
    history_requested: int | None = None,
    history_available: int | None = None,
    selection_stats: tuple[CanonSelectionStat, ...] = (),
    history_token_limit: int | None = None,
    state_scope: str = "",
    style_root: str = "",
) -> PromptBundle:
    """Budget sections against instruction overhead, then render."""
    overhead = len(system_prompt) + len(render({}))
    budget = max(1000, int(prompt_budget))
    context_budget = max(1000, budget - overhead)
    allocation = allocate_with_report(
        sections, context_budget,
        history_token_limit=(
            history_token_budget(int(budget * DEFAULT_TOKEN_SAFETY_FACTOR), "balanced")
            if history_token_limit is None else history_token_limit
        ),
    )
    style = allocation.values.get("style", "")
    start = style.rfind("<STYLE_SAMPLE ")
    if start >= 0 and "</STYLE_SAMPLE>" not in style[start:]:
        style = style[:start].rstrip()
        values = dict(allocation.values, style=style)
        usages = tuple(replace(row, sent_chars=len(style), status="trimmed") if row.key == "style" else row for row in allocation.sections)
        allocation = replace(allocation, values=values, sections=usages)
    user_prompt = render(allocation.values)
    included = None
    history_window = next((s.history for s in sections if s.history is not None), None)
    if allocation.history is not None:
        included = allocation.history.included
        history_available = len(history_window.entries)
        history_requested = history_window.requested
    elif history_requested is not None:
        included = 0
        history_available = 0
    finalized_selection = _finalize_selection_stats(
        selection_stats,
        allocation.values,
    )
    missing_required = [
        item.category
        for item in finalized_selection
        if item.required > 0
        and item.prompt_included is not None
        and item.prompt_included < item.required
    ]
    if missing_required:
        labels = {
            "core_power": "全局核心规则",
            "core_systems": "核心体系",
            "selected_power": "手动选择体系",
        }
        raise RuntimeError(
            "本次上下文预算无法容纳全部核心或手动选择资料："
            + "、".join(labels.get(item, item) for item in missing_required)
            + "。请减少核心/手选资料，或使用已验证的扩展文件传输预算。"
        )
    from .style_library import sample_usage
    report = PromptContextReport(
        style_samples=sample_usage(style_root, user_prompt, chapter_id) if style_root and task_kind in {"chapter_expansion", "continuation_current_chapter"} else (),
        schema_version=1,
        task_kind=task_kind,
        chapter_id=chapter_id,
        prompt_budget=budget,
        context_budget=context_budget,
        overhead_chars=overhead,
        system_prompt_chars=len(system_prompt),
        user_prompt_chars=len(user_prompt),
        total_prompt_chars=len(system_prompt) + len(user_prompt),
        sections=allocation.sections,
        selections=finalized_selection,
        history_requested=history_requested,
        history_available=history_available,
        history_included=included,
        history_in_range=history_window.in_range if history_window else 0,
        history_missing=history_window.missing if history_window else 0,
        history_excluded_budget=allocation.history.excluded_budget if allocation.history else 0,
        history_token_budget=allocation.history.token_budget if allocation.history else 0,
        history_estimated_tokens=allocation.history.estimated_tokens if allocation.history else 0,
        history_remote_candidates=history_window.remote_candidates if history_window else 0,
        history_remote_matched=history_window.remote_matched if history_window else 0,
        history_remote_included=allocation.history.remote_included if allocation.history else 0,
        history_stale=history_window.stale if history_window else 0,
        history_unverified=history_window.unverified if history_window else 0,
        history_provenance_error=history_window.provenance_error if history_window else False,
        history_sources=allocation.history.sources if allocation.history else (),
        state_scope=state_scope,
    )
    return PromptBundle(system_prompt, user_prompt, report)


def _finalize_selection_stats(
    stats: tuple[CanonSelectionStat, ...],
    values: dict[str, str],
) -> tuple[CanonSelectionStat, ...]:
    finalized = []
    for item in stats:
        if item.category not in values:
            continue
        rendered = str(values.get(item.category) or "")
        if not rendered or rendered == DROPPED_PLACEHOLDER:
            prompt_included = 0
        elif item.category in {"core_power", "timeline"}:
            prompt_included = 1
        else:
            headings = sum(
                1 for line in rendered.splitlines() if line.startswith("### ")
            )
            if headings:
                prompt_included = min(item.included, headings)
            else:
                prompt_included = min(1, item.included)
        finalized.append(replace(item, prompt_included=prompt_included))
    return tuple(finalized)


def _direct_bundle(
    system_prompt: str,
    user_prompt: str,
    *,
    task_kind: str,
    chapter_id: str,
    sections: tuple[SectionUsage, ...] = (),
) -> PromptBundle:
    total = len(system_prompt) + len(user_prompt)
    report = PromptContextReport(
        schema_version=1,
        task_kind=task_kind,
        chapter_id=chapter_id,
        prompt_budget=total,
        context_budget=sum(item.sent_chars for item in sections),
        overhead_chars=max(0, total - sum(item.sent_chars for item in sections)),
        system_prompt_chars=len(system_prompt),
        user_prompt_chars=len(user_prompt),
        total_prompt_chars=total,
        sections=sections,
    )
    return PromptBundle(system_prompt, user_prompt, report)


def _rebundle(
    base: PromptBundle,
    system_prompt: str,
    user_prompt: str,
    task_kind: str,
) -> PromptBundle:
    report = replace(
        base.report,
        task_kind=task_kind,
        overhead_chars=max(
            0,
            base.report.overhead_chars
            + len(system_prompt)
            - len(base.system_prompt)
            + len(user_prompt)
            - len(base.user_prompt),
        ),
        system_prompt_chars=len(system_prompt),
        user_prompt_chars=len(user_prompt),
        total_prompt_chars=len(system_prompt) + len(user_prompt),
    )
    return PromptBundle(system_prompt, user_prompt, report)


def _section(ctx: dict[str, str], key: str, fallback: str = "（暂无）") -> str:
    return ctx.get(key) or fallback


def _render_length_contract(
    target_chars: int,
    min_chars: int,
    max_chars: int,
    *,
    subject: str,
) -> str:
    """Render one shared, explicit length contract for every writing attempt."""
    return (
        "【固定字数契约】\n"
        f"- {subject}目标：{int(target_chars)} 字\n"
        f"- 合格范围：{int(min_chars)}～{int(max_chars)} 字\n"
        "- 统计口径：排除空白字符，包含标点、英文和数字\n"
        "- 完成标记只表示响应结束；正文达到合格范围才表示长度达标"
    )


def _style_block(ctx: dict[str, str]) -> str:
    from .writing_style import render_style
    return render_style(str(ctx.get('style') or '').strip())


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
