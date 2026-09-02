import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from core.context_budget import build_task_context
from core.context_profiles import (
    CONSISTENCY_CONTEXT_PROFILE,
    EXPANSION_CONTEXT_PROFILE,
    STATE_UPDATE_CONTEXT_PROFILE,
)
from core.project import NovelProject
from core.prompt_builder import (
    build_consistency_repair_prompt,
    build_state_update_prompt,
    build_summary_prompt,
)


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
        self.assertFalse(STATE_UPDATE_CONTEXT_PROFILE.include_world)
        self.assertFalse(STATE_UPDATE_CONTEXT_PROFILE.include_power)
        self.assertFalse(STATE_UPDATE_CONTEXT_PROFILE.include_timeline)
        self.assertFalse(STATE_UPDATE_CONTEXT_PROFILE.include_summaries)

    def test_state_update_profile_avoids_unneeded_canon_and_project_text(self) -> None:
        with patch.object(
            self.project,
            "find_related_canon",
            wraps=self.project.find_related_canon,
        ) as find_related:
            context = build_task_context(
                self.project,
                "chapter_01",
                profile=STATE_UPDATE_CONTEXT_PROFILE,
            )

        options = find_related.call_args.kwargs
        self.assertEqual(options["character_query"], "")
        self.assertFalse(options["include_world"])
        self.assertFalse(options["include_power"])
        self.assertFalse(options["include_timeline"])
        self.assertEqual(context.chapter_summaries, {})
        self.assertEqual(context.main_arc, "")
        self.assertEqual(context.future_plan, "")
        self.assertEqual(context.style_guide, "")

    def test_prompt_sections_follow_the_task_profile(self) -> None:
        state = build_state_update_prompt(self.project, "chapter_01")
        summary = build_summary_prompt(self.project, "chapter_01")
        repair = build_consistency_repair_prompt(
            self.project,
            "chapter_01",
            {
                "issue_id": "issue_1",
                "chapter_quote": "周弦在旧站找到钥匙。",
                "description": "测试问题",
            },
        )

        self.assertEqual(
            {item.key for item in state.report.sections},
            {"outline", "plot_brief", "content", "state"},
        )
        self.assertNotIn("summaries", {item.key for item in summary.report.sections})
        self.assertNotIn("content", {item.key for item in repair.report.sections})
        self.assertNotIn("summaries", {item.key for item in repair.report.sections})
        self.assertNotIn("main_arc", {item.key for item in repair.report.sections})
