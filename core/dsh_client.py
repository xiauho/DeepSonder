"""DSH (DeepSeek Harness) client adapter.

The adapter keeps the `headless` profile's prompt-as-final-argument contract,
as shown in dsh's own help:

    dsh --profile headless "run the tests"

Long prompts can be stored in Novalist's isolated workspace after a live file
read probe succeeds; only the short loader task remains on the command line.
"""

from __future__ import annotations

import os
import re
import signal
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .context_budget import ARGV_PROMPT_BUDGET
from .context_report import PromptContextReport, ReportCallback
from .task_controller import AITaskCancelled
from .json_utils import JSONExtractionError, extract_json
from .storage import atomic_write_text


WINDOWS_CMDLINE_LIMIT = 7800
WINDOWS_CREATEPROCESS_LIMIT = 30000
FILE_TRANSPORT_SWITCH_LIMIT = 20000
DSH_PROBE_MARKER = "NOVALIST_PROBE_OK"
DSH_FILE_PROBE_PREFIX = "NOVALIST_FILE_PROBE_"
DSH_FILE_READ_FAILED = "NOVALIST_FILE_TASK_READ_FAILED"
EMPTY_TASK_HINTS = (
    "I don't see an actual task",
    "I don't see a specific task",
    "only the runtime context",
    "only the session startup context",
    "只收到运行时上下文",
    "没有看到实际任务",
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
        prompt_transport: str = "argv",
        file_prompt_budget: int = 48_000,
        report_callback: ReportCallback | None = None,
    ):
        self.dsh_command = dsh_command
        self.launcher_args = launcher_args or []
        self.profile = profile
        self.timeout = timeout
        self.extra_args = extra_args or []
        self.working_directory = Path(working_directory) if working_directory else None
        normalized_transport = str(prompt_transport or "argv").strip().lower()
        self.prompt_transport = (
            normalized_transport
            if normalized_transport in {"auto", "argv", "file"}
            else "argv"
        )
        try:
            normalized_budget = int(file_prompt_budget)
        except (TypeError, ValueError):
            normalized_budget = 48_000
        self.file_prompt_budget = max(ARGV_PROMPT_BUDGET, normalized_budget)
        self.report_callback = report_callback
        self._file_transport_supported: bool | None = None
        self._isolated_workspace: Path | None = None
        self._last_command_chars = 0

    def set_working_directory(self, directory: str | Path | None) -> None:
        self.working_directory = Path(directory) if directory else None

    def use_isolated_workspace(self) -> None:
        """Run dsh in a private empty directory instead of the user's project.

        Story context travels either inline or through one short-lived task
        file created in this directory.  Keeping that bridge outside the
        novel project prevents the agent from seeing unrelated project files.
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
        """Run dsh headless using argv or a verified local task-file bridge."""
        combined = self._combine_prompts(system_prompt, user_prompt)
        effective_timeout = max(
            1,
            int(timeout_override if timeout_override is not None else self.timeout),
        )
        task_file: Path | None = None
        transport = "argv"
        fallback_used = False
        outcome = "failed"
        self._last_command_chars = 0
        try:
            transmitted_prompt = combined
            wants_file = self._should_use_file_transport(combined, session_id)
            if wants_file:
                if not self._ensure_file_transport_support(
                    timeout=min(effective_timeout, 30),
                    cancel_event=cancel_event,
                ):
                    if self.prompt_transport == "file":
                        transport = "file_unavailable"
                        raise RuntimeError(
                            "当前 DeepSeek Harness 无法读取 Novalist 的临时任务文件。"
                            "请将 dsh_prompt_transport 改为 argv，或检查 headless 的文件读取能力。"
                        )
                    fallback_used = True
                else:
                    task_file = self._write_task_file(combined)
                    transmitted_prompt = self._file_loader_prompt(task_file.name)
                    transport = "file"
            self._last_command_chars = 0
            result = self._execute_prompt(
                transmitted_prompt,
                session_id=session_id,
                timeout=effective_timeout,
                cancel_event=cancel_event,
                submitted_prompt_length=len(combined),
            )
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
            task_file_cleaned = task_file is None or not task_file.exists()
            if context_report is not None:
                completed_report = context_report.complete_invocation(
                    transport=transport,
                    submitted_prompt_chars=len(combined),
                    command_chars=self._last_command_chars,
                    fallback_used=fallback_used,
                    outcome=outcome,
                    task_file_cleaned=task_file_cleaned,
                )
                self._publish_context_report(completed_report)

    def resolve_prompt_budget(
        self,
        *,
        cancel_event: threading.Event | None = None,
    ) -> int:
        """Return the safe builder budget after resolving file capability."""
        if self.prompt_transport == "argv":
            return ARGV_PROMPT_BUDGET
        supported = self._ensure_file_transport_support(
            timeout=min(self.timeout, 30),
            cancel_event=cancel_event,
        )
        if supported:
            return self.file_prompt_budget
        if self.prompt_transport == "file":
            raise RuntimeError(
                "当前 DeepSeek Harness 无法读取 Novalist 的临时任务文件。"
                "请改用自动或 argv 兼容模式。"
            )
        return ARGV_PROMPT_BUDGET

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
        cmd.append(prompt)
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

    def _should_use_file_transport(
        self,
        prompt: str,
        session_id: str | None,
    ) -> bool:
        if self.prompt_transport == "argv":
            return False
        if self.prompt_transport == "file":
            return True
        command = self._resolve_command()
        cmd = [command, *self.launcher_args, "--profile", self.profile]
        cmd += self.extra_args
        if session_id:
            cmd += ["--resume", session_id]
        cmd.append(prompt)
        return len(subprocess.list2cmdline(cmd)) > FILE_TRANSPORT_SWITCH_LIMIT

    def _ensure_file_transport_support(
        self,
        *,
        timeout: int,
        cancel_event: threading.Event | None,
    ) -> bool:
        """Probe once whether this headless composition can read a task file."""
        if self._file_transport_supported is not None:
            return self._file_transport_supported

        marker = DSH_FILE_PROBE_PREFIX + uuid.uuid4().hex.upper()
        probe_payload = (
            "NOVALIST_TASK_START\n"
            "这是 Novalist 的本地任务文件传输测试。\n"
            f"请只回复这一行标记：{marker}\n"
            "NOVALIST_TASK_END\n"
        )
        task_file = self._write_task_file(probe_payload)
        try:
            output = self._execute_prompt(
                self._file_loader_prompt(task_file.name),
                session_id=None,
                timeout=max(1, int(timeout)),
                cancel_event=cancel_event,
                submitted_prompt_length=len(probe_payload),
            )
            self._file_transport_supported = marker in output
        finally:
            self._remove_task_file(task_file)
        return bool(self._file_transport_supported)

    def _write_task_file(self, prompt: str) -> Path:
        """Write one private UTF-8 task file in Novalist's isolated workspace."""
        if self._isolated_workspace is None or not self._isolated_workspace.is_dir():
            self.use_isolated_workspace()
        workspace = self._isolated_workspace
        if workspace is None:
            raise RuntimeError("无法创建 Novalist 的隔离 AI 工作目录。")
        self.working_directory = workspace
        path = workspace / f".novalist-task-{uuid.uuid4().hex}.md"
        atomic_write_text(path, prompt, encoding="utf-8")
        return path

    def _remove_task_file(self, path: Path | None) -> None:
        if path is None:
            return
        workspace = self._isolated_workspace
        try:
            if workspace is None or path.parent.resolve() != workspace.resolve():
                return
            path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _file_loader_prompt(filename: str) -> str:
        return (
            "NOVALIST_FILE_TASK_LOADER\n"
            f"请完整读取当前工作目录中的 {filename}（UTF-8）。\n"
            "该文件中 NOVALIST_TASK_START 与 NOVALIST_TASK_END 之间的内容"
            "才是本次完整任务。请立即执行该任务，不要仅概括文件内容，也不要"
            "修改或删除任何文件。\n"
            f"如果无法完整读取，只回复 {DSH_FILE_READ_FAILED}。"
        )

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
            r'(?<!["\w])([^\s"\r\n]+\.js)(?=\s|$)',
            shim,
            re.IGNORECASE,
        )
        script: Path | None = None
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
                script = candidate
                break
        if script is None:
            return None

        node_path = path.parent / "node.exe"
        node = str(node_path) if node_path.is_file() else shutil.which("node")
        if not node:
            return None
        return str(node), str(script)

    def check_connection(self) -> str:
        """Check startup and verify that a real task reaches headless DSH.

        ``--version`` only proves that the executable can start.  It does not
        catch Windows command-line truncation, which is exactly the failure
        mode that can leave DSH running with an empty task.  Keep the version
        check for a useful diagnostic, then send a tiny marker task through
        the same path used by normal generation.
        """
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
        try:
            probe = self.generate(
                "",
                f"这是 Novalist 的连接测试。请只回复 {DSH_PROBE_MARKER}。",
                timeout_override=min(self.timeout, 15),
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
        file_status = "命令行兼容模式"
        if self.prompt_transport != "argv":
            try:
                file_supported = self._ensure_file_transport_support(
                    timeout=min(self.timeout, 30),
                    cancel_event=None,
                )
            except RuntimeError as exc:
                raise RuntimeError(
                    "dsh 可以执行普通任务，但本地任务文件探测失败。\n"
                    f"{exc}"
                ) from exc
            file_status = (
                "扩展任务文件传输可用"
                if file_supported
                else "任务文件不可读，已回退命令行兼容模式"
            )
        return (
            f"dsh 可用，任务传递正常，{file_status}（{version}）"
            if version
            else f"dsh 可用，任务传递正常，{file_status}"
        )

    def _resolve_command(self) -> str:
        """Resolve npm command shims before passing them to CreateProcess."""
        command = str(self.dsh_command or "dsh").strip()
        if Path(command).suffix.lower() in {".cmd", ".bat", ".com", ".exe"}:
            return command
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
        try:
            return extract_json(text)
        except JSONExtractionError as exc:
            raise RuntimeError(f"dsh 返回内容不是合法 JSON：\n{str(text)[:500]}") from exc
