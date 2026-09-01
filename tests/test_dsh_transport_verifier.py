import hashlib
import json
import threading
from unittest import TestCase
from unittest.mock import patch

from core.dsh_client import DSHClient
from scripts.verify_dsh_prompt_transport import (
    DiagnosticDSHClient,
    InvocationTrace,
    SimulatedTimeoutClient,
    _final_report,
    build_synthetic_prompt,
)


class DSHTransportVerifierTests(TestCase):
    def test_synthetic_prompt_has_exact_length_and_ordered_unique_markers(self) -> None:
        prompt = build_synthetic_prompt(45_000)

        self.assertEqual(len(prompt.text), 45_000)
        positions = [prompt.text.index(marker) for marker in prompt.markers]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(set(prompt.markers)), 3)
        self.assertEqual(
            prompt.sha256,
            hashlib.sha256(prompt.text.encode("utf-8")).hexdigest(),
        )
        self.assertIn("synthetic='引号'", prompt.text)
        self.assertIn('json={"ok":true}', prompt.text)

    def test_diagnostic_trace_keeps_metrics_but_not_prompt_text(self) -> None:
        client = DiagnosticDSHClient("dsh")
        secret = "PRIVATE_SYNTHETIC_PAYLOAD"
        with patch.object(DSHClient, "_execute_prompt", return_value="ok"):
            result = client._execute_prompt(
                secret,
                session_id=None,
                timeout=5,
                cancel_event=threading.Event(),
                submitted_prompt_length=len(secret),
            )

        self.assertEqual(result, "ok")
        rendered = json.dumps(client.invocations[0].__dict__)
        self.assertNotIn(secret, rendered)
        self.assertEqual(client.invocations[0].submitted_prompt_chars, len(secret))

    def test_final_report_does_not_contain_markers_or_prompt(self) -> None:
        prompt = build_synthetic_prompt(10_000)
        client = DiagnosticDSHClient("dsh")
        client.invocations.append(InvocationTrace("file", 10_000, 400, 300))
        report = _final_report(
            {
                "dsh_command": "dsh",
                "dsh_timeout": 60,
            },
            client,
            prompt,
            [],
        )

        rendered = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(prompt.text, rendered)
        for marker in prompt.markers:
            self.assertNotIn(marker, rendered)
        self.assertIn(prompt.sha256, rendered)

    def test_simulated_timeout_removes_task_file_and_workspace(self) -> None:
        prompt = build_synthetic_prompt(10_000)
        client = SimulatedTimeoutClient("dsh", prompt_transport="file")
        client.use_isolated_workspace()
        workspace = client.working_directory
        client._file_transport_supported = True

        with self.assertRaisesRegex(RuntimeError, "超时"):
            client.generate("timeout cleanup", prompt.text)
        self.assertEqual(list(workspace.glob(".novalist-task-*.md")), [])
        client.cleanup()

        self.assertFalse(workspace.exists())
