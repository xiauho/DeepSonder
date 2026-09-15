from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from core import continuation
from core.project import NovelProject


class FakeDSH:
    def __init__(self, outputs, json_outputs=None):
        self.outputs = list(outputs)
        self.calls = []
        self.json_outputs = list(json_outputs or [])
        self.json_calls = []

    def prompt_build_budget(self):
        return 24_000

    def generate(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(
            {
                "system": system_prompt,
                "user": user_prompt,
                "context_report": kwargs.get("context_report"),
            }
        )
        return self.outputs.pop(0)

    def generate_json(self, system_prompt, user_prompt, **kwargs):
        self.json_calls.append({"system": system_prompt, "user": user_prompt})
        return self.json_outputs.pop(0)


class ContinuationTargetTests(TestCase):
    def test_remaining_length_is_used_up_to_three_thousand(self) -> None:
        self.assertEqual(continuation.continuation_target_chars(800, 3000), 2200)
        self.assertEqual(continuation.continuation_target_chars(500, 5000), 3000)

    def test_empty_near_target_and_completed_chapters_are_rejected(self) -> None:
        with self.assertRaisesRegex(continuation.ContinuationNotAvailable, "正文为空"):
            continuation.continuation_target_chars(0, 3000)
        with self.assertRaisesRegex(continuation.ContinuationNotAvailable, "不足最小续写"):
            continuation.continuation_target_chars(2850, 3000)
        with self.assertRaisesRegex(continuation.ContinuationNotAvailable, "已达到"):
            continuation.continuation_target_chars(3200, 3000)


class ContinuationWorkflowTests(TestCase):
    def make_project(self, root: Path, content: str) -> NovelProject:
        project = NovelProject.create(root / "novel", "测试")
        project.save_chapter(
            "chapter_01",
            title="第一章",
            outline="推进调查",
            content=content,
        )
        return project

    def test_valid_output_uses_remaining_length_and_one_context(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self.make_project(Path(tmp), "原" * 800)
            output = f"<NOVEL_TEXT>\n{'续' * 2200}\n</NOVEL_TEXT>"
            dsh = FakeDSH([output])

            result = continuation.run_continuation(
                project, "chapter_01", dsh, target_chapter_chars=3000
            )

            self.assertEqual(result.current_chars, 800)
            self.assertEqual(result.requested_chars, 2200)
            self.assertEqual(len(dsh.calls), 1)
            self.assertIn("本次新增正文目标：2200 字", dsh.calls[0]["system"])
            self.assertEqual(
                dsh.calls[0]["context_report"].task_kind,
                "continuation_current_chapter",
            )

    def test_request_is_capped_at_three_thousand(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self.make_project(Path(tmp), "原" * 500)
            output = f"<NOVEL_TEXT>\n{'续' * 3000}\n</NOVEL_TEXT>"
            result = continuation.run_continuation(
                project,
                "chapter_01",
                FakeDSH([output]),
                target_chapter_chars=5000,
            )
            self.assertEqual(result.requested_chars, 3000)

    def test_protocol_failure_retries_once(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self.make_project(Path(tmp), "原" * 2700)
            invalid = "我已就绪，请告诉我需要做什么。"
            valid = f"<NOVEL_TEXT>\n{'续' * 300}\n</NOVEL_TEXT>"
            dsh = FakeDSH([invalid, valid])

            result = continuation.run_continuation(
                project, "chapter_01", dsh, target_chapter_chars=3000
            )

            self.assertEqual(result.first_raw_output, invalid)
            self.assertEqual(len(dsh.calls), 2)
            self.assertIn("续写任务纠偏重试", dsh.calls[1]["system"])
            self.assertEqual(
                dsh.calls[1]["context_report"].task_kind,
                "continuation_retry",
            )

    def test_under_length_continuation_receives_safe_supplement(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self.make_project(Path(tmp), "原" * 800)
            anchor = "风从长街尽头卷来，灯影在湿润的石板上缓慢摇晃。"
            source = anchor + "续" * (2000 - len(anchor))
            addition = "他停下脚步，重新确认远处传来的声音。" + "补" * 130
            dsh = FakeDSH(
                [f"<NOVEL_TEXT>\n{source}\n</NOVEL_TEXT>"],
                json_outputs=[
                    {
                        "type": "prose_length_supplement",
                        "chapter_id": "chapter_01",
                        "insertions": [
                            {
                                "anchor": anchor,
                                "position": "after",
                                "text": addition,
                            }
                        ],
                    }
                ],
            )

            result = continuation.run_continuation(
                project, "chapter_01", dsh, target_chapter_chars=3000
            )

            self.assertTrue(result.supplement_attempted)
            self.assertTrue(result.supplement_applied)
            self.assertEqual(result.initial_generated_chars, 2000)
            self.assertEqual(result.final_generated_chars, 2000 + len(addition))
            self.assertEqual(result.projected_final_chars, 2800 + len(addition))
            self.assertEqual(result.length_status, "qualified")
            self.assertEqual(len(dsh.json_calls), 1)

    def test_severely_short_continuation_receives_full_length_retry(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self.make_project(Path(tmp), "原" * 800)
            short = "短" * 1200
            corrected = "续" * 2100
            dsh = FakeDSH(
                [
                    f"<NOVEL_TEXT>\n{short}\n</NOVEL_TEXT>",
                    f"<NOVEL_TEXT>\n{corrected}\n</NOVEL_TEXT>",
                ]
            )

            result = continuation.run_continuation(
                project, "chapter_01", dsh, target_chapter_chars=3000
            )

            self.assertTrue(result.length_retry_attempted)
            self.assertTrue(result.length_retry_applied)
            self.assertEqual(result.original_draft_text, short)
            self.assertEqual(result.final_generated_chars, 2100)
            self.assertEqual(result.projected_final_chars, 2900)
            self.assertFalse(result.supplement_attempted)
            self.assertIn("本次新增正文目标：2200 字", dsh.calls[1]["system"])
