from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from core import expansion
from core.project import NovelProject


class FakeDSH:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    def generate(self, system_prompt, user_prompt, session_id=None, *, timeout_override=None):
        self.calls.append(
            {
                "system": system_prompt,
                "user": user_prompt,
                "timeout_override": timeout_override,
            }
        )
        if not self.outputs:
            raise AssertionError("generate() called more times than expected")
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


class ExpansionTests(TestCase):
    def setUp(self) -> None:
        self.project = NovelProject(Path(__file__).parents[1] / "projects" / "demo_novel")
        self.valid_output = f"<NOVEL_TEXT>\n{'字' * 300}\n</NOVEL_TEXT>"
        self.onboarding_output = "我已就绪。当前会话环境已加载，项目现有结构完好，请问这次需要我做什么？"

    def test_valid_first_output_returns_without_retry(self) -> None:
        dsh = FakeDSH([self.valid_output])
        raw, first_raw = expansion.run_expansion(self.project, "chapter_01", dsh, target_chars=300)
        self.assertEqual(raw, self.valid_output)
        self.assertIsNone(first_raw)
        self.assertEqual(len(dsh.calls), 1)

    def test_retry_keeps_full_timeout_budget(self) -> None:
        dsh = FakeDSH([self.onboarding_output, self.valid_output])
        with patch(
            "core.expansion.build_ai_context",
            wraps=expansion.build_ai_context,
        ) as build_context:
            raw, first_raw = expansion.run_expansion(
                self.project, "chapter_01", dsh, target_chars=300
            )
        self.assertEqual(raw, self.valid_output)
        self.assertEqual(first_raw, self.onboarding_output)
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
