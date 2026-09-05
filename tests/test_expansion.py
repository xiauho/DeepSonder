from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from core import expansion
from core.project import NovelProject


class FakeDSH:
    def __init__(self, outputs, json_outputs=None):
        self.outputs = list(outputs)
        self.calls: list[dict] = []
        self.json_outputs = list(json_outputs or [])
        self.json_calls: list[dict] = []

    def prompt_build_budget(self):
        return 24_000

    def generate(
        self,
        system_prompt,
        user_prompt,
        session_id=None,
        *,
        timeout_override=None,
        context_report=None,
    ):
        self.calls.append(
            {
                "system": system_prompt,
                "user": user_prompt,
                "timeout_override": timeout_override,
                "context_report": context_report,
            }
        )
        if not self.outputs:
            raise AssertionError("generate() called more times than expected")
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output

    def generate_json(self, system_prompt, user_prompt, *args, **kwargs):
        self.json_calls.append({"system": system_prompt, "user": user_prompt})
        if self.json_outputs:
            output = self.json_outputs.pop(0)
            if isinstance(output, Exception):
                raise output
            return output
        return {"chapter_id": "chapter_01", "possibly_resolved": []}


class ExpansionTests(TestCase):
    def setUp(self) -> None:
        self.project = NovelProject(Path(__file__).parents[1] / "projects" / "demo_novel")
        self.valid_output = f"<NOVEL_TEXT>\n{'字' * 300}\n</NOVEL_TEXT>"
        self.onboarding_output = "我已就绪。当前会话环境已加载，项目现有结构完好，请问这次需要我做什么？"

    def test_valid_first_output_returns_without_retry(self) -> None:
        dsh = FakeDSH([self.valid_output])
        result = expansion.run_expansion(
            self.project, "chapter_01", dsh, target_chars=300
        )
        self.assertEqual(
            expansion.ai_protocol.parse_expansion(result.raw_output, min_chars=1).text,
            "字" * 300,
        )
        self.assertIsNone(result.first_raw_output)
        self.assertEqual(result.plain_text_fallback_count, 0)
        self.assertEqual(len(dsh.calls), 1)

    def test_plain_text_fallback_is_counted_before_canonicalization(self) -> None:
        plain_text = "风吹过长街。" * 50
        dsh = FakeDSH([plain_text])

        result = expansion.run_expansion(
            self.project, "chapter_01", dsh, target_chars=300
        )

        self.assertIsNone(result.first_raw_output)
        self.assertEqual(result.plain_text_fallback_count, 1)
        self.assertIn("<NOVEL_TEXT>", result.raw_output)
        self.assertEqual(
            expansion.ai_protocol.parse_expansion(result.raw_output, min_chars=1).text,
            plain_text,
        )

    def test_retry_keeps_full_timeout_budget(self) -> None:
        dsh = FakeDSH([self.onboarding_output, self.valid_output])
        with patch(
            "core.expansion.build_ai_context",
            wraps=expansion.build_ai_context,
        ) as build_context:
            result = expansion.run_expansion(
                self.project, "chapter_01", dsh, target_chars=300
            )
        self.assertEqual(
            expansion.ai_protocol.parse_expansion(result.raw_output, min_chars=1).text,
            "字" * 300,
        )
        self.assertEqual(result.first_raw_output, self.onboarding_output)
        self.assertEqual(result.plain_text_fallback_count, 0)
        self.assertEqual(len(dsh.calls), 2)
        self.assertIn("纠偏重试", dsh.calls[1]["system"])
        # Regression: the retry regenerates the whole chapter and must not run
        # under a shortened timeout budget.
        self.assertIsNone(dsh.calls[1]["timeout_override"])
        self.assertEqual(build_context.call_count, 1)

    def test_history_window_is_forwarded_to_prompt_and_retry(self) -> None:
        dsh = FakeDSH([self.onboarding_output, self.valid_output])
        expansion.run_expansion(
            self.project,
            "chapter_01",
            dsh,
            target_chars=300,
            history_chapters=7,
        )
        self.assertIn("最多最近 7 个已完成章节的摘要", dsh.calls[0]["user"])
        self.assertIn("最多最近 7 个已完成章节的摘要", dsh.calls[1]["user"])

    def test_selected_foreshadowing_is_forwarded_to_prompt_and_retry(self) -> None:
        dsh = FakeDSH([self.onboarding_output, self.valid_output])
        selected = [
            {
                "id": "f-selected",
                "title": "残缺古剑的来历",
                "note": "与旧宗门有关。",
                "planned_resolution_chapter": "chapter_08",
                "priority": "high",
                "status": "open",
            }
        ]
        expansion.run_expansion(
            self.project,
            "chapter_01",
            dsh,
            target_chars=300,
            selected_foreshadowing=selected,
        )
        for call in dsh.calls:
            self.assertIn("残缺古剑的来历", call["user"])
            self.assertIn("本次重点关注的伏笔", call["user"])
        self.assertEqual(len(dsh.json_calls), 1)
        self.assertIn("foreshadowing_review", dsh.json_calls[0]["system"])

    def test_selected_foreshadowing_is_reviewed_after_generation(self) -> None:
        selected = [
            {
                "id": "f-selected",
                "title": "残缺古剑的来历",
                "note": "后续揭示铸剑者。",
                "status": "open",
            }
        ]
        dsh = FakeDSH(
            [self.valid_output],
            json_outputs=[
                {
                    "chapter_id": "chapter_01",
                    "possibly_resolved": [
                        {
                            "foreshadowing_id": "f-selected",
                            "evidence": "铭文揭示了铸剑者",
                            "reason": "古剑来源已经明确",
                        }
                    ],
                }
            ],
        )

        result = expansion.run_expansion(
            self.project,
            "chapter_01",
            dsh,
            target_chars=300,
            selected_foreshadowing=selected,
        )

        self.assertIsNone(result.first_raw_output)
        self.assertEqual(result.plain_text_fallback_count, 0)
        self.assertIn("<FORESHADOWING_FEEDBACK>", result.raw_output)
        parsed = expansion.ai_protocol.parse_expansion(
            result.raw_output,
            min_chars=1,
            expected_chapter_id="chapter_01",
            allowed_foreshadowing_ids={"f-selected"},
        )
        self.assertEqual(
            [item.foreshadowing_id for item in parsed.foreshadowing_feedback],
            ["f-selected"],
        )

    def test_foreshadowing_review_failure_keeps_generated_prose(self) -> None:
        selected = [{"id": "f-selected", "title": "古剑来历", "status": "open"}]
        dsh = FakeDSH([self.valid_output], json_outputs=[RuntimeError("复核失败")])

        result = expansion.run_expansion(
            self.project,
            "chapter_01",
            dsh,
            target_chars=300,
            selected_foreshadowing=selected,
        )

        parsed = expansion.ai_protocol.parse_expansion(
            result.raw_output,
            min_chars=1,
            expected_chapter_id="chapter_01",
            allowed_foreshadowing_ids={"f-selected"},
        )
        self.assertEqual(parsed.text, "字" * 300)
        self.assertIn("未返回可用", parsed.feedback_warning)

    def test_malformed_model_markers_are_cleaned_before_review_and_write(self) -> None:
        cleaned_text = "青" * 299 + "。"
        malformed = "<NOVEL_TEXT>\n" + cleaned_text + "\n</NOVALIST_TASK_DONE>"
        selected = [{"id": "f-selected", "title": "古剑来历", "status": "open"}]
        dsh = FakeDSH(
            [malformed],
            json_outputs=[
                {
                    "chapter_id": "chapter_01",
                    "possibly_resolved": [
                        {
                            "foreshadowing_id": "f-selected",
                            "evidence": "古剑来历已揭示",
                            "reason": "核心疑问已经闭合",
                        }
                    ],
                }
            ],
        )

        result = expansion.run_expansion(
            self.project,
            "chapter_01",
            dsh,
            target_chars=300,
            selected_foreshadowing=selected,
        )
        parsed = expansion.ai_protocol.parse_expansion(
            result.raw_output,
            min_chars=1,
            expected_chapter_id="chapter_01",
            allowed_foreshadowing_ids={"f-selected"},
        )

        self.assertIsNone(result.first_raw_output)
        self.assertEqual(result.plain_text_fallback_count, 0)
        self.assertEqual(parsed.text, cleaned_text)
        self.assertNotIn("NOVEL_TEXT", parsed.text)
        self.assertNotIn("NOVALIST_TASK_DONE", parsed.text)
        self.assertEqual(result.raw_output.count("<NOVEL_TEXT>"), 1)
        self.assertEqual(len(parsed.foreshadowing_feedback), 1)

    def test_selected_power_is_forwarded_as_high_priority_context(self) -> None:
        power_path = self.project.canon_dir / "power" / "战力体系.md"
        if not power_path.exists():
            self.skipTest("演示项目没有能力体系样本")
        dsh = FakeDSH([self.valid_output])
        expansion.run_expansion(
            self.project,
            "chapter_01",
            dsh,
            target_chars=300,
            selected_power=[power_path],
        )
        self.assertIn("【本次重点体系设定】", dsh.calls[0]["user"])
        self.assertIn("【常驻核心规则】", dsh.calls[0]["user"])
