from unittest import TestCase

from core.text_anchor import build_text_anchor, resolve_text_anchor


class TextAnchorTests(TestCase):
    def test_chapter_body_is_preferred_when_outline_repeats_quote(self) -> None:
        text = "## 大纲\n林夜拔剑。\n\n## 正文\n林夜拔剑。\n"
        anchor = build_text_anchor(text, "林夜拔剑。")
        self.assertIsNotNone(anchor)
        self.assertGreater(anchor["start"], text.index("## 正文"))

    def test_unique_quote_resolves_after_prefix_edit(self) -> None:
        text = "前文发生变化。\n苏婉扶住林夜，指尖发颤。\n后文。"
        anchor = build_text_anchor(text, "苏婉扶住林夜，指尖发颤。")
        self.assertIsNotNone(anchor)
        resolution = resolve_text_anchor("新增前缀。\n" + text, anchor)
        self.assertEqual(resolution.status, "relocated")
        self.assertEqual(resolution.end - resolution.start, len("苏婉扶住林夜，指尖发颤。"))

    def test_duplicate_quote_is_ambiguous_without_position(self) -> None:
        text = "同一句。\n中间。\n同一句。"
        self.assertIsNone(build_text_anchor(text, "同一句。"))
        self.assertEqual(resolve_text_anchor(text, "同一句。").status, "ambiguous")

    def test_whitespace_change_can_be_relocated(self) -> None:
        anchor = {"quote": "甲\n乙", "start": 0, "end": 3}
        resolution = resolve_text_anchor("甲 乙", anchor)
        self.assertEqual(resolution.status, "relocated")
