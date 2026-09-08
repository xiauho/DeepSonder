import unittest

from ui.editor import (
    LARGE_DOCUMENT_CHARS,
    MAX_FIND_HIGHLIGHTS,
    calculate_editor_stats,
    find_highlight_config,
)


class EditorPerformanceTests(unittest.TestCase):
    def test_stats_label_uses_the_ai_protocol_counting_rules(self) -> None:
        self.assertEqual(
            calculate_editor_stats("你好 world\n\n第二段"),
            "10 字 · 2 段 · 约 1 分钟阅读",
        )
        self.assertEqual(calculate_editor_stats(""), "0 字 · 0 段")

    def test_find_highlights_are_limited_for_many_matches(self) -> None:
        limit, suffix = find_highlight_config(1000, MAX_FIND_HIGHLIGHTS + 1)
        self.assertEqual(limit, MAX_FIND_HIGHLIGHTS)
        self.assertIn("仅显示前", suffix)

        limit, suffix = find_highlight_config(1000, 1001)
        self.assertEqual(limit, 0)
        self.assertIn("仅显示数量", suffix)

    def test_large_documents_skip_find_highlights(self) -> None:
        limit, suffix = find_highlight_config(LARGE_DOCUMENT_CHARS, 1)
        self.assertEqual(limit, 0)
        self.assertIn("大文档", suffix)


if __name__ == "__main__":
    unittest.main()
