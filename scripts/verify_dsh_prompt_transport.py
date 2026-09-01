"""Run a live, synthetic end-to-end check of Novalist's DSH prompt transport.

The verifier reads the normal Novalist configuration, but never opens a novel
project.  It sends only random markers and generated filler text, then emits a
redacted JSON report containing sizes and pass/fail results instead of prompts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import load_config, normalize_config  # noqa: E402
from core.context_budget import ARGV_PROMPT_BUDGET  # noqa: E402
from core.dsh_client import DSHClient  # noqa: E402
from core.storage import atomic_write_text  # noqa: E402
from core.task_controller import AITaskCancelled  # noqa: E402


DEFAULT_LONG_CHARS = 45_000
MAX_LONG_CHARS = 120_000
SAFE_LOADER_COMMAND_CHARS = 4_000


@dataclass(frozen=True)
class SyntheticPrompt:
    text: str
    markers: tuple[str, str, str]
    sha256: str


@dataclass(frozen=True)
class InvocationTrace:
    transport: str
    submitted_prompt_chars: int
    command_chars: int
    transmitted_argument_chars: int


class DiagnosticDSHClient(DSHClient):
    """DSH client that records transport metrics without retaining prompts."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.invocations: list[InvocationTrace] = []

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
        prepared = self._prepare_command_for_prompt(cmd, command)
        self.invocations.append(
            InvocationTrace(
                transport=(
                    "file"
                    if prompt.startswith("NOVALIST_FILE_TASK_LOADER")
                    else "argv"
                ),
                submitted_prompt_chars=submitted_prompt_length,
                command_chars=len(subprocess.list2cmdline(prepared)),
                transmitted_argument_chars=len(prompt),
            )
        )
        return super()._execute_prompt(
            prompt,
            session_id=session_id,
            timeout=timeout,
            cancel_event=cancel_event,
            submitted_prompt_length=submitted_prompt_length,
        )


class SimulatedTimeoutClient(DSHClient):
    """Exercise generate() cleanup without terminating a real Harness boot."""

    def _execute_prompt(self, *_args, **_kwargs) -> str:
        raise RuntimeError("dsh 调用超时（诊断模拟）。")


def build_synthetic_prompt(target_chars: int = DEFAULT_LONG_CHARS) -> SyntheticPrompt:
    """Create exact-length UTF-8-friendly text with unique head/mid/tail markers."""
    target_chars = max(10_000, min(MAX_LONG_CHARS, int(target_chars)))
    token = uuid.uuid4().hex.upper()
    markers = (
        f"NVL_HEAD_{token}",
        f"NVL_MID_{token}",
        f"NVL_TAIL_{token}",
    )
    instruction = (
        "这是 Novalist DSh 长上下文传输验收。下面是合成数据，不含小说内容。\n"
        "请在数据中找到前缀分别为 NVL_HEAD_、NVL_MID_、NVL_TAIL_ 的三行，"
        "按出现顺序逐行原样返回；不要补写、缩写或猜测标记。\n"
        "SYNTHETIC_DATA_START\n"
    )
    fixed = (
        instruction
        + markers[0]
        + "\n"
        + markers[1]
        + "\n"
        + markers[2]
        + "\nSYNTHETIC_DATA_END"
    )
    remaining = target_chars - len(fixed)
    if remaining < 0:
        raise ValueError("目标长度不足以容纳验收指令。")
    pattern = "天地玄黄，synthetic='引号'，json={\"ok\":true}\n"
    filler = (pattern * ((remaining // len(pattern)) + 1))[:remaining]
    first_cut = len(filler) // 2
    text = (
        instruction
        + markers[0]
        + "\n"
        + filler[:first_cut]
        + markers[1]
        + "\n"
        + filler[first_cut:]
        + markers[2]
        + "\nSYNTHETIC_DATA_END"
    )
    if len(text) != target_chars:
        raise AssertionError("合成长提示词长度不符合目标。")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return SyntheticPrompt(text=text, markers=markers, sha256=digest)


def _case(name: str, passed: bool, **details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _task_file_names(client: DSHClient) -> list[str]:
    workspace = client.working_directory
    if workspace is None or not workspace.is_dir():
        return []
    return sorted(path.name for path in workspace.glob(".novalist-task-*.md"))


def _long_invocation(client: DiagnosticDSHClient, minimum_chars: int) -> InvocationTrace | None:
    for trace in reversed(client.invocations):
        if trace.submitted_prompt_chars >= minimum_chars:
            return trace
    return None


def _finish_report(
    config: dict,
    client: DiagnosticDSHClient,
    synthetic: SyntheticPrompt,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    workspace = client._isolated_workspace
    client.cleanup()
    workspace_removed = workspace is None or not workspace.exists()
    cases.append(
        _case(
            "workspace_directory_cleanup",
            workspace_removed,
            workspace_removed=workspace_removed,
        )
    )
    return _final_report(config, client, synthetic, cases)


def run_verification(config: dict, *, long_chars: int) -> dict[str, Any]:
    """Run live transport checks and return a prompt-redacted report."""
    normalized = normalize_config(config)
    client = DiagnosticDSHClient(
        dsh_command=normalized["dsh_command"],
        launcher_args=normalized["dsh_launcher_args"],
        profile="headless",
        timeout=normalized["dsh_timeout"],
        extra_args=normalized["dsh_extra_args"],
        prompt_transport="auto",
        file_prompt_budget=max(long_chars + 3_000, normalized["dsh_file_prompt_budget"]),
    )
    client.use_isolated_workspace()
    cases: list[dict[str, Any]] = []
    connection_status = ""
    synthetic = build_synthetic_prompt(long_chars)

    try:
        try:
            connection_status = client.check_connection()
            cases.append(
                _case(
                    "connection_and_file_capability",
                    "扩展任务文件传输可用" in connection_status,
                    status=connection_status,
                )
            )
        except Exception as exc:
            cases.append(
                _case(
                    "connection_and_file_capability",
                    False,
                    error_type=type(exc).__name__,
                    error=str(exc)[:500],
                )
            )
            return _finish_report(normalized, client, synthetic, cases)

        short_marker = "NVL_SHORT_" + uuid.uuid4().hex.upper()
        try:
            output = client.generate(
                "这是 Novalist 的短命令行传输验收。",
                f"请只回复这一行标记：{short_marker}",
                timeout_override=min(normalized["dsh_timeout"], 60),
            )
            trace = client.invocations[-1]
            cases.append(
                _case(
                    "short_argv_transport",
                    short_marker in output and trace.transport == "argv",
                    transport=trace.transport,
                    command_chars=trace.command_chars,
                    response_chars=len(output),
                )
            )
        except Exception as exc:
            cases.append(
                _case(
                    "short_argv_transport",
                    False,
                    error_type=type(exc).__name__,
                    error=str(exc)[:500],
                )
            )

        try:
            output = client.generate(
                "这是传输完整性验收，只处理用户任务中的合成数据。",
                synthetic.text,
                timeout_override=normalized["dsh_timeout"],
            )
            positions = [output.find(marker) for marker in synthetic.markers]
            trace = _long_invocation(client, long_chars)
            passed = (
                trace is not None
                and trace.transport == "file"
                and trace.command_chars < SAFE_LOADER_COMMAND_CHARS
                and all(position >= 0 for position in positions)
                and positions == sorted(positions)
            )
            cases.append(
                _case(
                    "long_file_transport_integrity",
                    passed,
                    transport=trace.transport if trace else "missing",
                    submitted_prompt_chars=(
                        trace.submitted_prompt_chars if trace else 0
                    ),
                    command_chars=trace.command_chars if trace else 0,
                    response_chars=len(output),
                    head_found=positions[0] >= 0,
                    middle_found=positions[1] >= 0,
                    tail_found=positions[2] >= 0,
                    marker_order_valid=positions == sorted(positions),
                    synthetic_sha256=synthetic.sha256,
                )
            )
        except Exception as exc:
            cases.append(
                _case(
                    "long_file_transport_integrity",
                    False,
                    error_type=type(exc).__name__,
                    error=str(exc)[:500],
                    synthetic_sha256=synthetic.sha256,
                )
            )

        cancel_event = threading.Event()
        cancel_event.set()
        try:
            client.generate(
                "取消清理验收。",
                synthetic.text,
                cancel_event=cancel_event,
            )
            cancelled = False
        except AITaskCancelled:
            cancelled = True
        except Exception:
            cancelled = False
        cases.append(
            _case(
                "cancelled_task_file_cleanup",
                cancelled and not _task_file_names(client),
                cancellation_observed=cancelled,
                residual_task_files=_task_file_names(client),
            )
        )

        timeout_client = SimulatedTimeoutClient(
            "dsh",
            prompt_transport="file",
            file_prompt_budget=long_chars + 3_000,
        )
        timeout_client.use_isolated_workspace()
        timeout_client._file_transport_supported = True
        timeout_workspace = timeout_client._isolated_workspace
        try:
            try:
                timeout_client.generate("超时清理验收。", synthetic.text)
                timeout_observed = False
            except RuntimeError as exc:
                timeout_observed = "超时" in str(exc)
            residual = _task_file_names(timeout_client)
        finally:
            timeout_client.cleanup()
        timeout_workspace_removed = (
            timeout_workspace is None or not timeout_workspace.exists()
        )
        cases.append(
            _case(
                "timeout_task_file_cleanup_simulation",
                timeout_observed and not residual and timeout_workspace_removed,
                timeout_observed=timeout_observed,
                residual_task_files=residual,
                workspace_removed=timeout_workspace_removed,
            )
        )

        fallback_client = DSHClient(
            "dsh",
            prompt_transport="auto",
            file_prompt_budget=long_chars + 3_000,
        )
        fallback_client._file_transport_supported = False
        fallback_budget = fallback_client.resolve_prompt_budget()
        cases.append(
            _case(
                "unavailable_file_transport_fallback",
                fallback_budget == ARGV_PROMPT_BUDGET,
                fallback_budget=fallback_budget,
            )
        )
        return _finish_report(normalized, client, synthetic, cases)
    finally:
        if client._isolated_workspace is not None:
            client.cleanup()


def _final_report(
    config: dict,
    client: DiagnosticDSHClient,
    synthetic: SyntheticPrompt,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    residual = _task_file_names(client)
    cases.append(
        _case(
            "final_task_file_cleanup",
            not residual,
            residual_task_files=residual,
        )
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": all(case["passed"] for case in cases),
        "configuration": {
            "dsh_command_name": Path(config["dsh_command"]).name,
            "profile": "headless",
            "configured_timeout_seconds": config["dsh_timeout"],
            "long_prompt_chars": len(synthetic.text),
            "synthetic_sha256": synthetic.sha256,
        },
        "cases": cases,
        "invocations": [asdict(trace) for trace in client.invocations],
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="使用纯合成数据验收 Novalist 的 DSh 长提示词传输。"
    )
    parser.add_argument(
        "--long-chars",
        type=int,
        default=DEFAULT_LONG_CHARS,
        help=f"长提示词目标字符数（默认 {DEFAULT_LONG_CHARS}，最大 {MAX_LONG_CHARS}）。",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="可选：将脱敏 JSON 报告原子写入指定路径。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = _parse_args(argv)
    report = run_verification(load_config(), long_chars=args.long_chars)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report is not None:
        atomic_write_text(args.report.resolve(), rendered + "\n")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
