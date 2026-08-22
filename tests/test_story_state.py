from unittest import TestCase

from core.memory import merge_story_state
from core.project import NovelProject


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
