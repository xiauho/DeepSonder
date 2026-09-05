import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from core.ai_workflow import AIWorkflowService
from core.project import NovelProject


class FakeDSH:
    def __init__(self, summary: str, state: dict):
        self.summary = summary
        self.state = state
        self.generate_calls: list[tuple[str, str]] = []
        self.json_calls: list[tuple[str, str]] = []
        self.context_reports = []

    def prompt_build_budget(self):
        return 24_000

    def generate(self, system_prompt: str, user_prompt: str, *args, **kwargs) -> str:
        self.generate_calls.append((system_prompt, user_prompt))
        self.context_reports.append(kwargs.get("context_report"))
        return self.summary

    def generate_json(self, system_prompt: str, user_prompt: str, *args, **kwargs) -> dict:
        self.json_calls.append((system_prompt, user_prompt))
        self.context_reports.append(kwargs.get("context_report"))
        return self.state


class AIWorkflowTests(TestCase):
    def test_consistency_check_forwards_remote_history_setting(self) -> None:
        project = object()
        service = AIWorkflowService(FakeDSH("unused", {}))
        with patch("core.ai_workflow.consistency.run_consistency_check", return_value="ok") as run:
            result = service.check(project, "chapter_01", history_remote_enabled=False)
        self.assertEqual(result, "ok")
        run.assert_called_once_with(
            project,
            "chapter_01",
            service.dsh,
            cancel_event=None,
            history_remote_enabled=False,
        )

    def test_memory_workflow_exposes_only_fact_patch_entry(self) -> None:
        service = AIWorkflowService(FakeDSH("unused", {}))
        self.assertTrue(callable(service.update_memory))
        self.assertFalse(hasattr(service, "update_memory_v2"))
        self.assertFalse(hasattr(service, "update_memory_for_pipeline"))

    def test_consistency_repair_uses_json_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            dsh = FakeDSH(
                "unused",
                {
                    "type": "consistency_repair",
                    "chapter_id": "chapter_01",
                    "issue_id": "issue_1",
                    "status": "ready",
                    "target": "chapter",
                    "expected_original": "原句",
                    "replacement": "修复句",
                    "explanation": "修复冲突",
                    "preserved_facts": [],
                },
            )
            result = AIWorkflowService(dsh).repair_consistency(
                project,
                "chapter_01",
                {
                    "issue_id": "issue_1",
                    "chapter_quote": "原句",
                    "description": "冲突",
                    "evidence": "证据",
                },
            )
            self.assertEqual(result["replacement"], "修复句")
            self.assertEqual(len(dsh.json_calls), 1)
            self.assertIn("consistency_repair", dsh.json_calls[0][1])
