from unittest import TestCase

from core.ai_result_service import AIResultService


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
