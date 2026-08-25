import unittest

from ui.ai_task_view_controller import (
    MAX_OUTPUT_ENTRY_CHARS,
    limit_output_entry,
)


class AIOutputFeedbackTests(unittest.TestCase):
    def test_short_output_is_preserved(self) -> None:
        self.assertEqual(limit_output_entry("任务已完成"), "任务已完成")

    def test_long_output_keeps_both_ends_and_stays_bounded(self) -> None:
        value = "开头" + ("中间" * MAX_OUTPUT_ENTRY_CHARS) + "结尾"
        result = limit_output_entry(value)

        self.assertLessEqual(len(result), MAX_OUTPUT_ENTRY_CHARS)
        self.assertTrue(result.startswith("开头"))
        self.assertTrue(result.endswith("结尾"))
        self.assertIn("已折叠中间内容", result)

    def test_empty_output_is_normalized(self) -> None:
        self.assertEqual(limit_output_entry(None), "")


if __name__ == "__main__":
    unittest.main()
