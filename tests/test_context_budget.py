import json
import tempfile
from pathlib import Path
from unittest import TestCase

from core.context_budget import (
    DROPPED_PLACEHOLDER,
    HEAD_MARK,
    Section,
    TAIL_MARK,
    allocate,
    compact_story_state,
    prior_chapter_summaries,
)
from core.project import NovelProject
from core.prompt_builder import build_check_prompt, build_state_update_prompt, build_write_prompt


def section(key, text, cap=1000, keep="head", priority=5):
    return Section(key, text, cap, keep, priority)


class AllocateTests(TestCase):
    def test_keeps_everything_when_under_budget(self) -> None:
        result = allocate([section("a", "甲" * 100), section("b", "乙" * 100)], 1000)
        self.assertEqual(result["a"], "甲" * 100)
        self.assertEqual(result["b"], "乙" * 100)

    def test_applies_per_section_cap_with_head_marker(self) -> None:
        result = allocate([section("a", "甲" * 500, cap=100)], 10000)
        self.assertTrue(result["a"].startswith("甲"))
        self.assertTrue(result["a"].endswith(HEAD_MARK.strip()))
        self.assertLessEqual(len(result["a"]), 100)

    def test_tail_trim_keeps_the_ending(self) -> None:
        text = "头" * 400 + "尾" * 400
        result = allocate([section("a", text, cap=200, keep="tail")], 10000)
        self.assertIn(TAIL_MARK.strip(), result["a"])
        self.assertTrue(result["a"].endswith("尾" * 100))

    def test_budget_pressure_drops_lowest_priority_first(self) -> None:
        sections = [
            section("low", "丙" * 300, priority=9),
            section("high", "甲" * 300, priority=1),
            section("mid", "乙" * 300, priority=5),
        ]
        result = allocate(sections, 700)
        self.assertEqual(result["high"], "甲" * 300)
        self.assertTrue(result["mid"].startswith("乙"))
        self.assertEqual(result["low"], DROPPED_PLACEHOLDER)

    def test_trim_result_never_exceeds_remaining_budget(self) -> None:
        sections = [section("a", "甲" * 300, priority=1), section("b", "乙" * 300, priority=2)]
        result = allocate(sections, 400)
        self.assertEqual(len(result["a"]), 300)
        self.assertLessEqual(len(result["b"]), 100)
        self.assertLessEqual(len(result["a"]) + len(result["b"]), 400)


class CompactStoryStateTests(TestCase):
    def test_caps_long_values_and_keeps_structure(self) -> None:
        state = {
            "current_chapter": 7,
            "characters": {"周弦": {"state": "长" * 500, "items": ["物"] * 30}},
            "foreshadowing": ["钩子" + str(i) for i in range(40)],
        }
        compacted = compact_story_state(state, value_cap=200, hook_cap=50, max_list_items=12, max_hooks=24)
        self.assertEqual(compacted["current_chapter"], 7)
        self.assertLessEqual(len(compacted["characters"]["周弦"]["state"]), 201)
        self.assertTrue(compacted["characters"]["周弦"]["state"].endswith("…"))
        self.assertLessEqual(len(compacted["characters"]["周弦"]["items"]), 12)
        self.assertLessEqual(len(compacted["foreshadowing"]), 24)
        json.dumps(compacted)  # stays valid JSON

    def test_short_state_passes_through(self) -> None:
        state = {"current_chapter": 3, "characters": {"A": {"state": "受伤"}}, "foreshadowing": ["钥匙"]}
        self.assertEqual(compact_story_state(state), state)


class PriorChapterSummariesTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = NovelProject.create(Path(self._tmp.name) / "proj", "测试")
        summaries = {
            "chapter_01": "第一章摘要",
            "chapter_02": "第二章摘要",
            "chapter_10": "第十章摘要",
            "chapter_11": "第十一章摘要",
            "序章": "序章摘要",
        }
        self.project.save_chapter_summaries(summaries)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_future_chapters_are_excluded(self) -> None:
        text = prior_chapter_summaries(self.project, "chapter_10")
        self.assertIn("第二章摘要", text)
        self.assertIn("序章摘要", text)
        self.assertNotIn("第十章摘要", text)  # own chapter
        self.assertNotIn("第十一章摘要", text)  # future chapter

    def test_natural_order_places_2_before_10(self) -> None:
        text = prior_chapter_summaries(self.project, "chapter_11")
        self.assertLess(text.index("第二章摘要"), text.index("第十章摘要"))
        self.assertIn("序章摘要", text)


class BudgetedPromptTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "proj"
        self.project = NovelProject.create(root, "测试")
        # A chapter far beyond the Windows command-line limits.
        self.project.save_chapter(
            "chapter_02",
            outline="主角抵达列城。" * 200,
            content="夜色中的列城亮起灯火。" * 2500,
            title="第二章",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_prompt_stays_under_hard_limit_and_keeps_tail(self) -> None:
        system, user = build_write_prompt(self.project, "chapter_02", 2000)
        self.assertLess(len(system) + len(user), 30000)
        self.assertIn("【当前章节已有正文】", user)
        # The sliding window marks the cut and must keep the chapter's ending,
        # because that is what the continuation has to join.
        self.assertIn("（前文过长，已截断）", user)
        self.assertIn("夜色中的列城亮起灯火。\n\n只输出以下标记之间的小说正文", user)

    def test_long_style_guide_is_bounded_and_prompt_stays_under_hard_limit(self) -> None:
        self.project.style_guide_path.write_text(
            "# 写作风格指南\n\n" + "冷峻短句，保持紧张感。" * 1000 + "不应保留的尾部标记",
            encoding="utf-8",
        )

        system, user = build_write_prompt(self.project, "chapter_02", 2000)

        self.assertLess(len(system) + len(user), 30000)
        self.assertIn("【写作风格约束】", user)
        self.assertIn("冷峻短句", user)
        self.assertNotIn("不应保留的尾部标记", user)

    def test_check_prompt_stays_under_hard_limit(self) -> None:
        system, user = build_check_prompt(self.project, "chapter_02")
        self.assertLess(len(system) + len(user), 30000)

    def test_state_update_prompt_stays_under_hard_limit(self) -> None:
        system, user = build_state_update_prompt(self.project, "chapter_02")
        self.assertLess(len(system) + len(user), 30000)
        self.assertIn('"current_chapter": 2', user)
