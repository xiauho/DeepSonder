from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from core import prompt_builder
from core.project import NovelProject


class PromptBuilderTests(TestCase):
    def setUp(self) -> None:
        self.project = NovelProject(Path(__file__).parents[1] / "projects" / "demo_novel")

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

    def test_expansion_prompt_uses_scoped_context_and_planning_characters(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "novel", "测试作品")
            project.save_chapter(
                "chapter_01",
                outline="顾衍将在城门迎接计划角色。",
                content="正文曾出现正文角色。",
                title="第一章",
            )
            characters = project.canon_dir / "characters"
            (characters / "计划角色.md").write_text("# 计划角色\n计划设定\n", encoding="utf-8")
            (characters / "正文角色.md").write_text("# 正文角色\n正文设定\n", encoding="utf-8")
            (project.canon_dir / "world" / "世界规则.md").write_text(
                "# 世界规则\n昼夜由潮汐决定。\n", encoding="utf-8"
            )
            (project.canon_dir / "power" / "能力体系.md").write_text(
                "# 能力体系\n能力使用会消耗记忆。\n", encoding="utf-8"
            )
            _system, user = prompt_builder.build_expansion_prompt(project, "chapter_01")

        self.assertIn("【本次上下文范围】", user)
        self.assertIn("最近 5 个已完成章节的摘要", user)
        self.assertIn("计划角色", user)
        self.assertNotIn("正文角色", user)
        self.assertIn("【世界观摘要】", user)
        self.assertIn("昼夜由潮汐决定", user)
        self.assertIn("【其他体系设定·低优先级背景】", user)
        self.assertIn("能力使用会消耗记忆", user)

    def test_expansion_prompt_contains_only_selected_foreshadowing(self) -> None:
        selected = [
            {
                "id": "f-selected",
                "title": "残缺古剑的来历",
                "note": "剑身上的缺口与旧宗门有关。",
                "first_seen_chapter": "chapter_01",
                "recent_seen_chapter": "chapter_03",
                "planned_resolution_chapter": "chapter_08",
                "priority": "high",
                "related_characters": ["林夜"],
                "status": "open",
            },
        ]
        _system, user = prompt_builder.build_expansion_prompt(
            self.project,
            "chapter_01",
            2000,
            selected_foreshadowing=selected,
        )

        self.assertIn("【本次重点关注的伏笔】", user)
        self.assertIn("残缺古剑的来历", user)
        self.assertIn("剑身上的缺口与旧宗门有关", user)
        self.assertIn("chapter_08", user)
        self.assertIn("伏笔 ID：f-selected", user)

        review_system, review_user = prompt_builder.build_foreshadowing_review_prompt(
            "chapter_01",
            "古剑铭文揭示了铸剑者。",
            selected,
        )
        self.assertIn("foreshadowing_review", review_system)
        self.assertIn("伏笔 ID：f-selected", review_user)
        self.assertIn('"possibly_resolved"', review_user)
        self.assertIn("古剑铭文揭示了铸剑者", review_user)

    def test_expansion_prompt_can_omit_foreshadowing(self) -> None:
        _system, user = prompt_builder.build_expansion_prompt(
            self.project,
            "chapter_01",
            2000,
            selected_foreshadowing=[],
        )
        self.assertIn("（本次未指定伏笔）", user)

    def test_prompt_history_window_is_configurable(self) -> None:
        _system, expansion_user = prompt_builder.build_expansion_prompt(
            self.project, "chapter_01", summary_count=7
        )
        _system, continuation_user = prompt_builder.build_write_prompt(
            self.project, "chapter_01", summary_count=4
        )
        self.assertIn("最多最近 7 个已完成章节的摘要", expansion_user)
        self.assertIn("最多最近 4 个已完成章节的摘要", continuation_user)

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
        self.assertIn('"kind": "hard_conflict"', user)
        self.assertIn("资料未同步", user)

    def test_repair_prompt_is_minimal_and_anchored(self) -> None:
        system, user = prompt_builder.build_consistency_repair_prompt(
            self.project,
            "chapter_01",
            {
                "issue_id": "issue_1",
                "kind": "hard_conflict",
                "chapter_quote": "林夜拔剑。",
                "description": "能力设定冲突",
                "evidence": "正文证据",
            },
        )
        self.assertIn("consistency_repair", system)
        self.assertIn("只允许修改章节正文中的一个连续文本区间", user)
        self.assertIn("林夜拔剑。", user)
        self.assertIn('"expected_original"', user)

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

    def test_state_update_prompt_carries_real_chapter_number(self) -> None:
        _system, user = prompt_builder.build_state_update_prompt(self.project, "chapter_07")
        self.assertIn('"current_chapter": 7', user)
        self.assertIn("current_chapter 必须填写为 7", user)
        self.assertNotIn('"current_chapter": 1,', user)

    def test_state_update_prompt_falls_back_to_old_state_number(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "novel", "测试作品")
            _system, user = prompt_builder.build_state_update_prompt(project, "序章")
        self.assertIn('"current_chapter": 1', user)
