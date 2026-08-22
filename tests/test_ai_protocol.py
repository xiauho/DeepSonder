from unittest import TestCase

from core.ai_protocol import (
    AIProtocolError,
    format_consistency_report,
    parse_consistency_report,
    parse_continuation,
    parse_expansion,
    parse_story_state,
    parse_summary,
)


class AIProtocolTests(TestCase):
    def test_continuation_requires_explicit_text_marker(self) -> None:
        result = parse_continuation(
            "<NOVEL_TEXT>\n新的正文。\n</NOVEL_TEXT>",
            min_chars=1,
            max_chars=20,
        )
        self.assertEqual(result.text, "新的正文。")
        self.assertTrue(result.length_ok)

    def test_agent_preamble_is_not_accepted_as_continuation(self) -> None:
        with self.assertRaisesRegex(AIProtocolError, "未识别出小说正文"):
            parse_continuation("我已就绪，请问这次需要我做什么？")

    def test_plain_narrative_is_accepted_for_headless_compatibility(self) -> None:
        result = parse_continuation("他推开舱门，冷风裹着灰尘扑面而来。")
        self.assertEqual(result.text, "他推开舱门，冷风裹着灰尘扑面而来。")
        self.assertFalse(result.used_marker)

    def test_expansion_protocol_has_expansion_completion_message(self) -> None:
        result = parse_expansion(
            "<NOVEL_TEXT>完整章节正文。</NOVEL_TEXT>"
            "<NOVALIST_TASK_DONE>扩写任务已完成</NOVALIST_TASK_DONE>",
            min_chars=1,
            max_chars=20,
        )
        self.assertEqual(result.completion_message, "扩写任务已完成")

    def test_expansion_protocol_accepts_only_expansion_json_type(self) -> None:
        with self.assertRaises(AIProtocolError):
            parse_expansion('{"type":"continuation","content":"正文。"}')

    def test_consistency_report_is_validated_and_rendered(self) -> None:
        report = parse_consistency_report(
            '{"type":"consistency_report","status":"warning",'
            '"issues":[{"severity":"low","category":"timeline",'
            '"description":"时间不明","evidence":"正文证据"}]}'
        )
        rendered = format_consistency_report(report)
        self.assertIn("时间不明", rendered)

    def test_summary_and_state_require_task_types(self) -> None:
        self.assertEqual(
            parse_summary('{"type":"chapter_summary","summary":"摘要"}'),
            "摘要",
        )
        state = parse_story_state(
            '{"type":"story_state_update","current_chapter":1,'
            '"current_location":"地点","characters":{},"foreshadowing":[]}'
        )
        self.assertNotIn("type", state)
        self.assertIn("completion_message", parse_consistency_report(
            '{"type":"consistency_report","issues":[]}'
        ))
        with self.assertRaises(AIProtocolError):
            parse_summary('{"type":"continuation","summary":"错误"}')
