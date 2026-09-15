"""DSH (DeepSeek Harness) client adapter.

The adapter keeps the `headless` profile's task-as-final-argument contract,
as shown in dsh's own help:

    dsh --profile headless "run the tests"

Every business prompt is stored in Novalist's isolated workspace after a live
file-read probe succeeds. The command line carries only a short loader task.
"""

from __future__ import annotations

import hashlib
import os
import re
import signal
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .context_budget import DEFAULT_PROMPT_BUDGET
from .context_report import PromptContextReport, ReportCallback
from .task_controller import AITaskCancelled
from .json_utils import JSONExtractionError, extract_json
from .storage import atomic_write_text
from .token_budget import (
    DEFAULT_INPUT_TOKEN_BUDGET,
    DEFAULT_RUNTIME_RESERVE_TOKENS,
    DEFAULT_TOKEN_ESTIMATOR,
    ConservativeTokenEstimator,
    TokenBudget,
)


WINDOWS_CMDLINE_LIMIT = 7800
WINDOWS_CREATEPROCESS_LIMIT = 30000
DSH_PROBE_MARKER = "NOVALIST_PROBE_OK"
DSH_FILE_PROBE_PREFIX = "NOVALIST_FILE_PROBE_"
DSH_FILE_READ_FAILED = "NOVALIST_FILE_TASK_READ_FAILED"
DSH_FILE_TASK_SCHEMA = "NOVALIST_TASK_FILE_V1"
DSH_FILE_ACK_PREFIX = "NOVALIST_FILE_ACK:"
DSH_FILE_ACK_SEARCH_LINES = 6
DEFAULT_TASK_FILE_MAX_BYTES = 512_000
EMPTY_TASK_HINTS = (
    "I don't see an actual task",
    "I don't see a specific task",
    "only the runtime context",
    "only the session startup context",
    "只收到运行时上下文",
    "没有看到实际任务",
)


@dataclass(frozen=True)
class _TaskFile:
    """One verified, short-lived file bridge artifact."""

    path: Path
    task_id: str
    nonce_head: str
    nonce_middle: str
    nonce_tail: str
    payload_sha256: str
    byte_count: int

    @property
    def expected_ack(self) -> str:
        return (
            f"{DSH_FILE_ACK_PREFIX}{self.nonce_head}:"
            f"{self.nonce_middle}:{self.nonce_tail}"
        )


class _TaskFileReceiptError(RuntimeError):
    """A redacted, retryable failure to prove that one task file was read."""

    def __init__(self, reason: str):
        self.reason = str(reason or "unknown")
        super().__init__(
            "DeepSeek Harness 的任务文件回执无效，"
            "无法确认业务提示词已跨段读取。"
        )


class DSHClient:
    def __init__(
        self,
        dsh_command: str = "dsh",
        launcher_args: list[str] | None = None,
        profile: str = "headless",
        timeout: int = 180,
        extra_args: list[str] | None = None,
        working_directory: str | Path | None = None,
        file_prompt_budget: int = 48_000,
        task_file_max_bytes: int = DEFAULT_TASK_FILE_MAX_BYTES,
        input_token_budget: int = DEFAULT_INPUT_TOKEN_BUDGET,
        runtime_reserve_tokens: int = DEFAULT_RUNTIME_RESERVE_TOKENS,
        model_context_window_tokens: int = 0,
        context_strategy: str = "balanced",
        token_estimator: ConservativeTokenEstimator = DEFAULT_TOKEN_ESTIMATOR,
        report_callback: ReportCallback | None = None,
    ):
        self.dsh_command = dsh_command
        self.launcher_args = launcher_args or []
        self.profile = profile
        self.timeout = timeout
        self.extra_args = extra_args or []
        self.working_directory = Path(working_directory) if working_directory else None
        try:
            normalized_budget = int(file_prompt_budget)
        except (TypeError, ValueError):
            normalized_budget = 48_000
        self.file_prompt_budget = max(DEFAULT_PROMPT_BUDGET, normalized_budget)
        try:
            normalized_file_bytes = int(task_file_max_bytes)
        except (TypeError, ValueError):
            normalized_file_bytes = DEFAULT_TASK_FILE_MAX_BYTES
        self.task_file_max_bytes = max(64_000, normalized_file_bytes)
        try:
            normalized_input_tokens = int(input_token_budget)
        except (TypeError, ValueError):
            normalized_input_tokens = DEFAULT_INPUT_TOKEN_BUDGET
        self.input_token_budget = max(1_000, normalized_input_tokens)
        try:
            normalized_runtime_reserve = int(runtime_reserve_tokens)
        except (TypeError, ValueError):
            normalized_runtime_reserve = DEFAULT_RUNTIME_RESERVE_TOKENS
        self.runtime_reserve_tokens = max(0, normalized_runtime_reserve)
        try:
            normalized_context_window = int(model_context_window_tokens)
        except (TypeError, ValueError):
            normalized_context_window = 0
        self.model_context_window_tokens = max(0, normalized_context_window)
        self.context_strategy = str(context_strategy or "balanced")
        self.token_budget = TokenBudget(
            input_limit=self.input_token_budget,
            runtime_reserve=self.runtime_reserve_tokens,
            model_context_window=self.model_context_window_tokens,
        )
        self.token_estimator = token_estimator
        self.report_callback = report_callback
        self._file_transport_supported: bool | None = None
        self._isolated_workspace: Path | None = None
        self._last_command_chars = 0

    def set_working_directory(self, directory: str | Path | None) -> None:
        self.working_directory = Path(directory) if directory else None

    def use_isolated_workspace(self) -> None:
        """Run dsh in a private empty directory instead of the user's project.

        Story context travels through one short-lived task file created in this
        directory. Keeping that bridge outside the novel project prevents the
        agent from seeing unrelated project files.
        """
        if self._isolated_workspace is None or not self._isolated_workspace.is_dir():
            self._isolated_workspace = Path(tempfile.mkdtemp(prefix="novalist-dsh-"))
        self.working_directory = self._isolated_workspace

    def cleanup(self) -> None:
        """Best-effort removal of the isolated workspace."""
        workspace = self._isolated_workspace
        self._isolated_workspace = None
        if workspace is not None:
            if self.working_directory == workspace:
                self.working_directory = None
            # Windows can retain a just-exited child process' cwd handle for a
            # short interval.  Retry the exact private workspace instead of
            # silently leaving an empty directory after one transient denial.
            for delay in (0.0, 0.05, 0.15, 0.3, 0.6, 1.0):
                if delay:
                    time.sleep(delay)
                try:
                    shutil.rmtree(workspace)
                except FileNotFoundError:
                    break
                except OSError:
                    continue
                break

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        session_id: str | None = None,
        *,
        timeout_override: int | None = None,
        cancel_event: threading.Event | None = None,
        context_report: PromptContextReport | None = None,
    ) -> str:
        """Run dsh with argv as control plane and one task file as data plane."""
        combined = self._combine_prompts(system_prompt, user_prompt)
        estimated_input_tokens = max(
            self.token_estimator.estimate_pair(system_prompt, user_prompt),
            # Measure the exact wrapper sent to DSH as well.  The fixed role
            # allowance covers Harness framing that is not visible here.
            self.token_estimator.estimate(combined) + 32,
        )
        effective_timeout = max(
            1,
            int(timeout_override if timeout_override is not None else self.timeout),
        )
        task_file: _TaskFile | None = None
        transport = "file"
        file_ack_verified = False
        file_ack_retry_count = 0
        file_ack_error = ""
        outcome = "failed"
        self._last_command_chars = 0
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise AITaskCancelled()
            if not self.token_budget.accepts(estimated_input_tokens):
                raise RuntimeError(
                    "Novalist 业务提示词超过输入 token 安全上限"
                    f"（估算 {estimated_input_tokens} > {self.input_token_budget}）。"
                    "请缩减上下文、降低分块大小或先执行摘要后重试。"
                )
            if not self._ensure_file_transport_support(
                timeout=min(effective_timeout, 30),
                cancel_event=cancel_event,
            ):
                transport = "file_unavailable"
                raise RuntimeError(
                    "DeepSeek Harness 无法验证读取 Novalist 的临时任务文件。"
                    "为避免提示词截断，本次任务已停止；请检查 headless 的文件读取能力。"
                )
            task_file = self._write_task_file(combined)
            transmitted_prompt = self._file_loader_prompt(task_file.path.name)
            self._last_command_chars = 0
            result = self._execute_prompt(
                transmitted_prompt,
                session_id=session_id,
                timeout=effective_timeout,
                cancel_event=cancel_event,
                submitted_prompt_length=len(combined),
            )
            if task_file is not None:
                try:
                    result = self._validate_file_response(result, task_file)
                except _TaskFileReceiptError as first_error:
                    # Headless exposes only model-authored final text, not a
                    # native transport receipt. Reuse the exact task file and
                    # nonce challenge for one bounded formatting retry.
                    file_ack_retry_count = 1
                    file_ack_error = first_error.reason
                    result = self._execute_prompt(
                        self._file_loader_prompt(task_file.path.name, retry=True),
                        session_id=session_id,
                        timeout=effective_timeout,
                        cancel_event=cancel_event,
                        submitted_prompt_length=len(combined),
                    )
                    try:
                        result = self._validate_file_response(result, task_file)
                    except _TaskFileReceiptError as retry_error:
                        file_ack_error = retry_error.reason
                        raise RuntimeError(
                            "DeepSeek Harness 的任务文件回执无效，"
                            "自动重试一次后仍无法确认业务提示词已跨段读取。"
                        ) from None
                file_ack_verified = True
            outcome = "success"
            return result
        except AITaskCancelled:
            outcome = "cancelled"
            raise
        except Exception as exc:
            outcome = "timeout" if "超时" in str(exc) else "failed"
            raise
        finally:
            self._remove_task_file(task_file)
            task_file_cleaned = task_file is None or not task_file.path.exists()
            if context_report is not None:
                completed_report = context_report.complete_invocation(
                    transport=transport,
                    submitted_prompt_chars=len(combined),
                    command_chars=self._last_command_chars,
                    outcome=outcome,
                    task_file_cleaned=task_file_cleaned,
                    task_file_bytes=task_file.byte_count if task_file is not None else 0,
                    file_ack_verified=file_ack_verified,
                    file_ack_retry_count=file_ack_retry_count,
                    file_ack_error=file_ack_error,
                    input_token_budget=self.input_token_budget,
                    runtime_reserve_tokens=self.runtime_reserve_tokens,
                    model_context_window_tokens=self.model_context_window_tokens,
                    context_strategy=self.context_strategy,
                    estimated_input_tokens=estimated_input_tokens,
                    token_estimator=self.token_estimator.name,
                )
                self._publish_context_report(completed_report)

    def prompt_build_budget(self) -> int:
        """Return a conservative character budget below the token hard limit."""
        token_headroom = max(1_000, self.input_token_budget - 512)
        safe_chars = int(token_headroom / self.token_estimator.safety_factor)
        return max(1_000, min(self.file_prompt_budget, safe_chars))

    def _execute_prompt(
        self,
        prompt: str,
        *,
        session_id: str | None,
        timeout: int,
        cancel_event: threading.Event | None,
        submitted_prompt_length: int,
    ) -> str:
        command = self._resolve_command()
        cmd = [command, *self.launcher_args, "--profile", self.profile]
        cmd += self.extra_args
        if session_id:
            cmd += ["--resume", session_id]
        cmd.append(self._serialize_prompt_argument(prompt))
        cmd = self._prepare_command_for_prompt(cmd, command)
        self._last_command_chars = len(subprocess.list2cmdline(cmd))

        if cancel_event is not None:
            return self._generate_cancellable(
                cmd,
                timeout,
                cancel_event,
                submitted_prompt_length,
            )

        try:
            result = subprocess.run(
                cmd,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=timeout,
                cwd=str(self.working_directory) if self.working_directory else None,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "找不到 AI 引擎命令。请确认 DeepSeek Harness 已安装，"
                "或在偏好设置中修改命令与启动参数。"
            ) from None
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"dsh 调用超时（>{timeout} 秒）。") from None

        if result.returncode != 0:
            raise RuntimeError(
                "dsh 调用失败。\n"
                f"命令: {' '.join(cmd[:-1])} ...\n"
                f"stderr: {result.stderr.strip()}"
            )

        output = result.stdout.strip()
        if not output:
            raise RuntimeError("dsh 返回了空内容。")
        if self._looks_like_empty_task(output):
            raise RuntimeError(
                "dsh 已启动，但没有收到 Novalist 的实际任务参数。"
                f"（入口：{command}；提示词长度：{submitted_prompt_length}）"
            )
        return output

    def _generate_cancellable(
        self,
        cmd: list[str],
        timeout: int,
        cancel_event: threading.Event,
        submitted_prompt_length: int,
    ) -> str:
        if cancel_event.is_set():
            raise AITaskCancelled()
        try:
            process_options: dict[str, Any] = {
                "text": True,
                "encoding": "utf-8",
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "cwd": str(self.working_directory) if self.working_directory else None,
            }
            if os.name == "nt":
                # Keep a process-group handle available for the Windows tree
                # termination fallback.  taskkill below handles descendants.
                process_options["creationflags"] = getattr(
                    subprocess, "CREATE_NEW_PROCESS_GROUP", 0
                )
            else:
                # dsh may launch a node/launcher child; kill the whole group on
                # cancellation instead of leaving descendants behind.
                process_options["start_new_session"] = True
            process = subprocess.Popen(
                cmd,
                **process_options,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "找不到 AI 引擎命令。请确认 DeepSeek Harness 已安装，"
                "或在偏好设置中修改命令与启动参数。"
            ) from None

        deadline = time.monotonic() + timeout
        try:
            while True:
                if cancel_event.is_set():
                    self._terminate_process_tree(process)
                    raise AITaskCancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._terminate_process_tree(process)
                    raise RuntimeError(f"dsh 调用超时（>{timeout} 秒）。")
                try:
                    stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None and cancel_event.is_set():
                self._terminate_process_tree(process)

        if process.returncode != 0:
            raise RuntimeError(
                "dsh 调用失败。\n"
                f"命令: {' '.join(cmd[:-1])} ...\n"
                f"stderr: {stderr.strip()}"
            )
        output = stdout.strip()
        if not output:
            raise RuntimeError("dsh 返回了空内容。")
        if self._looks_like_empty_task(output):
            raise RuntimeError(
                "dsh 已启动，但没有收到 Novalist 的实际任务参数。"
                f"（入口：{cmd[0]}；提示词长度：{submitted_prompt_length}）"
            )
        return output

    @staticmethod
    def _terminate_process_tree(process) -> None:
        """Terminate dsh and descendants, then reap pipes without hanging."""
        pid = getattr(process, "pid", None)
        if pid:
            if os.name == "nt":
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
            else:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass

        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass

        try:
            process.communicate(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            # A broken/escaped descendant must not leave the worker blocked
            # forever while trying to drain inherited stdout/stderr handles.
            try:
                process.kill()
            except (OSError, ProcessLookupError):
                pass
            try:
                process.communicate(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass

    @staticmethod
    def _serialize_prompt_argument(prompt: str) -> str:
        """Keep one logical prompt inside one Windows process argument.

        npm/npx can forward package binaries through nested ``.cmd`` shims.
        Literal CR/LF characters are command separators at that layer even
        when Python originally supplied a single argv item.  U+2028 remains
        one Unicode character through the launch chain while preserving a
        line boundary for the model.  POSIX argv supports literal newlines.
        """
        value = str(prompt)
        if os.name != "nt":
            return value
        return value.replace("\r\n", "\n").replace("\r", "\n").replace(
            "\n", "\u2028"
        )

    def _ensure_file_transport_support(
        self,
        *,
        timeout: int,
        cancel_event: threading.Event | None,
    ) -> bool:
        """Probe once whether this headless composition can read a task file."""
        if self._file_transport_supported is True:
            return True

        marker = DSH_FILE_PROBE_PREFIX + uuid.uuid4().hex.upper()
        probe_payload = (
            "NOVALIST_TASK_START\n"
            "这是 Novalist 的本地任务文件传输测试。\n"
            f"请只回复这一行标记：{marker}\n"
            "NOVALIST_TASK_END\n"
        )
        task_file = self._write_task_file(probe_payload)
        try:
            raw_output = self._execute_prompt(
                self._file_loader_prompt(task_file.path.name),
                session_id=None,
                timeout=max(1, int(timeout)),
                cancel_event=cancel_event,
                submitted_prompt_length=len(probe_payload),
            )
            try:
                output = self._validate_file_response(raw_output, task_file)
            except RuntimeError:
                supported = False
            else:
                supported = marker in output
                if supported:
                    self._file_transport_supported = True
        finally:
            self._remove_task_file(task_file)
        return supported

    def _write_task_file(self, prompt: str) -> _TaskFile:
        """Write and re-read one authenticated UTF-8 task file."""
        if self._isolated_workspace is None or not self._isolated_workspace.is_dir():
            self.use_isolated_workspace()
        workspace = self._isolated_workspace
        if workspace is None:
            raise RuntimeError("无法创建 Novalist 的隔离 AI 工作目录。")
        self.working_directory = workspace
        task_id = uuid.uuid4().hex
        nonce_head = uuid.uuid4().hex.upper()
        nonce_middle = uuid.uuid4().hex.upper()
        nonce_tail = uuid.uuid4().hex.upper()
        payload = str(prompt)
        challenged_payload = self._inject_middle_challenge(payload, nonce_middle)
        payload_bytes = challenged_payload.encode("utf-8")
        payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
        envelope = (
            f"{DSH_FILE_TASK_SCHEMA}\n"
            f"task_id: {task_id}\n"
            "encoding: UTF-8\n"
            f"payload_chars: {len(challenged_payload)}\n"
            f"payload_bytes: {len(payload_bytes)}\n"
            f"payload_sha256: {payload_sha256}\n"
            f"task_nonce_head: {nonce_head}\n\n"
            "[传输协议]\n"
            "- 必须完整读取本文件后再执行任务。\n"
            "- 任务正文中部和文件末尾还有随机校验值。最终回复第一行必须严格使用格式：\n"
            f"  {DSH_FILE_ACK_PREFIX}<task_nonce_head>:<task_nonce_middle>:<task_nonce_tail>\n"
            "- 业务任务中的‘只输出 JSON’、‘只输出正文’或类似要求，只约束回执后的"
            "业务结果；传输回执始终是第一行，业务结果始终从第二行开始。\n"
            "- NOVALIST_TRANSPORT_CHECKPOINT 仅用于传输校验，不属于任务正文。\n"
            "- 从第二行开始输出任务要求的结果，不要重复或解释传输协议。\n"
            f"- 如果无法完整读取文件，只回复 {DSH_FILE_READ_FAILED}。\n\n"
            f"{challenged_payload}\n"
            "NOVALIST_TASK_FILE_FOOTER\n"
            "再次确认：无论业务输出格式如何，第一行先输出三段 nonce 回执，"
            "第二行起再输出业务结果。\n"
            f"task_nonce_tail: {nonce_tail}"
        )
        encoded = envelope.encode("utf-8")
        if len(encoded) > self.task_file_max_bytes:
            raise RuntimeError(
                "Novalist 任务文件超过安全上限"
                f"（{len(encoded)} > {self.task_file_max_bytes} 字节）。"
                "请缩减任务上下文后重试。"
            )
        path = workspace / f".novalist-task-{task_id}.md"
        atomic_write_text(path, envelope, encoding="utf-8")
        try:
            persisted = path.read_bytes()
        except OSError as exc:
            path.unlink(missing_ok=True)
            raise RuntimeError("Novalist 无法回读临时任务文件。") from exc
        if persisted != encoded:
            path.unlink(missing_ok=True)
            raise RuntimeError("Novalist 临时任务文件写入校验失败。")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return _TaskFile(
            path,
            task_id,
            nonce_head,
            nonce_middle,
            nonce_tail,
            payload_sha256,
            len(encoded),
        )

    @staticmethod
    def _inject_middle_challenge(payload: str, nonce_middle: str) -> str:
        """Place an out-of-band read challenge near the payload midpoint."""
        value = str(payload)
        midpoint = len(value) // 2
        before = value.rfind("\n", 0, midpoint)
        after = value.find("\n", midpoint)
        candidates = [index for index in (before, after) if index >= 0]
        split_at = (
            min(candidates, key=lambda index: abs(index - midpoint))
            if candidates
            else midpoint
        )
        checkpoint = (
            "\nNOVALIST_TRANSPORT_CHECKPOINT: "
            f"task_nonce_middle={nonce_middle}\n"
        )
        return value[:split_at] + checkpoint + value[split_at:]

    def _remove_task_file(self, task_file: _TaskFile | None) -> None:
        if task_file is None:
            return
        path = task_file.path
        workspace = self._isolated_workspace
        try:
            if workspace is None or path.parent.resolve() != workspace.resolve():
                return
            path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _file_loader_prompt(filename: str, *, retry: bool = False) -> str:
        retry_notice = (
            "上一次回复未提供可验证的三段 nonce 回执。请重新完整读取同一个文件，"
            "不要复用或猜测上一次结果。\n"
            if retry
            else ""
        )
        return (
            "NOVALIST_FILE_TASK_LOADER\n"
            f"{retry_notice}"
            f"请完整读取当前工作目录中的 {filename}（UTF-8）。\n"
            "该文件中 NOVALIST_TASK_START 与 NOVALIST_TASK_END 之间的内容"
            "才是本次完整任务。请立即执行该任务，不要仅概括文件内容，也不要"
            "修改或删除任何文件。最终回复必须遵守文件头部的传输协议，并从文件"
            "头、任务正文中部和文件尾分别取得 nonce；加载指令本身不包含这些值。"
            "文件内的‘只输出 JSON/正文’仅约束回执后的业务结果，不得省略第一行回执。\n"
            f"如果无法完整读取，只回复 {DSH_FILE_READ_FAILED}。"
        )

    @staticmethod
    def _validate_file_response(output: str, task_file: _TaskFile) -> str:
        """Accept output only when it proves the exact task file was read."""
        normalized = str(output or "").replace("\r\n", "\n").replace("\r", "\n")
        normalized = normalized.strip().lstrip("\ufeff")
        if normalized == DSH_FILE_READ_FAILED or normalized.startswith(
            DSH_FILE_READ_FAILED + "\n"
        ):
            raise _TaskFileReceiptError("file_read_failed")

        lines = normalized.split("\n") if normalized else []
        if (
            len(lines) >= 2
            and lines[0].strip().startswith("```")
            and lines[-1].strip() == "```"
        ):
            lines = lines[1:-1]

        expected = task_file.expected_ack
        matching_lines = [
            index for index, line in enumerate(lines) if line.strip() == expected
        ]
        if len(matching_lines) > 1:
            raise _TaskFileReceiptError("duplicate_ack")
        if not matching_lines:
            reason = (
                "wrong_nonce"
                if any(line.strip().startswith(DSH_FILE_ACK_PREFIX) for line in lines)
                else "missing_ack"
            )
            raise _TaskFileReceiptError(reason)
        ack_index = matching_lines[0]
        if ack_index >= DSH_FILE_ACK_SEARCH_LINES:
            raise _TaskFileReceiptError("late_ack")

        # The exact unpredictable nonce triple is the integrity proof. Ignore
        # a small model-authored preamble instead of confusing line position
        # with whether the file was read.
        result = "\n".join(lines[ack_index + 1 :]).strip()
        if not result:
            raise RuntimeError("DeepSeek Harness 已确认任务文件，但返回结果为空。")
        return result

    def _prepare_command_for_prompt(self, cmd: list[str], resolved_command: str) -> list[str]:
        """Avoid the ~8K ``cmd.exe`` limit used by Windows npm shims.

        DSH's headless contract currently accepts the task as positional
        arguments, so the prompt cannot be moved to stdin without changing
        the external CLI contract.  Whenever a readable ``.CMD`` shim is
        available, invoke its JavaScript entry point directly.  This avoids
        both the ``cmd.exe`` argument parser and its ~8K limit, while keeping
        the same DSH argument order for short and long prompts.
        """
        length = len(subprocess.list2cmdline(cmd))
        if not resolved_command.lower().endswith((".cmd", ".bat")):
            if os.name == "nt" and length > WINDOWS_CREATEPROCESS_LIMIT:
                raise RuntimeError(
                    f"dsh 提示词过长（命令约 {length} 个字符），已超过 Windows 进程限制。"
                    "请减少章节设定或剧情简写后重试。"
                )
            return cmd
        direct = self._resolve_windows_shim(resolved_command)
        if direct:
            node, script = direct
            direct_cmd = [node, script, *cmd[1:]]
            direct_length = len(subprocess.list2cmdline(direct_cmd))
            if direct_length <= WINDOWS_CREATEPROCESS_LIMIT:
                return direct_cmd
            length = direct_length

        if length <= WINDOWS_CMDLINE_LIMIT:
            return cmd

        raise RuntimeError(
            f"dsh 提示词过长（命令约 {length} 个字符），Windows 无法启动该任务。"
            "请减少章节设定或剧情简写后重试。"
        )

    @staticmethod
    def _resolve_windows_shim(command: str) -> tuple[str, str] | None:
        """Resolve a Windows npm shim to ``node`` plus its JavaScript entry.

        npm has emitted several equivalent ``.cmd`` templates over time.  In
        particular, some use ``%_prog%`` while the normal npm template uses
        ``%~dp0\\node.exe``.  Reading only one template makes long prompts
        fall back to ``cmd.exe`` and can silently lose the final task
        argument, so accept both forms and locate the first JavaScript entry
        referenced by the shim.
        """
        path = Path(command)
        try:
            shim = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

        script_refs = re.findall(r'"([^"\r\n]+\.js)"', shim, re.IGNORECASE)
        script_refs += re.findall(
            r'(?<!["\w])([^\s"\r\n]+\.js)(?=["\s]|$)',
            shim,
            re.IGNORECASE,
        )
        scripts: list[Path] = []
        for script_ref in script_refs:
            script_ref = script_ref.strip()
            # Support both the conventional ``%~dp0`` form and the older
            # ``%dp0%`` spelling used by some launcher generators.
            script_ref = script_ref.replace("%~dp0%", str(path.parent))
            script_ref = script_ref.replace("%~dp0", str(path.parent) + "\\")
            script_ref = script_ref.replace("%dp0%", str(path.parent))
            candidate = Path(script_ref)
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            if candidate.is_file():
                scripts.append(candidate)
        if not scripts:
            return None
        # Modern npm/npx wrappers mention a helper such as npm-prefix.js
        # before the actual CLI. Prefer an executable-looking CLI entry so
        # bypassing the .cmd layer preserves argument forwarding.
        script = min(
            scripts,
            key=lambda item: (
                0 if "cli" in item.stem.casefold() else 1,
                1 if "prefix" in item.stem.casefold() else 0,
                scripts.index(item),
            ),
        )

        node_path = path.parent / "node.exe"
        node = str(node_path) if node_path.is_file() else shutil.which("node")
        if not node:
            return None
        return str(node), str(script)

    def check_connection(
        self,
        cancel_event: threading.Event | None = None,
    ) -> str:
        """Check startup and verify that a real task reaches headless DSH.

        ``--version`` only proves that the executable can start.  It does not
        catch Windows command-line truncation, which is exactly the failure
        mode that can leave DSH running with an empty task.  Keep the version
        check for a useful diagnostic, then send a tiny marker task through
        the same path used by normal generation.
        """
        executable = self.dsh_command
        if cancel_event is not None and cancel_event.is_set():
            raise AITaskCancelled()
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
        try:
            probe = self.generate(
                "",
                f"这是 Novalist 的连接测试。请只回复 {DSH_PROBE_MARKER}。",
                timeout_override=min(self.timeout, 15),
                cancel_event=cancel_event,
            )
        except RuntimeError as exc:
            raise RuntimeError(
                "dsh 可以启动，但实际任务探测失败。请检查任务参数传递，"
                f"而不是只检查版本号。\n{exc}"
            ) from exc
        if DSH_PROBE_MARKER not in probe:
            preview = probe[:500].replace("\r", " ").replace("\n", " ")
            raise RuntimeError(
                "dsh 可以启动，但没有按要求返回任务探测标记，"
                "可能存在任务参数丢失或 headless 配置不匹配。\n"
                f"原始返回：{preview}"
            )
        file_status = "argv 控制传输与 file 业务传输均可用"
        return (
            f"dsh 可用，任务传递正常，{file_status}（{version}）"
            if version
            else f"dsh 可用，任务传递正常，{file_status}"
        )

    def _resolve_command(self) -> str:
        """Resolve npm command shims before passing them to CreateProcess."""
        command = str(self.dsh_command or "dsh").strip()
        if Path(command).suffix.lower() in {".cmd", ".bat", ".com", ".exe"}:
            if Path(command).is_absolute():
                return command
            return shutil.which(command) or command
        resolved = shutil.which(command)
        return resolved or command

    @staticmethod
    def _looks_like_empty_task(output: str) -> bool:
        """Recognize DSH's onboarding response for a missing task argument."""
        normalized = str(output or "")
        return any(hint in normalized for hint in EMPTY_TASK_HINTS)

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        session_id: str | None = None,
        *,
        timeout_override: int | None = None,
        cancel_event: threading.Event | None = None,
        context_report: PromptContextReport | None = None,
    ) -> Any:
        """Ask dsh for JSON and parse it safely."""
        text = self.generate(
            system_prompt,
            user_prompt,
            session_id,
            timeout_override=timeout_override,
            cancel_event=cancel_event,
            context_report=context_report,
        )
        return self._extract_json(text)

    def _publish_context_report(self, report: PromptContextReport) -> None:
        """Publish diagnostics without allowing display failures to break AI work."""
        callback = self.report_callback
        if callback is None:
            return
        try:
            callback(report)
        except Exception:
            pass

    def _combine_prompts(self, system_prompt: str, user_prompt: str) -> str:
        system_prompt = (system_prompt or "").strip()
        user_prompt = (user_prompt or "").strip()
        if not system_prompt:
            return (
                "NOVALIST_TASK_START\n"
                "请立即执行下面这一个任务，并在本次回复中给出最终结果。"
                "不要停留在准备状态，也不要询问用户下一步。\n\n"
                "[输出包装说明]\n"
                "任务中的‘只输出 JSON’、‘只输出正文’或类似限制，只约束传输回执后的"
                "业务结果；必须先按任务文件传输协议输出第一行 ACK。\n\n"
                f"[用户任务]\n{user_prompt}\n"
                "NOVALIST_TASK_END"
            )
        return (
            "NOVALIST_TASK_START\n"
            "请立即执行下面这一个任务，并在本次回复中给出最终结果。"
            "不要停留在准备状态，也不要询问用户下一步。\n\n"
            "[输出包装说明]\n"
            "任务中的‘只输出 JSON’、‘只输出正文’或类似限制，只约束传输回执后的"
            "业务结果；必须先按任务文件传输协议输出第一行 ACK。\n\n"
            f"[系统约束]\n{system_prompt}\n\n"
            f"[用户任务]\n{user_prompt}\n"
            "NOVALIST_TASK_END"
        )

    @staticmethod
    def _extract_json(text: str) -> Any:
        """Extract a JSON object/array from dsh output, tolerating code fences."""
        try:
            return extract_json(text)
        except JSONExtractionError as exc:
            raise RuntimeError(f"dsh 返回内容不是合法 JSON：\n{str(text)[:500]}") from exc
