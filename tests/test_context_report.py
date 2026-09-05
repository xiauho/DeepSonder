import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from core.context_budget import Section, allocate_with_report
from core.context_report import PromptContextReport
from core.dsh_client import DSHClient
from core.project import NovelProject
from core.prompt_builder import build_expansion_prompt
from core.task_controller import AITaskCancelled
from ui.inspector import render_context_reports


def empty_report(task_kind: str = "consistency_check") -> PromptContextReport:
    return PromptContextReport(
        schema_version=1,
        task_kind=task_kind,
        chapter_id="chapter_01",
        prompt_budget=24_000,
        context_budget=20_000,
        overhead_chars=1_000,
        system_prompt_chars=500,
        user_prompt_chars=1_500,
        total_prompt_chars=2_000,
    )


def acknowledged(client: DSHClient, output: str = "完成"):
    def response(_prompt, **_kwargs):
        client._last_command_chars = 256
        task_path = next(client.working_directory.glob(".novalist-task-*.md"))
        lines = task_path.read_text(encoding="utf-8").splitlines()
        nonce_head = next(line.split(": ", 1)[1] for line in lines if line.startswith("task_nonce_head: "))
        nonce_middle = next(
            line.split("=", 1)[1]
            for line in lines
            if line.startswith("NOVALIST_TRANSPORT_CHECKPOINT: task_nonce_middle=")
        )
        nonce_tail = next(line.split(": ", 1)[1] for line in lines if line.startswith("task_nonce_tail: "))
        return f"NOVALIST_FILE_ACK:{nonce_head}:{nonce_middle}:{nonce_tail}\n{output}"

    return response


class ContextAllocationReportTests(TestCase):
    def test_allocation_reports_full_capped_trimmed_and_dropped(self) -> None:
        sections = [
            Section("full", "甲" * 100, 200, "head", 0),
            Section("capped", "乙" * 400, 200, "head", 1),
            Section("trimmed", "丙" * 400, 400, "tail", 2),
            Section("dropped", "丁" * 200, 200, "head", 3),
        ]
        result = allocate_with_report(sections, 500)

        self.assertEqual(
            [item.status for item in result.sections],
            ["full", "capped", "trimmed", "dropped"],
        )
        self.assertEqual(result.sections[0].sent_chars, 100)
        self.assertEqual(result.sections[-1].sent_chars, 0)

    def test_prompt_report_contains_counts_but_not_source_text(self) -> None:
        secret = "绝不能进入诊断报告的小说原文"
        result = allocate_with_report(
            [Section("content", secret * 20, 160, "tail", 1)],
            160,
        )
        report = empty_report().complete_invocation(
            transport="argv",
            submitted_prompt_chars=300,
            command_chars=350,
            outcome="success",
            task_file_cleaned=True,
        )
        report = PromptContextReport(**{**report.__dict__, "sections": result.sections})

        rendered = json.dumps(report.to_dict(), ensure_ascii=False)
        self.assertNotIn(secret, rendered)
        self.assertNotIn(secret[:5], rendered)
        self.assertIn('"source_chars"', rendered)


class PromptConstructionReportTests(TestCase):
    def test_expansion_report_tracks_history_and_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            project.save_chapter_summaries(
                {"chapter_01": "一", "chapter_02": "二", "chapter_04": "未来"}
            )
            project.save_chapter(
                "chapter_03",
                title="第三章",
                outline="继续前行",
                content="",
            )
            bundle = build_expansion_prompt(
                project,
                "chapter_03",
                3000,
                summary_count=20,
                prompt_budget=48_000,
            )

        self.assertTrue(bundle.system_prompt)
        self.assertTrue(bundle.user_prompt)
        with self.assertRaises(TypeError):
            iter(bundle)
        self.assertEqual(bundle.report.task_kind, "chapter_expansion")
        self.assertEqual(bundle.report.history_requested, 20)
        self.assertEqual(bundle.report.history_available, 2)
        self.assertEqual(bundle.report.history_included, 2)
        self.assertLessEqual(bundle.report.total_prompt_chars, bundle.report.prompt_budget)


class DSHInvocationReportTests(TestCase):
    def test_success_report_contains_actual_argv_metrics(self) -> None:
        reports = []
        client = DSHClient(
            "dsh",
            report_callback=reports.append,
            model_context_window_tokens=128_000,
            context_strategy="compatible",
        )
        client.use_isolated_workspace()
        client._file_transport_supported = True

        with patch.object(client, "_execute_prompt", side_effect=acknowledged(client)):
            client.generate("系统", "任务", context_report=empty_report())

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].transport, "file")
        self.assertEqual(reports[0].outcome, "success")
        self.assertGreater(reports[0].command_chars, 0)
        self.assertEqual(reports[0].input_token_budget, 24_000)
        self.assertEqual(reports[0].runtime_reserve_tokens, 6_000)
        self.assertEqual(reports[0].model_context_window_tokens, 128_000)
        self.assertEqual(reports[0].context_strategy, "compatible")
        self.assertGreater(reports[0].estimated_input_tokens, 0)
        self.assertEqual(reports[0].token_estimator, "conservative_v1")
        client.cleanup()

    def test_file_report_confirms_task_file_cleanup(self) -> None:
        reports = []
        client = DSHClient(
            "dsh",
            report_callback=reports.append,
            input_token_budget=120_000,
        )
        client.use_isolated_workspace()
        client._file_transport_supported = True
        workspace = client.working_directory

        with patch.object(client, "_execute_prompt", side_effect=acknowledged(client)):
            client.generate("系统", "长任务", context_report=empty_report())

        self.assertEqual(reports[0].transport, "file")
        self.assertTrue(reports[0].task_file_cleaned)
        self.assertTrue(reports[0].file_ack_verified)
        self.assertGreater(reports[0].task_file_bytes, 0)
        self.assertEqual(list(workspace.iterdir()), [])
        client.cleanup()

    def test_report_records_recovered_receipt_without_response_text(self) -> None:
        reports = []
        client = DSHClient("dsh", report_callback=reports.append)
        client.use_isolated_workspace()
        client._file_transport_supported = True
        secret_response = "不应进入脱敏报告的模型输出"
        valid_response = acknowledged(client, output="完成")
        calls = 0

        def response(prompt, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return secret_response
            return valid_response(prompt, **kwargs)

        with patch.object(
            client,
            "_execute_prompt",
            side_effect=response,
        ):
            client.generate("系统", "任务", context_report=empty_report())

        self.assertEqual(len(reports), 1)
        self.assertTrue(reports[0].file_ack_verified)
        self.assertEqual(reports[0].file_ack_retry_count, 1)
        self.assertEqual(reports[0].file_ack_error, "missing_ack")
        self.assertNotIn(
            secret_response,
            json.dumps(reports[0].to_dict(), ensure_ascii=False),
        )
        client.cleanup()

    def test_unavailable_long_file_transport_fails_without_prompt_content(self) -> None:
        reports = []
        client = DSHClient(
            "dsh",
            report_callback=reports.append,
            input_token_budget=120_000,
        )
        secret = "秘密正文" * 6_000
        with patch.object(client, "_ensure_file_transport_support", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "本次任务已停止"):
                client.generate("系统", secret, context_report=empty_report())

        self.assertEqual(reports[0].transport, "file_unavailable")
        self.assertEqual(reports[0].outcome, "failed")
        self.assertNotIn(secret, json.dumps(reports[0].to_dict(), ensure_ascii=False))

    def test_timeout_and_cancellation_are_reported_after_file_cleanup(self) -> None:
        for failure, expected in (
            (RuntimeError("dsh 调用超时（>1 秒）。"), "timeout"),
            (AITaskCancelled(), "cancelled"),
        ):
            with self.subTest(expected=expected):
                reports = []
                client = DSHClient(
                    "dsh",
                    report_callback=reports.append,
                )
                client.use_isolated_workspace()
                client._file_transport_supported = True
                workspace = client.working_directory
                with patch.object(client, "_execute_prompt", side_effect=failure):
                    with self.assertRaises(type(failure)):
                        client.generate(
                            "系统",
                            "任务",
                            context_report=empty_report(),
                        )

                self.assertEqual(reports[0].outcome, expected)
                self.assertTrue(reports[0].task_file_cleaned)
                self.assertEqual(list(workspace.iterdir()), [])
                client.cleanup()


class ContextReportRenderingTests(TestCase):
    def test_rendering_shows_metrics_without_accepting_prompt_text(self) -> None:
        report = empty_report().complete_invocation(
            transport="file",
            submitted_prompt_chars=2_100,
            command_chars=300,
            outcome="success",
            task_file_cleaned=True,
        )
        rendered = render_context_reports([report])
        self.assertIn("一致性检查", rendered)
        self.assertIn("临时文件传输", rendered)
        self.assertIn("2,000", rendered)
        self.assertIn("复制脱敏诊断信息", rendered)

    def test_rendering_shows_redacted_token_estimate(self) -> None:
        report = empty_report().complete_invocation(
            transport="argv",
            submitted_prompt_chars=2_000,
            command_chars=2_100,
            outcome="success",
            task_file_cleaned=True,
            input_token_budget=24_000,
            runtime_reserve_tokens=6_000,
            estimated_input_tokens=1_234,
            token_estimator="conservative_v1",
        )
        rendered = render_context_reports([report])
        self.assertIn("1,234 / 24,000 token", rendered)
        self.assertIn("运行预留 6,000 token", rendered)
        self.assertIn("conservative_v1", rendered)

    def test_rendering_shows_receipt_retry_reason(self) -> None:
        report = empty_report().complete_invocation(
            transport="file",
            submitted_prompt_chars=2_000,
            command_chars=300,
            outcome="success",
            task_file_cleaned=True,
            task_file_bytes=3_000,
            file_ack_verified=True,
            file_ack_retry_count=1,
            file_ack_error="missing_ack",
        )

        rendered = render_context_reports([report])
        self.assertIn("自动重试 1 次", rendered)
        self.assertIn("首次异常：未找到回执", rendered)
