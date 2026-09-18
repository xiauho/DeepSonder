from unittest import TestCase

from ui.pages import DashboardPage


class DashboardPageHelperTests(TestCase):
    def test_chapter_preview_compacts_whitespace_and_limits_length(self) -> None:
        preview = DashboardPage._chapter_preview("第一段。\n\n第二段。", limit=5)
        self.assertEqual(preview, "“第一段。…”")
