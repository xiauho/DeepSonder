import json
import tempfile
from pathlib import Path
from unittest import TestCase

from core.config import normalize_config
from core.context_budget import build_task_context
from core.context_profiles import EXPANSION_CONTEXT_PROFILE
from core.context_selection import relevance_score, select_ranked_documents
from core.project import NovelProject
from core.prompt_builder import build_consistency_repair_prompt, build_expansion_prompt
from ui.inspector import render_context_reports


class ContextSelectionTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = NovelProject.create(Path(self._tmp.name) / "proj", "筛选测试")
        self.project.save_chapter(
            "chapter_01",
            title="第一章",
            outline="周弦抵达星港。",
            plot_brief="调查港区规则。",
            content="周弦在星港寻找线索。",
        )
        for index in range(8):
            self.project.write_file(
                self.project.canon_dir / "world" / f"背景_{index}.md",
                f"# 普通背景 {index}\n\n" + f"无关资料{index}。" * 300,
            )
        self.relevant_path = self.project.canon_dir / "world" / "星港.md"
        self.project.write_file(
            self.relevant_path,
            "# 星港规则\n\n星港相关性标记：夜间禁止开启外环舱门。" + "规则。" * 300,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_explicit_title_heading_and_metadata_matches_are_explainable(self) -> None:
        self.assertEqual(
            relevance_score(Path("星港.md"), "# 规则", "主角抵达星港")[1],
            "title_match",
        )
        self.assertEqual(
            relevance_score(Path("地点.md"), "# 星港规则", "调查星港规则")[1],
            "heading_match",
        )
        self.assertEqual(
            relevance_score(Path("地点.md"), "标签：星港", "抵达星港")[1],
            "metadata_match",
        )

    def test_safe_mode_places_relevant_world_before_unmatched_background(self) -> None:
        context = build_task_context(
            self.project,
            "chapter_01",
            profile=EXPANSION_CONTEXT_PROFILE,
            selection_mode="safe",
        )
        world_stat = next(
            item for item in context.related.selection if item.category == "world"
        )

        self.assertTrue(context.related.world.startswith("### 星港"))
        self.assertIn("星港相关性标记", context.related.world)
        self.assertEqual(world_stat.candidates, 9)
        self.assertEqual(world_stat.matched, 1)
        self.assertLess(world_stat.included, world_stat.candidates)
        self.assertGreater(world_stat.excluded_unmatched, 0)

    def test_selection_is_deterministic_and_prompt_keeps_the_relevant_entry(self) -> None:
        first = build_expansion_prompt(self.project, "chapter_01", prompt_budget=24_000)
        second = build_expansion_prompt(self.project, "chapter_01", prompt_budget=24_000)

        self.assertEqual(first.user_prompt, second.user_prompt)
        self.assertIn("星港相关性标记", first.user_prompt)
        world = next(item for item in first.report.selections if item.category == "world")
        self.assertEqual(world.matched, 1)
        self.assertGreaterEqual(world.prompt_included, 1)
        self.assertIn("相关资料筛选", render_context_reports([first.report]))

    def test_repair_uses_issue_text_instead_of_unrelated_full_chapter_for_ranking(self) -> None:
        other = self.project.canon_dir / "world" / "旧城.md"
        self.project.write_file(other, "# 旧城规则\n\n旧城修复标记：城门只能向内开启。")
        self.project.write_file(
            self.project.canon_dir / "characters" / "周弦.md",
            "# 周弦\n\n当前章节角色。",
        )
        self.project.write_file(
            self.project.canon_dir / "characters" / "顾衍.md",
            "# 顾衍\n\n问题直接相关角色标记。",
        )
        issue = {
            "issue_id": "issue_1",
            "chapter_quote": "顾衍在门前停下。",
            "description": "顾衍打开旧城城门的方向与旧城规则冲突",
        }
        prompt = build_consistency_repair_prompt(
            self.project,
            "chapter_01",
            issue,
            prompt_budget=24_000,
        )

        self.assertIn("旧城修复标记", prompt.user_prompt)
        self.assertIn("问题直接相关角色标记", prompt.user_prompt)
        self.assertNotIn("当前章节角色。", prompt.user_prompt)

    def test_legacy_mode_keeps_all_documents_as_a_compatible_fallback(self) -> None:
        context = build_task_context(
            self.project,
            "chapter_01",
            profile=EXPANSION_CONTEXT_PROFILE,
            selection_mode="legacy_all",
        )
        world_stat = next(
            item for item in context.related.selection if item.category == "world"
        )

        self.assertEqual(world_stat.included, world_stat.candidates)
        self.assertEqual(world_stat.excluded_unmatched, 0)
        self.assertNotIn("### 星港\n", context.related.world)

    def test_core_and_manually_selected_systems_are_required_and_reported(self) -> None:
        core_system = self.project.canon_dir / "power" / "星术.md"
        selected_system = self.project.canon_dir / "power" / "门禁.md"
        self.project.write_file(core_system, "# 星术\n\n核心体系标记：星光不可逆转时间。")
        self.project.write_file(selected_system, "# 门禁\n\n手选体系标记：外环门需要双重授权。")
        from core.project_data import ProjectDataStore

        ProjectDataStore(self.project).set_system_importance(core_system, "core")
        prompt = build_expansion_prompt(
            self.project,
            "chapter_01",
            selected_power=[str(selected_system)],
            prompt_budget=24_000,
        )

        self.assertIn("核心体系标记", prompt.user_prompt)
        self.assertIn("手选体系标记", prompt.user_prompt)
        required = {
            item.category: item
            for item in prompt.report.selections
            if item.required
        }
        self.assertEqual(required["core_systems"].prompt_included, 1)
        self.assertEqual(required["selected_power"].prompt_included, 1)
        serialized = json.dumps(prompt.report.to_dict(), ensure_ascii=False)
        self.assertNotIn("核心体系标记", serialized)
        self.assertNotIn("手选体系标记", serialized)
        self.assertNotIn("星术", serialized)
        self.assertNotIn("门禁", serialized)

    def test_prompt_build_stops_if_multiple_required_systems_cannot_fit(self) -> None:
        from core.project_data import ProjectDataStore

        store = ProjectDataStore(self.project)
        for index in range(4):
            path = self.project.canon_dir / "power" / f"核心体系_{index}.md"
            self.project.write_file(
                path,
                f"# 核心体系 {index}\n\n" + "不可违背的核心规则。" * 300,
            )
            store.set_system_importance(path, "core")

        with self.assertRaisesRegex(RuntimeError, "核心体系"):
            build_expansion_prompt(
                self.project,
                "chapter_01",
                prompt_budget=24_000,
            )

    def test_invalid_config_mode_falls_back_to_safe(self) -> None:
        self.assertEqual(
            normalize_config({"ai_context_selection_mode": "unknown"})[
                "ai_context_selection_mode"
            ],
            "safe",
        )


class RankedDocumentUnitTests(TestCase):
    def test_matched_entry_wins_capacity_regardless_of_filename_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = []
            for name in ("a.md", "b.md", "目标.md"):
                path = root / name
                path.write_text("# 资料\n" + name * 200, encoding="utf-8")
                paths.append(path)
            selected = select_ranked_documents(
                paths,
                query="目标",
                category="world",
                mode="safe",
                reader=lambda path: path.read_text(encoding="utf-8"),
                total_chars=400,
                entry_chars=400,
            )

        self.assertTrue(selected.text.startswith("### 目标"))
        self.assertEqual(selected.stat.included, 1)
        self.assertEqual(selected.stat.excluded_unmatched, 2)
