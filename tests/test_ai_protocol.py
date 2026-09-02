from unittest import TestCase

from core.ai_protocol import (
    AIProtocolError,
    consistency_issue_counts,
    format_consistency_report,
    parse_consistency_report,
    parse_consistency_repair,
    parse_continuation,
    parse_expansion,
    parse_story_state,
    parse_summary,
)


class AIProtocolTests(TestCase):
    def test_consistency_repair_requires_exact_anchor(self) -> None:
        result = parse_consistency_repair(
            {
                "type": "consistency_repair",
                "chapter_id": "chapter_01",
                "issue_id": "issue_1",
                "status": "ready",
                "target": "chapter",
                "expected_original": "原句",
                "replacement": "修复句",
                "explanation": "使事实与正文保持一致。",
                "preserved_facts": ["人物身份"],
            },
            expected_chapter_id="chapter_01",
            expected_issue_id="issue_1",
            expected_original="原句",
        )
        self.assertEqual(result.replacement, "修复句")
        with self.assertRaisesRegex(AIProtocolError, "原文锚点"):
            parse_consistency_repair(
                {
                    "type": "consistency_repair",
                    "chapter_id": "chapter_01",
                    "issue_id": "issue_1",
                    "status": "ready",
                    "target": "chapter",
                    "expected_original": "旧句",
                    "replacement": "修复句",
                    "explanation": "说明",
                    "preserved_facts": [],
                },
                expected_chapter_id="chapter_01",
                expected_issue_id="issue_1",
                expected_original="原句",
            )

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

    def test_expansion_parses_scoped_foreshadowing_feedback(self) -> None:
        result = parse_expansion(
            '<NOVEL_TEXT>古剑铭文揭示了铸剑者。</NOVEL_TEXT>'
            '<FORESHADOWING_FEEDBACK>{'
            '"chapter_id":"chapter_08","possibly_resolved":['
            '{"foreshadowing_id":"f-selected","evidence":"铭文揭示了铸剑者",'
            '"reason":"古剑来源得到明确回答"},'
            '{"foreshadowing_id":"f-unselected","evidence":"其他证据",'
            '"reason":"不应被采用"}]}</FORESHADOWING_FEEDBACK>',
            min_chars=1,
            expected_chapter_id="chapter_08",
            allowed_foreshadowing_ids={"f-selected"},
        )

        self.assertEqual(
            [item.foreshadowing_id for item in result.foreshadowing_feedback],
            ["f-selected"],
        )
        self.assertIn("安全忽略", result.feedback_warning)

    def test_invalid_foreshadowing_feedback_does_not_invalidate_prose(self) -> None:
        result = parse_expansion(
            "<NOVEL_TEXT>完整正文。</NOVEL_TEXT>"
            "<FORESHADOWING_FEEDBACK>{bad json}</FORESHADOWING_FEEDBACK>",
            min_chars=1,
        )

        self.assertEqual(result.text, "完整正文。")
        self.assertEqual(result.foreshadowing_feedback, ())
        self.assertIn("已忽略", result.feedback_warning)

    def test_malformed_markers_are_repaired_without_leaking_into_prose(self) -> None:
        result = parse_expansion(
            "<NOVEL_TEXT>\n青云宗的夜，被喊杀声撕碎了。\n"
            "这个消息必须尽快传回教中。\n</NOVALIST_TASK_DONE>",
            min_chars=1,
        )

        self.assertEqual(
            result.text,
            "青云宗的夜，被喊杀声撕碎了。\n这个消息必须尽快传回教中。",
        )
        self.assertNotIn("NOVEL_TEXT", result.text)
        self.assertNotIn("NOVALIST_TASK_DONE", result.text)
        self.assertIn("标记不完整", result.protocol_warning)

    def test_protocol_artifacts_without_repairable_body_are_not_plain_prose(self) -> None:
        with self.assertRaisesRegex(AIProtocolError, "未识别出小说正文"):
            parse_expansion("正文之前出现错误结束标记。</NOVALIST_TASK_DONE>")

    def test_consistency_report_is_validated_and_rendered(self) -> None:
        report = parse_consistency_report(
            '{"type":"consistency_report","status":"warning",'
            '"issues":[{"severity":"low","category":"relationship",'
            '"kind":"sync_gap","description":"关系资料未更新","evidence":"正文证据"}]}'
        )
        rendered = format_consistency_report(report)
        self.assertIn("提示", rendered)
        self.assertIn("人物关系", rendered)
        self.assertIn("资料未同步", rendered)
        self.assertEqual(consistency_issue_counts(report), {"high": 0, "medium": 0, "low": 1})

    def test_consistency_report_normalizes_legacy_values_and_checks_chapter(self) -> None:
        report = parse_consistency_report(
            {
                "type": "consistency_report",
                "chapter_id": "chapter_01",
                "issues": [
                    {
                        "severity": "warning",
                        "category": "relationships",
                        "description": "关系资料未更新",
                        "evidence": "正文证据",
                    }
                ],
            },
            expected_chapter_id="chapter_01",
        )
        issue = report["issues"][0]
        self.assertEqual(issue["severity"], "medium")
        self.assertEqual(issue["category"], "relationship")
        self.assertEqual(issue["kind"], "continuity_risk")

        with self.assertRaisesRegex(AIProtocolError, "章节"):
            parse_consistency_report(
                {
                    "type": "consistency_report",
                    "chapter_id": "chapter_02",
                    "issues": [],
                },
                expected_chapter_id="chapter_01",
            )

    def test_consistency_report_safely_normalizes_generic_data_target(self) -> None:
        report = parse_consistency_report(
            {
                "type": "consistency_report",
                "issues": [
                    {
                        "severity": "low",
                        "category": "state",
                        "kind": "sync_gap",
                        "description": "故事状态资料未更新",
                        "evidence": "正文证据",
                        "recommended_target": "data",
                        "repairability": "automatic",
                    }
                ],
            }
        )

        issue = report["issues"][0]
        self.assertEqual(issue["recommended_target"], "manual")
        self.assertEqual(issue["repairability"], "manual")

    def test_consistency_report_prevents_non_chapter_automatic_repair(self) -> None:
        report = parse_consistency_report(
            {
                "type": "consistency_report",
                "issues": [
                    {
                        "severity": "medium",
                        "category": "character",
                        "kind": "hard_conflict",
                        "description": "角色卡需要人工确认",
                        "evidence": "正文证据",
                        "recommended_target": "character_card",
                        "repairability": "automatic",
                    }
                ],
            }
        )

        self.assertEqual(report["issues"][0]["repairability"], "manual")

    def test_consistency_report_rejects_unknown_enum_values(self) -> None:
        with self.assertRaisesRegex(AIProtocolError, "无效 category"):
            parse_consistency_report(
                {
                    "type": "consistency_report",
                    "issues": [
                        {
                            "severity": "low",
                            "category": "made_up",
                            "description": "问题",
                            "evidence": "证据",
                        }
                    ],
                }
            )

    def test_consistency_report_rejects_duplicate_issue_ids(self) -> None:
        with self.assertRaisesRegex(AIProtocolError, "重复 issue_id"):
            parse_consistency_report(
                {
                    "type": "consistency_report",
                    "issues": [
                        {
                            "issue_id": "same",
                            "severity": "low",
                            "category": "state",
                            "description": "问题一",
                            "evidence": "证据一",
                        },
                        {
                            "issue_id": "same",
                            "severity": "low",
                            "category": "state",
                            "description": "问题二",
                            "evidence": "证据二",
                        },
                    ],
                }
            )

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
