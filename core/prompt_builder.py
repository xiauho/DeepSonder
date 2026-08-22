"""Prompt builders for the task-oriented dsh writing features."""

from __future__ import annotations

import json

from .project import NovelProject

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
) -> tuple[str, str]:
    """Build a compact outline-to-chapter expansion task.

    Unlike continuation, this task deliberately excludes the current chapter
    body.  The user's outline is the source of truth for the new draft.
    """
    chapter = project.load_chapter(chapter_id)
    related = project.find_related_canon(chapter_id)
    state = project.load_story_state()
    context = _build_story_context(project, chapter_id)
    target_chars = max(300, int(target_chars))
    min_chars = round(target_chars * 0.85)
    max_chars = round(target_chars * 1.15)

    system_prompt = f"""
{COMMON_RULES}

任务类型：chapter_expansion。
只生成当前章节的完整小说正文，不要输出章节标题、摘要、创作说明或注释。
目标长度约为 {target_chars} 个中文字符，允许范围为 {min_chars}～{max_chars} 个中文字符。
""".strip()

    user_prompt = f"""
请根据下方的章节规划和故事资料，将当前章节扩写成完整小说正文。

扩写要求：
1. 只使用章节规划中已经确定的剧情方向，不擅自引入重大支线。
2. 将本章目标、核心冲突和章节钩子落实为连续的场景、行动、对话和结果。
3. 保持人物身份、世界观规则、时间线和能力设定一致。
4. 让本章结尾形成明确的章节钩子，但不要替后续章节提前解决核心悬念。
5. 只返回 NOVEL_TEXT 标记之间的正文。

【当前章节】
章节：{chapter.title}
章节 ID：{chapter.id}

【本章规划与用户剧情简写】
{chapter.outline or "（暂无规划，请根据故事状态生成合理但克制的章节正文）"}

【主线大纲】
{context["main_arc"] or "（暂无）"}

【后续剧情规划】
{context["future_plan"] or "（暂无）"}

【相关章节摘要】
{context["summaries"] or "（暂无）"}

{related.to_block() or "【相关设定】\n（暂无）"}

【当前故事状态】
{json.dumps(state, ensure_ascii=False, indent=2)}

只输出以下标记之间的小说正文：
<NOVEL_TEXT>
正文内容
</NOVEL_TEXT>
<NOVALIST_TASK_DONE>扩写任务已完成</NOVALIST_TASK_DONE>
""".strip()
    return system_prompt, user_prompt


def build_expansion_retry_prompt(
    project: NovelProject,
    chapter_id: str,
    target_chars: int = 2000,
) -> tuple[str, str]:
    """Build a correction prompt when headless returns a workspace preamble."""
    _system_prompt, user_prompt = build_expansion_prompt(project, chapter_id, target_chars)
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


def build_write_prompt(
    project: NovelProject,
    chapter_id: str,
    target_chars: int = 2000,
) -> tuple[str, str]:
    chapter = project.load_chapter(chapter_id)
    related = project.find_related_canon(chapter_id)
    state = project.load_story_state()
    context = _build_story_context(project, chapter_id)
    target_chars = max(300, int(target_chars))
    min_chars = round(target_chars * 0.85)
    max_chars = round(target_chars * 1.15)

    system_prompt = f"""
{COMMON_RULES}

任务类型：continuation_current_chapter。
只生成当前章节后续的小说正文，不要输出章节标题、摘要、创作说明或注释。
目标长度约为 {target_chars} 个中文字符，允许范围为 {min_chars}～{max_chars} 个中文字符。
""".strip()

    user_prompt = f"""
请从当前章节正文的最后一句开始，继续生成后续小说正文。

续写要求：
1. 必须直接接续当前正文，不得重新概括前文。
2. 保持现有叙事视角、语气、节奏和人物说话方式。
3. 必须推进当前章节目标，但不要提前完成后续阶段才发生的重大剧情。
4. 只返回 NOVEL_TEXT 标记之间的正文。

【当前章节】
章节：{chapter.title}
章节 ID：{chapter.id}

【本章大纲】
{chapter.outline or "（暂无，请根据当前故事状态合理推进）"}

【主线大纲】
{context["main_arc"] or "（暂无）"}

【后续剧情规划】
{context["future_plan"] or "（暂无）"}

【相关章节摘要】
{context["summaries"] or "（暂无）"}

{related.to_block() or "【相关设定】\n（暂无）"}

【当前故事状态】
{json.dumps(state, ensure_ascii=False, indent=2)}

【当前章节已有正文】
{chapter.content or "（暂无正文，请根据大纲开始写作）"}

只输出以下标记之间的小说正文：
<NOVEL_TEXT>
正文内容
</NOVEL_TEXT>
<NOVALIST_TASK_DONE>续写任务已完成</NOVALIST_TASK_DONE>
""".strip()
    return system_prompt, user_prompt


def build_write_retry_prompt(
    project: NovelProject,
    chapter_id: str,
    target_chars: int = 2000,
) -> tuple[str, str]:
    """Build an explicit retry after an Agent-style response."""
    system_prompt, user_prompt = build_write_prompt(project, chapter_id, target_chars)
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


def build_summary_prompt(project: NovelProject, chapter_id: str) -> tuple[str, str]:
    chapter = project.load_chapter(chapter_id)
    related = project.find_related_canon(chapter_id)
    state = project.load_story_state()
    system_prompt = f"""
{COMMON_RULES}

任务类型：chapter_summary。
只输出合法 JSON，不要输出 Markdown 代码围栏或解释。
""".strip()
    user_prompt = f"""
请为当前章节生成结构化摘要。摘要不超过 200 个中文字符，只描述正文中已经发生的事实。

【相关设定】
{related.to_block() or "（暂无）"}

【当前故事状态】
{json.dumps(state, ensure_ascii=False, indent=2)}

【章节标题】
{chapter.title}

【章节正文】
{chapter.content or "（本章暂无正文）"}

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
    return system_prompt, user_prompt


def build_state_update_prompt(project: NovelProject, chapter_id: str) -> tuple[str, str]:
    chapter = project.load_chapter(chapter_id)
    old_state = project.load_story_state()

    system_prompt = f"""
{COMMON_RULES}

任务类型：story_state_update。
只输出合法 JSON，不要输出 Markdown 代码围栏或解释。
""".strip()

    user_prompt = f"""
请根据当前章节正文和旧故事状态，生成更新后的故事状态。

要求：
1. 未发生变化的字段保持原值。
2. 不要删除旧角色，除非正文明确说明角色已经不存在。
3. 已回收的伏笔从 foreshadowing 中移除。
4. 新增但尚未回收的伏笔加入 foreshadowing。

【旧故事状态】
{json.dumps(old_state, ensure_ascii=False, indent=2)}

【本章大纲】
{chapter.outline or "（暂无）"}

【本章正文】
{chapter.content or "（暂无）"}

返回格式：
{{
  "type": "story_state_update",
  "completion_message": "故事状态更新任务已完成",
  "current_chapter": 1,
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
    return system_prompt, user_prompt


def build_check_prompt(project: NovelProject, chapter_id: str) -> tuple[str, str]:
    chapter = project.load_chapter(chapter_id)
    related = project.find_related_canon(chapter_id)
    state = project.load_story_state()
    context = _build_story_context(project, chapter_id)

    system_prompt = f"""
{COMMON_RULES}

任务类型：consistency_check。
只输出合法 JSON，不要改写正文。
""".strip()

    user_prompt = f"""
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

【主线大纲】
{context["main_arc"] or "（暂无）"}

【相关设定】
{related.to_block() or "（暂无相关设定）"}

【当前故事状态】
{json.dumps(state, ensure_ascii=False, indent=2)}

【本章大纲】
{chapter.outline or "（暂无）"}

【本章正文】
{chapter.content or "（暂无）"}

返回格式：
{{
  "type": "consistency_report",
  "chapter_id": "{chapter_id}",
  "status": "ok",
  "completion_message": "一致性检查任务已完成",
  "issues": [
    {{
      "severity": "high",
      "category": "timeline",
      "description": "问题描述",
      "evidence": "正文中的具体证据",
      "location_hint": "相关段落或句子",
      "suggestion": "建议处理方式"
    }}
  ]
}}
""".strip()
    return system_prompt, user_prompt


def _build_story_context(project: NovelProject, chapter_id: str) -> dict[str, str]:
    summaries = project.load_chapter_summaries()
    summary_items = []
    for key, value in summaries.items():
        if key == chapter_id:
            continue
        if str(value).strip():
            summary_items.append(f"### {key}\n{str(value).strip()}")
    return {
        "main_arc": project.load_main_arc(),
        "future_plan": project.load_future_plan(),
        "summaries": "\n\n".join(summary_items[-5:]),
    }
