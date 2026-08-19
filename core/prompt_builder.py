"""Prompt builders for dsh-backed writing features."""

from __future__ import annotations

import json

from .project import NovelProject

WRITER_SYSTEM_PROMPT = """
你是资深网文写作助手。
你必须严格遵循提供的世界观、战力体系和角色设定。
不要擅自新增战力等级，不要改变角色已有性格。
如果正文需要推进剧情，请保持与当前故事状态一致。
不要解释设定，直接输出小说正文。
""".strip()


def build_write_prompt(project: NovelProject, chapter_id: str) -> tuple[str, str]:
    chapter = project.load_chapter(chapter_id)
    related = project.find_related_canon(chapter_id)
    state = project.load_story_state()

    user_prompt = f"""
【当前章节】
章节：{chapter.title}
章节 ID：{chapter.id}

【本章大纲】
{chapter.outline or "（暂无大纲，请根据当前故事状态合理续写）"}

{related.to_block()}

【当前故事状态】
{json.dumps(state, ensure_ascii=False, indent=2)}

【已有正文】
{chapter.content or "（暂无正文，请根据本章大纲开始写作）"}

请续写或补全这一章。保持文风统一，不要擅自改变设定。
""".strip()
    return WRITER_SYSTEM_PROMPT, user_prompt


def build_summary_prompt(project: NovelProject, chapter_id: str) -> tuple[str, str]:
    chapter = project.load_chapter(chapter_id)
    related = project.find_related_canon(chapter_id)
    state = project.load_story_state()
    system_prompt = "你是小说章节摘要助手。只输出简洁的章节摘要，不要评价。"
    user_prompt = f"""
请为以下章节写一份 200 字以内的摘要，包含：
1. 本章主要事件
2. 出场角色及状态变化
3. 新增伏笔或已回收伏笔
4. 战力/等级变化（如有）

【相关设定】
{related.to_block() or "（暂无）"}

【当前故事状态】
{json.dumps(state, ensure_ascii=False, indent=2)}

【章节标题】
{chapter.title}

【章节正文】
{chapter.content or "（本章暂无正文）"}
""".strip()
    return system_prompt, user_prompt


def build_state_update_prompt(project: NovelProject, chapter_id: str) -> tuple[str, str]:
    chapter = project.load_chapter(chapter_id)
    old_state = project.load_story_state()

    system_prompt = """
你是小说故事状态维护器。
请根据章节正文更新故事状态，只输出 JSON，不要输出任何解释。
必须严格遵守以下 JSON 结构：
{
  "current_chapter": 数字,
  "current_location": "当前地点",
  "characters": {
    "角色名": {
      "location": "所在地点",
      "state": "状态描述",
      "power_level": "当前修为/战力等级",
      "items": ["物品1"],
      "relations": {"角色名": "关系描述"}
    }
  },
  "foreshadowing": ["未回收伏笔"]
}
""".strip()

    user_prompt = f"""
请阅读以下章节正文和旧故事状态，输出更新后的 story_state JSON。

【旧故事状态】
{json.dumps(old_state, ensure_ascii=False, indent=2)}

【本章大纲】
{chapter.outline}

【本章正文】
{chapter.content}
""".strip()
    return system_prompt, user_prompt


def build_check_prompt(project: NovelProject, chapter_id: str) -> tuple[str, str]:
    chapter = project.load_chapter(chapter_id)
    related = project.find_related_canon(chapter_id)
    state = project.load_story_state()

    system_prompt = """
你是网文设定一致性检查器。
请检查正文是否存在设定冲突，例如：
- 角色实力越级
- 角色位置/状态突变
- 使用未获得的能力或道具
- 称呼、关系前后不一致
- 时间线矛盾
- 与世界观/战力体系冲突

只输出检查报告，不要改写正文。
格式：
✅ 未发现明显冲突
或
⚠️ 发现以下问题：
- 具体问题（引用原文位置）
""".strip()

    user_prompt = f"""
【相关设定】
{related.to_block() or "（暂无相关设定）"}

【当前故事状态】
{json.dumps(state, ensure_ascii=False, indent=2)}

【本章大纲】
{chapter.outline}

【本章正文】
{chapter.content}
""".strip()
    return system_prompt, user_prompt
