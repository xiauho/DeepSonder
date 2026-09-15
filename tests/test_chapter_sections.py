from unittest import TestCase

from core.chapter_sections import chapter_body_bounds, chapter_body_text


class ChapterSectionTests(TestCase):
    def test_body_bounds_stop_before_custom_sections(self) -> None:
        raw = (
            "# 第一章\n\n## 大纲\n规划\n\n## 正文\n原正文。\n\n"
            "## 作者备注\n保留备注。\n"
        )
        start, end = chapter_body_bounds(raw) or (0, 0)
        self.assertEqual(raw[start:end].strip(), "原正文。")
        self.assertEqual(chapter_body_text(raw), "原正文。")
        self.assertLess(end, raw.index("## 作者备注"))

    def test_unsectioned_import_is_treated_as_body_without_title(self) -> None:
        self.assertEqual(chapter_body_text("# 标题\n\n直接正文。"), "直接正文。")
