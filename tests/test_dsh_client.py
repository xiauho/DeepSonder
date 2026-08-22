from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
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
        self.assertIn("[系统约束]", command[-1])
        self.assertIn("[用户任务]", command[-1])
        self.assertIn("NOVALIST_TASK_START", command[-1])

    def test_json_code_fence_is_supported(self) -> None:
        parsed = DSHClient._extract_json('```json\n{"ok": true}\n```')
        self.assertEqual(parsed, {"ok": True})

    def test_timeout_override_is_forwarded(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="generated text\n", stderr="")
        client = DSHClient("dsh", timeout=180)
        with patch("core.dsh_client.subprocess.run", return_value=completed) as run:
            client.generate("system", "user", timeout_override=7)
        self.assertEqual(run.call_args.kwargs["timeout"], 7)

    def test_json_preamble_is_supported(self) -> None:
        parsed = DSHClient._extract_json('Here is the result: {"ok": true}')
        self.assertEqual(parsed, {"ok": True})

    def test_bare_command_resolves_windows_shim(self) -> None:
        client = DSHClient("dsh")
        with patch("core.dsh_client.shutil.which", return_value="C:/npm/dsh.CMD"):
            self.assertEqual(client._resolve_command(), "C:/npm/dsh.CMD")

    def test_long_prompt_bypasses_windows_cmd_shim(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            shim = root / "dsh.CMD"
            script = root / "node_modules" / "@deepseek-ai" / "dsh" / "bin.js"
            script.parent.mkdir(parents=True)
            script.write_text("", encoding="utf-8")
            shim.write_text(
                '@echo off\n"%_prog%" "%dp0%\\node_modules\\@deepseek-ai\\dsh\\bin.js" %*\n',
                encoding="utf-8",
            )
            completed = SimpleNamespace(returncode=0, stdout="generated text\n", stderr="")
            client = DSHClient(str(shim), profile="headless")
            with patch("core.dsh_client.shutil.which", return_value="C:/node/node.exe"):
                with patch("core.dsh_client.subprocess.run", return_value=completed) as run:
                    client.generate("system", "x" * 9000)

            command = run.call_args.args[0]
            self.assertEqual(command[0], "C:/node/node.exe")
            self.assertEqual(command[1], str(script))
            self.assertIn("NOVALIST_TASK_START", command[-1])
