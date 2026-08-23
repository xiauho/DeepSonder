import tempfile
from pathlib import Path
from unittest import TestCase

from core.memory import apply_state_update, merge_story_state
from core.project import NovelProject, chapter_number_from_id


class StoryStateTests(TestCase):
    def test_generated_empty_hooks_clear_resolved_hooks(self) -> None:
        merged = merge_story_state(
            {"foreshadowing": ["old hook"], "characters": {"A": {"state": "旧"}}},
            {"foreshadowing": [], "characters": {"A": {"state": "新"}}},
        )
        self.assertEqual(merged["foreshadowing"], [])
        self.assertEqual(merged["characters"]["A"]["state"], "新")

    def test_unsectioned_markdown_is_available_as_content(self) -> None:
        outline, content = NovelProject._split_chapter("# 标题\n\n一段没有分区的正文")
        self.assertEqual(outline, "")
        self.assertEqual(content, "一段没有分区的正文")

    def test_chapter_sections_are_parsed_and_serialized_without_loss(self) -> None:
        raw = (
            "# 标题\n\n"
            "## 大纲\n目标\n\n"
            "## 剧情简写\n主角先调查\n\n"
            "## 正文\n正文内容\n\n"
            "## 备注\n保留这段\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            path = project.chapters_dir / "chapter_02.md"
            path.write_text(raw, encoding="utf-8")
            chapter = project.load_chapter("chapter_02")
            self.assertEqual(chapter.outline, "目标")
            self.assertEqual(chapter.plot_brief, "主角先调查")
            self.assertEqual(chapter.content, "正文内容")
            self.assertEqual(chapter.extra_sections, [("备注", "保留这段")])

            project.save_chapter(
                "chapter_02",
                chapter.outline,
                chapter.content,
                title=chapter.title,
                plot_brief=chapter.plot_brief,
                extra_sections=chapter.extra_sections,
            )
            saved = project.load_chapter("chapter_02")
            self.assertEqual(saved.plot_brief, "主角先调查")
            self.assertEqual(saved.extra_sections, [("备注", "保留这段")])

    def test_chapter_number_only_trusts_canonical_ids(self) -> None:
        self.assertEqual(chapter_number_from_id("chapter_07"), 7)
        self.assertEqual(chapter_number_from_id("Chapter-3"), 3)
        self.assertIsNone(chapter_number_from_id("序章"))
        self.assertIsNone(chapter_number_from_id("final_v2"))

    def test_apply_state_update_pins_current_chapter_to_processed_chapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            # A model echoing the template's example value must not reset progress.
            merged = apply_state_update(
                project,
                {"current_chapter": 1, "characters": {}, "foreshadowing": []},
                "chapter_07",
            )
            self.assertEqual(merged["current_chapter"], 7)
            self.assertEqual(project.load_story_state()["current_chapter"], 7)

    def test_apply_state_update_keeps_model_value_for_custom_chapter_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            merged = apply_state_update(
                project,
                {"current_chapter": 5, "characters": {}, "foreshadowing": []},
                "序章",
            )
            self.assertEqual(merged["current_chapter"], 5)
