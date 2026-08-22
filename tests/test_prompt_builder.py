from pathlib import Path
from unittest import TestCase

from core import prompt_builder
from core.project import NovelProject


class PromptBuilderTests(TestCase):
    def setUp(self) -> None:
        self.project = NovelProject(Path(__file__).parents[1] / "projects" / "planetary_dawn_demo")

    def test_write_prompt_contains_task_contract_and_context(self) -> None:
        system, user = prompt_builder.build_write_prompt(self.project, "chapter_01", 2000)
        self.assertIn("continuation_current_chapter", system)
        self.assertIn("<NOVEL_TEXT>", user)
        self.assertIn("【主线大纲】", user)
        self.assertIn("【后续剧情规划】", user)
        self.assertIn("NOVALIST_TASK_DONE", user)

    def test_expansion_prompt_uses_outline_without_current_body(self) -> None:
        system, user = prompt_builder.build_expansion_prompt(self.project, "chapter_01", 2000)
        self.assertIn("chapter_expansion", system)
        self.assertIn("【本章规划与用户剧情简写】", user)
        self.assertIn("<NOVEL_TEXT>", user)
        self.assertIn("扩写任务已完成", user)
        self.assertNotIn("【当前章节已有正文】", user)

    def test_expansion_retry_prompt_explicitly_corrects_agent_preamble(self) -> None:
        system, user = prompt_builder.build_expansion_retry_prompt(self.project, "chapter_01", 2000)
        self.assertIn("章节扩写任务纠偏重试", system)
        self.assertIn("不要回答“我已就绪”", user)

    def test_write_retry_prompt_explicitly_corrects_agent_preamble(self) -> None:
        system, user = prompt_builder.build_write_retry_prompt(self.project, "chapter_01", 2000)
        self.assertIn("纠偏重试", system)
        self.assertIn("不要回答“我已就绪”", user)
        self.assertIn("<NOVEL_TEXT>", user)

    def test_check_prompt_requires_structured_report(self) -> None:
        system, user = prompt_builder.build_check_prompt(self.project, "chapter_01")
        self.assertIn("consistency_check", system)
        self.assertIn('"type": "consistency_report"', user)
        self.assertIn('"completion_message"', user)

    def test_memory_prompts_require_structured_results(self) -> None:
        summary_system, summary_user = prompt_builder.build_summary_prompt(
            self.project, "chapter_01"
        )
        state_system, state_user = prompt_builder.build_state_update_prompt(
            self.project, "chapter_01"
        )
        self.assertIn('"type": "chapter_summary"', summary_user)
        self.assertIn('"type": "story_state_update"', state_user)
        self.assertIn("chapter_summary", summary_system)
        self.assertIn("story_state_update", state_system)
