from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from core.ai_result_service import AIResultService
from core.foreshadowing import ForeshadowingStore
from core.project import NovelProject


class AIResultServiceTests(TestCase):
    def test_expansion_and_consistency_parsing_are_centralized(self) -> None:
        expansion = AIResultService.parse_expansion(
            "<NOVEL_TEXT>完整正文。</NOVEL_TEXT>",
            target_chars=300,
        )
        self.assertEqual(expansion.text, "完整正文。")

        report, rendered = AIResultService.parse_consistency(
            '{"type":"consistency_report","issues":[]}'
        )
        self.assertEqual(report["type"], "consistency_report")
        self.assertIn("未发现", rendered)

    def test_memory_commit_validates_and_pins_processed_chapter(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            draft = AIResultService.prepare_memory(
                "章节摘要",
                {"current_chapter": 1, "characters": {}, "foreshadowing": []},
            )
            result = AIResultService.commit_memory(project, "chapter_07", draft)

            self.assertTrue(result.chapter_was_corrected)
            self.assertEqual(result.expected_chapter, 7)
            self.assertEqual(project.load_story_state()["current_chapter"], 7)
            self.assertEqual(project.load_chapter_summaries()["chapter_07"], "章节摘要")

    def test_memory_prepare_rejects_empty_summary_or_invalid_state(self) -> None:
        with self.assertRaises(ValueError):
            AIResultService.prepare_memory("", {})
        with self.assertRaises(ValueError):
            AIResultService.prepare_memory("摘要", None)

    def test_memory_commit_does_not_overwrite_structured_foreshadowing(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            state = project.load_story_state()
            state["foreshadowing"] = ["旧版伏笔"]
            project.save_story_state(state)
            note = ForeshadowingStore(project).create_note("结构化伏笔")

            draft = AIResultService.prepare_memory(
                "章节摘要",
                {
                    "current_chapter": 1,
                    "current_location": "新地点",
                    "characters": {},
                    "foreshadowing": [],
                },
            )
            AIResultService.commit_memory(project, "chapter_01", draft)

            self.assertEqual(project.load_story_state()["foreshadowing"], ["旧版伏笔"])
            self.assertEqual(ForeshadowingStore(project).get_note(note["id"])["title"], "结构化伏笔")
