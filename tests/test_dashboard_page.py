from unittest import TestCase

from ui.pages import DashboardPage


class DashboardPageHelperTests(TestCase):
    def test_chapter_preview_compacts_whitespace_and_limits_length(self) -> None:
        preview = DashboardPage._chapter_preview("第一段。\n\n第二段。", limit=5)
        self.assertEqual(preview, "“第一段。…”")

    def test_meaningful_markdown_ignores_empty_template_headings(self) -> None:
        self.assertFalse(
            DashboardPage._has_meaningful_markdown("# 主线大纲\n\n- 本书主线：\n")
        )
        self.assertTrue(
            DashboardPage._has_meaningful_markdown(
                "# 主线大纲\n\n- 主线：林夜查明父母死因。\n"
            )
        )
