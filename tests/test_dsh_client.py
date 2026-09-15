import re
import shutil
import subprocess
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from core.dsh_client import (
    DSHClient,
    DSH_FILE_ACK_PREFIX,
    DSH_FILE_PROBE_PREFIX,
    DSH_FILE_TASK_SCHEMA,
    DSH_PROBE_MARKER,
)
from core.task_controller import AITaskCancelled


def acknowledged_result(command, **kwargs):
    """Return a valid task-file ACK for subprocess-based client tests."""
    loader = command[-1]
    match = re.search(r"(\.novalist-task-[a-f0-9]+\.md)", loader)
    if match is None:
        return SimpleNamespace(returncode=0, stdout="generated text\n", stderr="")
    task_path = Path(kwargs["cwd"]) / match.group(1)
    payload = task_path.read_text(encoding="utf-8")
    nonce_head = re.search(r"^task_nonce_head: ([A-F0-9]+)$", payload, re.MULTILINE)
    nonce_middle = re.search(
        r"task_nonce_middle=([A-F0-9]+)", payload, re.MULTILINE
    )
    nonce_tail = re.search(r"^task_nonce_tail: ([A-F0-9]+)$", payload, re.MULTILINE)
    ack = (
        f"{DSH_FILE_ACK_PREFIX}{nonce_head.group(1)}:"
        f"{nonce_middle.group(1)}:{nonce_tail.group(1)}"
    )
    marker = re.search(rf"{DSH_FILE_PROBE_PREFIX}[A-F0-9]+", payload)
    result = (
        marker.group(0)
        if marker
        else DSH_PROBE_MARKER if DSH_PROBE_MARKER in payload else "generated text"
    )
    return SimpleNamespace(returncode=0, stdout=f"{ack}\n{result}", stderr="")


def current_task_ack(client: DSHClient) -> str:
    task_path = next(client.working_directory.glob(".novalist-task-*.md"))
    payload = task_path.read_text(encoding="utf-8")
    nonce_head = re.search(r"^task_nonce_head: ([A-F0-9]+)$", payload, re.MULTILINE)
    nonce_middle = re.search(
        r"task_nonce_middle=([A-F0-9]+)", payload, re.MULTILINE
    )
    nonce_tail = re.search(r"^task_nonce_tail: ([A-F0-9]+)$", payload, re.MULTILINE)
    return (
        f"{DSH_FILE_ACK_PREFIX}{nonce_head.group(1)}:"
        f"{nonce_middle.group(1)}:{nonce_tail.group(1)}"
    )


class DSHClientTests(TestCase):
    def test_launcher_arguments_precede_dsh_arguments(self) -> None:
        client = DSHClient(
            dsh_command="npx.cmd",
            launcher_args=["--yes", "@deepseek-ai/dsh"],
            profile="headless",
            extra_args=["--patch", "overlay.yml"],
        )
        client._file_transport_supported = True

        with patch.object(client, "_resolve_command", return_value="npx.cmd"):
            with patch.object(
                client,
                "_prepare_command_for_prompt",
                side_effect=lambda command, _resolved: command,
            ):
                with patch(
                    "core.dsh_client.subprocess.run", side_effect=acknowledged_result
                ) as run:
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
        self.assertIn("NOVALIST_FILE_TASK_LOADER", command[-1])
        self.assertNotIn("system", command[-1])
        client.cleanup()

    def test_json_code_fence_is_supported(self) -> None:
        parsed = DSHClient._extract_json('```json\n{"ok": true}\n```')
        self.assertEqual(parsed, {"ok": True})

    def test_timeout_override_is_forwarded(self) -> None:
        client = DSHClient("dsh", timeout=180)
        client._file_transport_supported = True
        with patch("core.dsh_client.subprocess.run", side_effect=acknowledged_result) as run:
            client.generate("system", "user", timeout_override=7)
        self.assertEqual(run.call_args.kwargs["timeout"], 7)
        client.cleanup()

    def test_windows_prompt_newlines_stay_inside_one_process_argument(self) -> None:
        client = DSHClient("dsh", input_token_budget=120_000)
        client._file_transport_supported = True
        with patch("core.dsh_client.os.name", "nt"):
            with patch(
                "core.dsh_client.subprocess.run", side_effect=acknowledged_result
            ) as run:
                client.generate("system line 1\nsystem line 2", "user line 1\r\nuser line 2")

        transmitted = run.call_args.args[0][-1]
        self.assertNotIn("\n", transmitted)
        self.assertNotIn("\r", transmitted)
        self.assertIn("\u2028", transmitted)
        self.assertNotIn("user line 2", transmitted)
        self.assertIn("NOVALIST_FILE_TASK_LOADER", transmitted)
        client.cleanup()

    def test_file_transport_moves_a_long_unicode_prompt_to_a_task_file(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(command, **kwargs):
            loader = command[-1]
            match = re.search(r"(\.novalist-task-[a-f0-9]+\.md)", loader)
            self.assertIsNotNone(match)
            task_path = Path(kwargs["cwd"]) / match.group(1)
            payload = task_path.read_text(encoding="utf-8")
            nonce_head = re.search(
                r"^task_nonce_head: ([A-F0-9]+)$", payload, re.MULTILINE
            )
            nonce_middle = re.search(
                r"task_nonce_middle=([A-F0-9]+)", payload, re.MULTILINE
            )
            nonce_tail = re.search(
                r"^task_nonce_tail: ([A-F0-9]+)$", payload, re.MULTILINE
            )
            self.assertIsNotNone(nonce_head)
            self.assertIsNotNone(nonce_middle)
            self.assertIsNotNone(nonce_tail)
            ack = (
                DSH_FILE_ACK_PREFIX
                + nonce_head.group(1)
                + ":"
                + nonce_middle.group(1)
                + ":"
                + nonce_tail.group(1)
            )
            marker = re.search(rf"{DSH_FILE_PROBE_PREFIX}[A-F0-9]+", payload)
            if marker:
                return SimpleNamespace(
                    returncode=0,
                    stdout=f"{ack}\n{marker.group(0)}",
                    stderr="",
                )
            captured["payload"] = payload
            captured["command"] = command
            return SimpleNamespace(
                returncode=0,
                stdout=f"{ack}\ngenerated text",
                stderr="",
            )

        client = DSHClient("dsh", input_token_budget=120_000)
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
        self.assertIn(DSH_FILE_TASK_SCHEMA, captured["payload"])
        self.assertIn("payload_sha256:", captured["payload"])
        self.assertNotIn("task_nonce_head:", captured["command"][-1])
        self.assertNotIn("task_nonce_tail:", captured["command"][-1])
        self.assertNotIn("尾部标记", captured["command"][-1])
        self.assertLess(len(subprocess.list2cmdline(captured["command"])), 4000)
        self.assertEqual(list(workspace.iterdir()), [])
        client.cleanup()

    def test_prompt_build_budget_does_not_probe_file_capability(self) -> None:
        client = DSHClient("dsh", file_prompt_budget=48_000)
        with patch("core.dsh_client.subprocess.run") as run:
            self.assertLess(client.prompt_build_budget(), 24_000)
            self.assertFalse(hasattr(client, "resolve_prompt_budget"))

        run.assert_not_called()
        client.cleanup()

    def test_short_business_prompt_also_uses_file_bridge(self) -> None:
        client = DSHClient("dsh")
        client._file_transport_supported = True
        with patch("core.dsh_client.subprocess.run", side_effect=acknowledged_result) as run:
            result = client.generate("系统", "短任务")

        self.assertEqual(result, "generated text")
        self.assertEqual(run.call_count, 1)
        self.assertIn("NOVALIST_FILE_TASK_LOADER", run.call_args.args[0][-1])
        self.assertNotIn("短任务", run.call_args.args[0][-1])
        client.cleanup()

    def test_unavailable_file_bridge_fails_closed_at_generation_time(self) -> None:
        client = DSHClient("dsh")
        with patch.object(client, "_ensure_file_transport_support", return_value=False):
            with patch.object(client, "_execute_prompt") as execute:
                with self.assertRaisesRegex(RuntimeError, "本次任务已停止"):
                    client.generate("system", "任务")
        execute.assert_not_called()
        client.cleanup()

    def test_transient_negative_file_probe_is_retried(self) -> None:
        client = DSHClient("dsh")
        client.use_isolated_workspace()

        with patch.object(client, "_execute_prompt", return_value="错误回执"):
            self.assertFalse(
                client._ensure_file_transport_support(timeout=5, cancel_event=None)
            )
        self.assertIsNone(client._file_transport_supported)

        def valid_probe(_loader, **_kwargs):
            task_path = next(client.working_directory.glob(".novalist-task-*.md"))
            payload = task_path.read_text(encoding="utf-8")
            head = re.search(r"^task_nonce_head: ([A-F0-9]+)$", payload, re.MULTILINE)
            middle = re.search(r"task_nonce_middle=([A-F0-9]+)", payload)
            tail = re.search(r"^task_nonce_tail: ([A-F0-9]+)$", payload, re.MULTILINE)
            marker = re.search(rf"{DSH_FILE_PROBE_PREFIX}[A-F0-9]+", payload)
            return (
                f"{DSH_FILE_ACK_PREFIX}{head.group(1)}:{middle.group(1)}:"
                f"{tail.group(1)}\n{marker.group(0)}"
            )

        with patch.object(client, "_execute_prompt", side_effect=valid_probe):
            self.assertTrue(
                client._ensure_file_transport_support(timeout=5, cancel_event=None)
            )
        self.assertTrue(client._file_transport_supported)
        client.cleanup()

    def test_over_budget_business_prompt_stops_before_probe_or_file_write(self) -> None:
        client = DSHClient("dsh", input_token_budget=4_000)
        with patch.object(client, "_ensure_file_transport_support") as probe:
            with patch.object(client, "_write_task_file") as write:
                with self.assertRaisesRegex(RuntimeError, "token 安全上限"):
                    client.generate("系统", "正文" * 4_000)
        probe.assert_not_called()
        write.assert_not_called()
        client.cleanup()

    def test_file_response_requires_exact_nonce_and_is_stripped(self) -> None:
        client = DSHClient("dsh")
        client.use_isolated_workspace()
        client._file_transport_supported = True

        def valid_response(_loader, **_kwargs):
            task_path = next(client.working_directory.glob(".novalist-task-*.md"))
            payload = task_path.read_text(encoding="utf-8")
            nonce_head = re.search(
                r"^task_nonce_head: ([A-F0-9]+)$", payload, re.MULTILINE
            )
            nonce_middle = re.search(
                r"task_nonce_middle=([A-F0-9]+)", payload, re.MULTILINE
            )
            nonce_tail = re.search(
                r"^task_nonce_tail: ([A-F0-9]+)$", payload, re.MULTILINE
            )
            return (
                f"{DSH_FILE_ACK_PREFIX}{nonce_head.group(1)}:"
                f"{nonce_middle.group(1)}:"
                f"{nonce_tail.group(1)}\n最终结果"
            )

        with patch.object(client, "_execute_prompt", side_effect=valid_response):
            self.assertEqual(client.generate("system", "user"), "最终结果")
        with patch.object(
            client, "_execute_prompt", return_value="错误回执\n最终结果"
        ) as execute:
            with self.assertRaisesRegex(RuntimeError, "回执无效"):
                client.generate("system", "user")
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(list(client.working_directory.iterdir()), [])
        client.cleanup()

    def test_file_response_accepts_bom_outer_fence_and_short_preamble(self) -> None:
        client = DSHClient("dsh")
        client.use_isolated_workspace()
        task_file = client._write_task_file("synthetic task")
        try:
            response = (
                "\ufeff```text\n已完成文件读取。\n"
                f"{task_file.expected_ack}\n最终结果\n```"
            )
            self.assertEqual(
                client._validate_file_response(response, task_file),
                "最终结果",
            )
        finally:
            client._remove_task_file(task_file)
            client.cleanup()

    def test_invalid_receipt_retries_same_task_file_once(self) -> None:
        client = DSHClient("dsh")
        client.use_isolated_workspace()
        client._file_transport_supported = True
        task_names: list[str] = []

        def response(prompt, **_kwargs):
            task_path = next(client.working_directory.glob(".novalist-task-*.md"))
            task_names.append(task_path.name)
            if len(task_names) == 1:
                return '{"ok": true}'
            self.assertIn("上一次回复未提供可验证", prompt)
            return f"{current_task_ack(client)}\n" + '{"ok": true}'

        with patch.object(client, "_execute_prompt", side_effect=response) as execute:
            self.assertEqual(client.generate("只输出合法 JSON", "返回结果"), '{"ok": true}')

        self.assertEqual(execute.call_count, 2)
        self.assertEqual(len(set(task_names)), 1)
        self.assertEqual(list(client.working_directory.iterdir()), [])
        client.cleanup()

    def test_retry_still_fails_closed_and_cleans_task_file(self) -> None:
        client = DSHClient("dsh")
        client.use_isolated_workspace()
        client._file_transport_supported = True
        workspace = client.working_directory

        with patch.object(client, "_execute_prompt", return_value="没有回执") as execute:
            with self.assertRaisesRegex(RuntimeError, "自动重试一次后仍无法确认"):
                client.generate("system", "user")

        self.assertEqual(execute.call_count, 2)
        self.assertEqual(list(workspace.iterdir()), [])
        client.cleanup()

    def test_task_file_explains_ack_precedes_json_only_output(self) -> None:
        client = DSHClient("dsh")
        client.use_isolated_workspace()
        task_file = client._write_task_file(
            client._combine_prompts("只输出合法 JSON。", "返回结果。")
        )
        try:
            payload = task_file.path.read_text(encoding="utf-8")
            self.assertIn("只约束回执后的业务结果", payload)
            self.assertIn("第一行先输出三段 nonce 回执", payload)
            self.assertTrue(payload.endswith(task_file.nonce_tail))
        finally:
            client._remove_task_file(task_file)
            client.cleanup()

    def test_task_file_byte_limit_is_enforced_before_execution(self) -> None:
        client = DSHClient(
            "dsh",
            task_file_max_bytes=64_000,
            input_token_budget=120_000,
        )
        client._file_transport_supported = True
        with patch.object(client, "_execute_prompt") as execute:
            with self.assertRaisesRegex(RuntimeError, "任务文件超过安全上限"):
                client.generate("system", "字" * 30_000)
        execute.assert_not_called()
        self.assertEqual(list(client.working_directory.iterdir()), [])
        client.cleanup()

    def test_task_file_is_removed_after_failure_and_cancellation(self) -> None:
        for failure in (RuntimeError("boom"), AITaskCancelled()):
            client = DSHClient("dsh")
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

    def test_relative_cmd_command_is_resolved_before_shim_handling(self) -> None:
        client = DSHClient("npx.cmd")
        with patch(
            "core.dsh_client.shutil.which",
            return_value="C:/Program Files/nodejs/npx.cmd",
        ):
            self.assertEqual(
                client._resolve_command(),
                "C:/Program Files/nodejs/npx.cmd",
            )

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
            client = DSHClient(str(shim), profile="headless")
            client._file_transport_supported = True
            with patch("core.dsh_client.shutil.which", return_value="C:/node/node.exe"):
                with patch(
                    "core.dsh_client.subprocess.run", side_effect=acknowledged_result
                ) as run:
                    client.generate("system", "x" * 9000)

            command = run.call_args.args[0]
            self.assertEqual(command[0], "C:/node/node.exe")
            self.assertEqual(command[1], str(script))
            self.assertIn("NOVALIST_FILE_TASK_LOADER", command[-1])
            self.assertNotIn("x" * 100, command[-1])
            client.cleanup()

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
            client = DSHClient(str(shim), profile="headless")
            client._file_transport_supported = True
            with patch(
                "core.dsh_client.subprocess.run", side_effect=acknowledged_result
            ) as run:
                client.generate("system", "x" * 9000)

            command = run.call_args.args[0]
            self.assertEqual(command[0], str(node))
            self.assertEqual(command[1], str(script))
            self.assertIn("NOVALIST_FILE_TASK_LOADER", command[-1])
            client.cleanup()

    def test_modern_npx_wrapper_uses_npx_cli_instead_of_prefix_helper(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            shim = root / "npx.cmd"
            node = root / "node.exe"
            npm_bin = root / "node_modules" / "npm" / "bin"
            prefix_script = npm_bin / "npm-prefix.js"
            cli_script = npm_bin / "npx-cli.js"
            npm_bin.mkdir(parents=True)
            node.write_text("", encoding="utf-8")
            prefix_script.write_text("", encoding="utf-8")
            cli_script.write_text("", encoding="utf-8")
            shim.write_text(
                '@ECHO OFF\n'
                'SET "NPM_PREFIX_JS=%~dp0\\node_modules\\npm\\bin\\npm-prefix.js"\n'
                'SET "NPX_CLI_JS=%~dp0\\node_modules\\npm\\bin\\npx-cli.js"\n'
                '"%NODE_EXE%" "%NPX_CLI_JS%" %*\n',
                encoding="utf-8",
            )
            client = DSHClient(
                "npx.cmd",
                launcher_args=["--yes", "@deepseek-ai/dsh"],
                profile="headless",
            )
            client._file_transport_supported = True
            with patch(
                "core.dsh_client.shutil.which",
                side_effect=lambda name: str(shim) if name == "npx.cmd" else None,
            ):
                with patch(
                    "core.dsh_client.subprocess.run", side_effect=acknowledged_result
                ) as run:
                    client.generate("system", "multiline\nprompt")

            command = run.call_args.args[0]
            self.assertEqual(command[:2], [str(node), str(cli_script)])
            self.assertNotIn(str(prefix_script), command)
            self.assertNotIn("multiline", command[-1])
            self.assertIn("NOVALIST_FILE_TASK_LOADER", command[-1])
            client.cleanup()

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
            client = DSHClient(str(shim), profile="headless")
            client._file_transport_supported = True
            with patch(
                "core.dsh_client.subprocess.run", side_effect=acknowledged_result
            ) as run:
                client.generate("system", "short task")

            command = run.call_args.args[0]
            self.assertEqual(command[:2], [str(node), str(script)])
            self.assertIn("NOVALIST_FILE_TASK_LOADER", command[-1])
            client.cleanup()

    def test_empty_task_onboarding_is_reported_before_protocol_retry(self) -> None:
        completed = SimpleNamespace(
            returncode=0,
            stdout="I don't see an actual task in your message yet.\n",
            stderr="",
        )
        client = DSHClient("dsh")
        client._file_transport_supported = True
        with patch("core.dsh_client.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "没有收到 Novalist"):
                client.generate("system", "user")

    def test_connection_check_runs_a_real_probe(self) -> None:
        version = SimpleNamespace(returncode=0, stdout="0.1.0-rc.7\n", stderr="")
        client = DSHClient("dsh", timeout=30)

        def fake_run(command, **kwargs):
            if "--version" in command:
                return version
            return acknowledged_result(command, **kwargs)

        with patch("core.dsh_client.shutil.which", return_value="dsh.exe"):
            with patch("core.dsh_client.subprocess.run", side_effect=fake_run) as run:
                result = client.check_connection()

        self.assertIn("任务传递正常", result)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(run.call_args_list[0].args[0][-1], "--version")
        self.assertIn("NOVALIST_FILE_TASK_LOADER", run.call_args_list[1].args[0][-1])
        client.cleanup()

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
            nonce_head = re.search(
                r"^task_nonce_head: ([A-F0-9]+)$", payload, re.MULTILINE
            )
            nonce_middle = re.search(
                r"task_nonce_middle=([A-F0-9]+)", payload, re.MULTILINE
            )
            nonce_tail = re.search(
                r"^task_nonce_tail: ([A-F0-9]+)$", payload, re.MULTILINE
            )
            self.assertIsNotNone(nonce_head)
            self.assertIsNotNone(nonce_middle)
            self.assertIsNotNone(nonce_tail)
            marker = re.search(rf"{DSH_FILE_PROBE_PREFIX}[A-F0-9]+", payload)
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    f"{DSH_FILE_ACK_PREFIX}{nonce_head.group(1)}:"
                    f"{nonce_middle.group(1)}:"
                    f"{nonce_tail.group(1)}\n"
                    f"{marker.group(0) if marker else DSH_PROBE_MARKER}"
                ),
                stderr="",
            )

        client = DSHClient("dsh", timeout=30)
        client.use_isolated_workspace()
        with patch("core.dsh_client.shutil.which", return_value="dsh.exe"):
            with patch("core.dsh_client.subprocess.run", side_effect=fake_run) as run:
                result = client.check_connection()

        self.assertIn("argv 控制传输与 file 业务传输均可用", result)
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
        client._file_transport_supported = True
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
        client = DSHClient("dsh")
        client.use_isolated_workspace()
        client._file_transport_supported = True
        workspace = client.working_directory
        with patch("core.dsh_client.subprocess.run", side_effect=acknowledged_result) as run:
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
