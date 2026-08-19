"""DSH (DeepSeek Harness) client adapter.

The current adapter assumes the `headless` profile accepts a prompt as its
final argument, as shown in dsh's own help:

    dsh --profile headless "run the tests"

If your local dsh supports a different way to pass a system prompt / session,
adjust only this file.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any


class DSHClient:
    def __init__(
        self,
        dsh_command: str = "dsh",
        launcher_args: list[str] | None = None,
        profile: str = "headless",
        timeout: int = 180,
        extra_args: list[str] | None = None,
    ):
        self.dsh_command = dsh_command
        self.launcher_args = launcher_args or []
        self.profile = profile
        self.timeout = timeout
        self.extra_args = extra_args or []

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        session_id: str | None = None,
    ) -> str:
        """Run dsh headless with a combined single prompt."""
        combined = self._combine_prompts(system_prompt, user_prompt)
        cmd = [self.dsh_command, *self.launcher_args, "--profile", self.profile]
        cmd += self.extra_args
        if session_id:
            cmd += ["--resume", session_id]
        cmd.append(combined)

        try:
            result = subprocess.run(
                cmd,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=self.timeout,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "找不到 AI 引擎命令。请确认 DeepSeek Harness 已安装，"
                "或在偏好设置中修改命令与启动参数。"
            ) from None
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"dsh 调用超时（>{self.timeout} 秒）。") from None

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

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        session_id: str | None = None,
    ) -> Any:
        """Ask dsh for JSON and parse it safely."""
        text = self.generate(system_prompt, user_prompt, session_id)
        return self._extract_json(text)

    def _combine_prompts(self, system_prompt: str, user_prompt: str) -> str:
        system_prompt = (system_prompt or "").strip()
        user_prompt = (user_prompt or "").strip()
        if not system_prompt:
            return user_prompt
        return f"[系统设定]\n{system_prompt}\n\n[用户任务]\n{user_prompt}"

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
            # Try to find the outermost {...} or [...] block.
            for start, end in (("```json", "```"), ("```", "```")):
                if start in text and end in text:
                    candidate = text.split(start, 1)[1].rsplit(end, 1)[0].strip()
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        continue
            raise RuntimeError(f"dsh 返回内容不是合法 JSON：\n{text[:500]}")
