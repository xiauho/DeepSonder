import tempfile
from pathlib import Path
from unittest import TestCase

from core.context_profiles import (
    CONSISTENCY_CONTEXT_PROFILE,
    EXPANSION_CONTEXT_PROFILE,
)
from core.project import NovelProject
from core.prompt_builder import build_consistency_repair_prompt


class ContextProfileTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = NovelProject.create(Path(self._tmp.name) / "proj", "测试")
        self.project.save_chapter(
            "chapter_01",
            title="第一章",
            outline="周弦进入旧站。",
            plot_brief="寻找钥匙。",
            content="周弦在旧站找到钥匙。",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_profiles_declare_distinct_task_source_scopes(self) -> None:
        self.assertEqual(EXPANSION_CONTEXT_PROFILE.character_scope, "planning")
        self.assertTrue(CONSISTENCY_CONTEXT_PROFILE.include_world)

    def test_prompt_sections_follow_the_task_profile(self) -> None:
        repair = build_consistency_repair_prompt(
            self.project,
            "chapter_01",
            {
                "issue_id": "issue_1",
                "chapter_quote": "周弦在旧站找到钥匙。",
                "description": "测试问题",
            },
        )

        self.assertNotIn("content", {item.key for item in repair.report.sections})
        self.assertNotIn("summaries", {item.key for item in repair.report.sections})
        self.assertNotIn("main_arc", {item.key for item in repair.report.sections})
