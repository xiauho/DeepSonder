import re
import shutil
import subprocess
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from core.dsh_client import DSHClient, DSH_FILE_PROBE_PREFIX
from core.task_controller import AITaskCancelled


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

    def test_auto_transport_moves_a_long_unicode_prompt_to_a_task_file(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(command, **kwargs):
            loader = command[-1]
            match = re.search(r"(\.novalist-task-[a-f0-9]+\.md)", loader)
            self.assertIsNotNone(match)
            task_path = Path(kwargs["cwd"]) / match.group(1)
            payload = task_path.read_text(encoding="utf-8")
            marker = re.search(rf"{DSH_FILE_PROBE_PREFIX}[A-F0-9]+", payload)
            if marker:
                return SimpleNamespace(returncode=0, stdout=marker.group(0), stderr="")
            captured["payload"] = payload
            captured["command"] = command
            return SimpleNamespace(returncode=0, stdout="generated text", stderr="")

        client = DSHClient("dsh", prompt_transport="auto")
        client.use_isolated_workspace()
        workspace = client.working_directory
        user_prompt = "头部标记\n" + "中间内容'\"\n" * 4000 + "尾部标记"
        with patch("core.dsh_client.subprocess.run", side_effect=fake_run) as run:
            result = client.generate("系统约束", user_prompt)

        self.assertEqual(result, "generated text")
        self.assertEqual(run.call_count, 2)
        self.assertIn("头部标记", captured["payload"])
        self.assertIn("中间内容'\"", captured["payload"])
        self.assertIn("尾部标记", captured["payload"])
        self.assertNotIn("尾部标记", captured["command"][-1])
        self.assertLess(len(subprocess.list2cmdline(captured["command"])), 4000)
        self.assertEqual(list(workspace.iterdir()), [])
        client.cleanup()

    def test_auto_transport_falls_back_to_argv_budget_when_file_probe_fails(self) -> None:
        unavailable = SimpleNamespace(
            returncode=0,
            stdout="NOVALIST_FILE_TASK_READ_FAILED",
            stderr="",
        )
        client = DSHClient("dsh", prompt_transport="auto", file_prompt_budget=48_000)
        client.use_isolated_workspace()
        with patch("core.dsh_client.subprocess.run", return_value=unavailable) as run:
            self.assertEqual(client.resolve_prompt_budget(), 24_000)
            self.assertEqual(client.resolve_prompt_budget(), 24_000)

        self.assertEqual(run.call_count, 1)
        self.assertEqual(list(client.working_directory.iterdir()), [])
        client.cleanup()

    def test_explicit_file_transport_reports_an_unavailable_bridge(self) -> None:
        unavailable = SimpleNamespace(
            returncode=0,
            stdout="NOVALIST_FILE_TASK_READ_FAILED",
            stderr="",
        )
        client = DSHClient("dsh", prompt_transport="file")
        with patch("core.dsh_client.subprocess.run", return_value=unavailable):
            with self.assertRaisesRegex(RuntimeError, "无法读取"):
                client.resolve_prompt_budget()
        client.cleanup()

    def test_task_file_is_removed_after_failure_and_cancellation(self) -> None:
        for failure in (RuntimeError("boom"), AITaskCancelled()):
            client = DSHClient("dsh", prompt_transport="file")
            client.use_isolated_workspace()
            client._file_transport_supported = True
            with patch.object(client, "_execute_prompt", side_effect=failure):
                with self.assertRaises(type(failure)):
                    client.generate("system", "private story text")
            self.assertEqual(list(client.working_directory.iterdir()), [])
            client.cleanup()

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

    def test_connection_check_reports_verified_file_transport(self) -> None:
        def fake_run(command, **kwargs):
            if "--version" in command:
                return SimpleNamespace(returncode=0, stdout="0.1.0-rc.7", stderr="")
            if "NOVALIST_PROBE_OK" in command[-1]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="NOVALIST_PROBE_OK",
                    stderr="",
                )
            match = re.search(r"(\.novalist-task-[a-f0-9]+\.md)", command[-1])
            self.assertIsNotNone(match)
            payload = (Path(kwargs["cwd"]) / match.group(1)).read_text(
                encoding="utf-8"
            )
            marker = re.search(rf"{DSH_FILE_PROBE_PREFIX}[A-F0-9]+", payload)
            self.assertIsNotNone(marker)
            return SimpleNamespace(returncode=0, stdout=marker.group(0), stderr="")

        client = DSHClient("dsh", timeout=30, prompt_transport="auto")
        client.use_isolated_workspace()
        with patch("core.dsh_client.shutil.which", return_value="dsh.exe"):
            with patch("core.dsh_client.subprocess.run", side_effect=fake_run) as run:
                result = client.check_connection()

        self.assertIn("扩展任务文件传输可用", result)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(list(client.working_directory.iterdir()), [])
        client.cleanup()

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
        workspace = client.working_directory
        with patch("core.dsh_client.subprocess.run", return_value=completed) as run:
            client.generate("system", "user")
        self.assertEqual(Path(run.call_args.kwargs["cwd"]), client.working_directory)
        client.cleanup()
        self.assertFalse(workspace.exists())

    def test_cleanup_keeps_explicitly_set_working_directory(self) -> None:
        with TemporaryDirectory() as directory:
            client = DSHClient("dsh")
            client.use_isolated_workspace()
            isolated = client.working_directory
            client.set_working_directory(directory)
            client.cleanup()
            self.assertFalse(isolated.exists())
            self.assertTrue(Path(directory).is_dir())

    def test_cleanup_retries_a_transient_windows_directory_error(self) -> None:
        client = DSHClient("dsh")
        client.use_isolated_workspace()
        workspace = client.working_directory
        real_rmtree = shutil.rmtree
        attempts = []

        def flaky_rmtree(path):
            attempts.append(Path(path))
            if len(attempts) == 1:
                raise PermissionError("transient cwd handle")
            real_rmtree(path)

        with patch("core.dsh_client.shutil.rmtree", side_effect=flaky_rmtree):
            with patch("core.dsh_client.time.sleep") as sleep:
                client.cleanup()

        self.assertEqual(attempts, [workspace, workspace])
        sleep.assert_called_once_with(0.05)
        self.assertFalse(workspace.exists())
