"""DSH (DeepSeek Harness) client adapter.

The current adapter assumes the `headless` profile accepts a prompt as its
final argument, as shown in dsh's own help:

    dsh --profile headless "run the tests"

If your local dsh supports a different way to pass a system prompt / session,
adjust only this file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


WINDOWS_CMDLINE_LIMIT = 7800
WINDOWS_CREATEPROCESS_LIMIT = 30000


class DSHClient:
    def __init__(
        self,
        dsh_command: str = "dsh",
        launcher_args: list[str] | None = None,
        profile: str = "headless",
        timeout: int = 180,
        extra_args: list[str] | None = None,
        working_directory: str | Path | None = None,
    ):
        self.dsh_command = dsh_command
        self.launcher_args = launcher_args or []
        self.profile = profile
        self.timeout = timeout
        self.extra_args = extra_args or []
        self.working_directory = Path(working_directory) if working_directory else None

    def set_working_directory(self, directory: str | Path | None) -> None:
        self.working_directory = Path(directory) if directory else None

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        session_id: str | None = None,
        *,
        timeout_override: int | None = None,
    ) -> str:
        """Run dsh headless with a combined single prompt."""
        combined = self._combine_prompts(system_prompt, user_prompt)
        command = self._resolve_command()
        effective_timeout = max(
            1,
            int(timeout_override if timeout_override is not None else self.timeout),
        )
        cmd = [command, *self.launcher_args, "--profile", self.profile]
        cmd += self.extra_args
        if session_id:
            cmd += ["--resume", session_id]
        cmd.append(combined)
        cmd = self._prepare_command_for_prompt(cmd, command)

        try:
            result = subprocess.run(
                cmd,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=effective_timeout,
                cwd=str(self.working_directory) if self.working_directory else None,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "找不到 AI 引擎命令。请确认 DeepSeek Harness 已安装，"
                "或在偏好设置中修改命令与启动参数。"
            ) from None
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"dsh 调用超时（>{effective_timeout} 秒）。") from None

        if result.returncode != 0:
            raise RuntimeError(
                "dsh 调用失败。\n"
                f"命令: {' '.join(cmd[:-1])} ...\n"
                f"stderr: {result.stderr.strip()}"
            )

        output = result.stdout.strip()
        if not output:
            raise RuntimeError("dsh 返回了空内容。")
        return output

    def _prepare_command_for_prompt(self, cmd: list[str], resolved_command: str) -> list[str]:
        """Avoid the ~8K ``cmd.exe`` limit used by Windows npm shims.

        DSh's headless contract currently accepts the task as positional
        arguments, so the prompt cannot be moved to stdin without changing
        the external CLI contract.  For a long prompt, invoke the JavaScript
        entry point behind a readable ``.CMD`` shim directly; this raises the
        practical limit to CreateProcess's limit while preserving DSh args.
        """
        length = len(subprocess.list2cmdline(cmd))
        if not resolved_command.lower().endswith((".cmd", ".bat")):
            if os.name == "nt" and length > WINDOWS_CREATEPROCESS_LIMIT:
                raise RuntimeError(
                    f"dsh 提示词过长（命令约 {length} 个字符），已超过 Windows 进程限制。"
                    "请减少章节设定或剧情简写后重试。"
                )
            return cmd
        if length <= WINDOWS_CMDLINE_LIMIT:
            return cmd

        direct = self._resolve_windows_shim(resolved_command)
        if direct:
            node, script = direct
            direct_cmd = [node, script, *cmd[1:]]
            direct_length = len(subprocess.list2cmdline(direct_cmd))
            if direct_length <= WINDOWS_CREATEPROCESS_LIMIT:
                return direct_cmd
            length = direct_length

        raise RuntimeError(
            f"dsh 提示词过长（命令约 {length} 个字符），Windows 无法启动该任务。"
            "请减少章节设定或剧情简写后重试。"
        )

    @staticmethod
    def _resolve_windows_shim(command: str) -> tuple[str, str] | None:
        """Resolve an npm ``.CMD`` shim to ``node`` plus its JavaScript entry."""
        path = Path(command)
        try:
            shim = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

        match = re.search(r'"%_prog%"\s+"([^"]+\.js)"', shim, re.IGNORECASE)
        if not match:
            return None
        script_ref = match.group(1).replace("%dp0%", str(path.parent))
        script = Path(script_ref)
        if not script.is_absolute():
            script = path.parent / script
        if not script.is_file():
            return None

        node_path = path.parent / "node.exe"
        node = str(node_path) if node_path.is_file() else shutil.which("node")
        if not node:
            return None
        return str(node), str(script)

    def check_connection(self) -> str:
        """Check that the configured Harness command can start successfully."""
        executable = self.dsh_command
        if not Path(executable).is_absolute() and shutil.which(executable) is None:
            # npx.cmd and shell aliases are resolved by the subprocess layer on
            # Windows only when present on PATH; report the same actionable error.
            raise RuntimeError(f"找不到命令：{executable}")
        command = self._resolve_command()
        cmd = [
            command,
            *self.launcher_args,
            "--version",
        ]
        try:
            result = subprocess.run(
                cmd,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=min(self.timeout, 15),
                cwd=str(self.working_directory) if self.working_directory else None,
            )
        except FileNotFoundError:
            raise RuntimeError(f"找不到命令：{self.dsh_command}") from None
        except subprocess.TimeoutExpired:
            raise RuntimeError("dsh 帮助命令超时，请检查 DeepSeek Harness 安装状态。") from None
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"dsh 无法启动。{detail}")
        version = result.stdout.strip() or result.stderr.strip()
        return f"dsh 命令可用（{version}）" if version else "dsh 命令可用"

    def _resolve_command(self) -> str:
        """Resolve npm command shims before passing them to CreateProcess."""
        command = str(self.dsh_command or "dsh").strip()
        if Path(command).suffix.lower() in {".cmd", ".bat", ".com", ".exe"}:
            return command
        resolved = shutil.which(command)
        return resolved or command

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        session_id: str | None = None,
        *,
        timeout_override: int | None = None,
    ) -> Any:
        """Ask dsh for JSON and parse it safely."""
        text = self.generate(
            system_prompt,
            user_prompt,
            session_id,
            timeout_override=timeout_override,
        )
        return self._extract_json(text)

    def _combine_prompts(self, system_prompt: str, user_prompt: str) -> str:
        system_prompt = (system_prompt or "").strip()
        user_prompt = (user_prompt or "").strip()
        if not system_prompt:
            return (
                "NOVALIST_TASK_START\n"
                "请立即执行下面这一个任务，并在本次回复中给出最终结果。"
                "不要停留在准备状态，也不要询问用户下一步。\n\n"
                f"[用户任务]\n{user_prompt}\n"
                "NOVALIST_TASK_END"
            )
        return (
            "NOVALIST_TASK_START\n"
            "请立即执行下面这一个任务，并在本次回复中给出最终结果。"
            "不要停留在准备状态，也不要询问用户下一步。\n\n"
            f"[系统约束]\n{system_prompt}\n\n"
            f"[用户任务]\n{user_prompt}\n"
            "NOVALIST_TASK_END"
        )

    @staticmethod
    def _extract_json(text: str) -> Any:
        """Extract a JSON object/array from dsh output, tolerating code fences."""
        text = text.strip()
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence_match:
            text = fence_match.group(1).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Harness may add a short preamble around an otherwise valid JSON
            # object. JSONDecoder.raw_decode lets us extract the first complete
            # object/array without guessing where nested braces end.
            decoder = json.JSONDecoder()
            for index, character in enumerate(text):
                if character not in "[{":
                    continue
                try:
                    value, _end = decoder.raw_decode(text[index:])
                except json.JSONDecodeError:
                    continue
                return value
            raise RuntimeError(f"dsh 返回内容不是合法 JSON：\n{text[:500]}")
