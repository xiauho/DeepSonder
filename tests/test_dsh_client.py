from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from core.dsh_client import DSHClient


class DSHClientTests(TestCase):
    def test_launcher_arguments_precede_dsh_arguments(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="generated text\n", stderr="")
        client = DSHClient(
            dsh_command="npx.cmd",
            launcher_args=["--yes", "@deepseek-ai/dsh"],
            profile="headless",
            extra_args=["--patch", "overlay.yml"],
        )

        with patch("core.dsh_client.subprocess.run", return_value=completed) as run:
            result = client.generate("system", "user")

        self.assertEqual(result, "generated text")
        command = run.call_args.args[0]
        self.assertEqual(
            command[:-1],
            [
                "npx.cmd",
                "--yes",
                "@deepseek-ai/dsh",
                "--profile",
                "headless",
                "--patch",
                "overlay.yml",
            ],
        )
        self.assertIn("[系统设定]", command[-1])
        self.assertIn("[用户任务]", command[-1])

    def test_json_code_fence_is_supported(self) -> None:
        parsed = DSHClient._extract_json('```json\n{"ok": true}\n```')
        self.assertEqual(parsed, {"ok": True})
