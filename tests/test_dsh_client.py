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

    def test_long_prompt_bypasses_standard_npm_cmd_shim(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            shim = root / "dsh.cmd"
            node = root / "node.exe"
            script = root / "node_modules" / "@deepseek-ai" / "dsh" / "dist" / "cli.js"
            script.parent.mkdir(parents=True)
            node.write_text("", encoding="utf-8")
            script.write_text("", encoding="utf-8")
            shim.write_text(
                '@IF EXIST "%~dp0\\node.exe" (\n'
                '  "%~dp0\\node.exe" "%~dp0\\node_modules\\@deepseek-ai\\dsh\\dist\\cli.js" %*\n'
                ') ELSE (\n'
                '  node "%~dp0\\node_modules\\@deepseek-ai\\dsh\\dist\\cli.js" %*\n'
                ')\n',
                encoding="utf-8",
            )
            completed = SimpleNamespace(returncode=0, stdout="generated text\n", stderr="")
            client = DSHClient(str(shim), profile="headless")
            with patch("core.dsh_client.subprocess.run", return_value=completed) as run:
                client.generate("system", "x" * 9000)

            command = run.call_args.args[0]
            self.assertEqual(command[0], str(node))
            self.assertEqual(command[1], str(script))
            self.assertIn("NOVALIST_TASK_START", command[-1])

    def test_short_prompt_also_bypasses_readable_cmd_shim(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            shim = root / "dsh.cmd"
            node = root / "node.exe"
            script = root / "node_modules" / "@deepseek-ai" / "dsh" / "cli.js"
            script.parent.mkdir(parents=True)
            node.write_text("", encoding="utf-8")
            script.write_text("", encoding="utf-8")
            shim.write_text(
                '"%~dp0\\node.exe" "%~dp0\\node_modules\\@deepseek-ai\\dsh\\cli.js" %*\n',
                encoding="utf-8",
            )
            completed = SimpleNamespace(returncode=0, stdout="generated text\n", stderr="")
            client = DSHClient(str(shim), profile="headless")
            with patch("core.dsh_client.subprocess.run", return_value=completed) as run:
                client.generate("system", "short task")

            command = run.call_args.args[0]
            self.assertEqual(command[:2], [str(node), str(script)])

    def test_empty_task_onboarding_is_reported_before_protocol_retry(self) -> None:
        completed = SimpleNamespace(
            returncode=0,
            stdout="I don't see an actual task in your message yet.\n",
            stderr="",
        )
        client = DSHClient("dsh")
        with patch("core.dsh_client.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "没有收到 Novalist"):
                client.generate("system", "user")

    def test_connection_check_runs_a_real_probe(self) -> None:
        version = SimpleNamespace(returncode=0, stdout="0.1.0-rc.7\n", stderr="")
        probe = SimpleNamespace(returncode=0, stdout="NOVALIST_PROBE_OK\n", stderr="")
        client = DSHClient("dsh", timeout=30)
        with patch("core.dsh_client.shutil.which", return_value="dsh.exe"):
            with patch("core.dsh_client.subprocess.run", side_effect=[version, probe]) as run:
                result = client.check_connection()

        self.assertIn("任务传递正常", result)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0][-1], "--version")
        self.assertIn("NOVALIST_PROBE_OK", run.call_args_list[1].args[0][-1])

    def test_connection_check_rejects_empty_task_response(self) -> None:
        version = SimpleNamespace(returncode=0, stdout="0.1.0-rc.7\n", stderr="")
        onboarding = SimpleNamespace(
            returncode=0,
            stdout="I don't see an actual task in your message.\n",
            stderr="",
        )
        client = DSHClient("dsh", timeout=30)
        with patch("core.dsh_client.shutil.which", return_value="dsh.exe"):
            with patch("core.dsh_client.subprocess.run", side_effect=[version, onboarding]):
                with self.assertRaisesRegex(RuntimeError, "没有收到 Novalist"):
                    client.check_connection()

    def test_isolated_workspace_is_empty_reused_and_cleaned(self) -> None:
        client = DSHClient("dsh")
        client.use_isolated_workspace()
        workspace = client.working_directory
        self.assertIsNotNone(workspace)
        self.assertTrue(workspace.is_dir())
        self.assertEqual(list(workspace.iterdir()), [])
        client.use_isolated_workspace()
        self.assertEqual(client.working_directory, workspace)
        client.cleanup()
        self.assertFalse(workspace.exists())
        self.assertIsNone(client.working_directory)

    def test_generate_runs_in_isolated_workspace(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="generated text\n", stderr="")
        client = DSHClient("dsh")
        client.use_isolated_workspace()
        with patch("core.dsh_client.subprocess.run", return_value=completed) as run:
            client.generate("system", "user")
        self.assertEqual(Path(run.call_args.kwargs["cwd"]), client.working_directory)

    def test_cleanup_keeps_explicitly_set_working_directory(self) -> None:
        with TemporaryDirectory() as directory:
            client = DSHClient("dsh")
            client.use_isolated_workspace()
            isolated = client.working_directory
            client.set_working_directory(directory)
            client.cleanup()
            self.assertFalse(isolated.exists())
            self.assertTrue(Path(directory).is_dir())
