"""Run a live, synthetic schema-v2 AI workflow acceptance check.

The verifier creates a disposable project and never opens user manuscript data.
It still invokes the configured remote DSH process, so an explicit command-line
acknowledgement is mandatory. Reports contain only sizes, states, and digests.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from application.ai_task_service import AITaskService  # noqa: E402
from application.ai_v2_context import MEMORY_PATH  # noqa: E402
from application.document_v2_service import DocumentV2Service  # noqa: E402
from application.manuscript_import_service import ManuscriptImportService  # noqa: E402
from application.project_v2_service import ProjectV2Service  # noqa: E402
from application.reconstruction_service import ReconstructionService  # noqa: E402
from core.config import load_config, normalize_config  # noqa: E402
from core.storage import atomic_write_text  # noqa: E402


TERMINAL = {"succeeded", "failed", "cancelled", "applied", "discarded"}


def _fingerprint(root: Path) -> str:
    entries = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        payload = path.read_bytes()
        entries.append({
            "path": path.relative_to(root).as_posix(),
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _wait(service: AITaskService, task_id: str, timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + max(30, timeout)
    while time.monotonic() < deadline:
        task = next(
            item for item in service.status()["recent"] if item["taskId"] == task_id
        )
        if task["status"] in TERMINAL:
            return task
        time.sleep(0.1)
    raise TimeoutError("schema-v2 AI 验收任务等待超时。")


def _create_synthetic_project(parent: Path):
    source = parent / "synthetic-source"
    source.mkdir()
    (source / "01.md").write_text(
        "# 第一章 合成雾港\n\n"
        "[[人物:林砚]] [[人物:苏乔]] [[关系:林砚|同伴|苏乔]] "
        "[[世界:雾港|地点|合成验收使用的虚构港城]]\n\n"
        "林砚和苏乔在雾港收到一封没有署名的合成信件。\n",
        encoding="utf-8",
    )
    (source / "02.md").write_text(
        "# 第二章 合成灯塔\n\n"
        "雨声敲打窗沿。林砚收起信件，苏乔指向远处的旧灯塔。"
        "两人尚未决定是否出发，房门外忽然传来三声轻响。\n",
        encoding="utf-8",
    )
    output = parent / "project-output"
    output.mkdir()
    project = ProjectV2Service().create_project(
        output,
        "phase-20d-synthetic-ai",
        author="Novalist release verifier",
        import_plan=ManuscriptImportService().scan(source),
    )
    reconstruction = ReconstructionService()
    batch = reconstruction.generate(project)
    pending = [
        {"proposal_id": item["proposal_id"], "decision": "accepted"}
        for item in batch["proposals"]
        if item.get("status") == "pending"
    ]
    if pending:
        reconstruction.review_many(project, batch["batch_id"], pending)
    return project, reconstruction


def run_verification(config: dict[str, Any], *, target_chars: int) -> dict[str, Any]:
    normalized = normalize_config(config)
    normalized["chapter_target_chars"] = max(300, min(2_000, int(target_chars)))
    cases: list[dict[str, Any]] = []
    context_reports: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="novalist-v2-ai-smoke-") as temporary:
        project, reconstruction = _create_synthetic_project(Path(temporary))
        documents = DocumentV2Service()

        def on_event(name: str, data: dict[str, Any]) -> None:
            if name == "ai.contextReport" and isinstance(data.get("report"), dict):
                context_reports[str(data.get("taskId", ""))] = data["report"]

        service = AITaskService(
            event_sink=on_event,
            config_loader=lambda: normalized,
            document_v2_service=documents,
            reconstruction_service=reconstruction,
        )
        timeout = int(normalized["dsh_timeout"]) + 30
        try:
            connection = service.start(
                None, "connection", notice_accepted=True,
            )
            terminal = _wait(service, connection["taskId"], timeout)
            cases.append({
                "kind": "connection",
                "passed": terminal["status"] == "succeeded",
                "status": terminal["status"],
                "error_type": "" if not terminal["error"] else "connection_failed",
            })

            for kind, chapter_id, adoption in (
                ("check", "chapter_0002", "discard"),
                ("expand", "chapter_0001", "discard"),
                ("continuation", "chapter_0002", "apply"),
                ("memory", "chapter_0002", "commit"),
            ):
                opened = documents.open_document(project, chapter_id)
                before_generation = _fingerprint(project.root)
                started_at = time.monotonic()
                task = service.start(
                    project,
                    kind,
                    chapter_id,
                    source_revision=opened.revision,
                    notice_accepted=True,
                )
                terminal = _wait(service, task["taskId"], timeout)
                generation_unchanged = _fingerprint(project.root) == before_generation
                case: dict[str, Any] = {
                    "kind": kind,
                    "passed": terminal["status"] == "succeeded" and generation_unchanged,
                    "status": terminal["status"],
                    "review_first": generation_unchanged,
                    "elapsed_ms": round((time.monotonic() - started_at) * 1_000),
                    "error_type": "" if not terminal["error"] else "workflow_failed",
                }
                if terminal["status"] == "succeeded":
                    result = service.result(task["taskId"])["result"]
                    case["result_type"] = str(result.get("type", ""))
                    case["generated_chars"] = int(result.get("charCount", 0) or 0)
                    report = context_reports.get(task["taskId"], {})
                    case["context_scope"] = str(report.get("state_scope", ""))
                    case["estimated_input_tokens"] = int(
                        report.get("estimated_input_tokens", 0) or 0
                    )
                    if adoption == "apply":
                        service.apply_writing_result(project, documents, task["taskId"])
                        reconstruction.invalidate_chapter(project, chapter_id)
                        case["adoption"] = "writing_applied_to_disposable_project"
                    elif adoption == "commit":
                        service.commit_memory_result(project, task["taskId"])
                        case["adoption"] = "memory_committed_to_disposable_project"
                        case["memory_file_created"] = (
                            project.root / MEMORY_PATH
                        ).is_file()
                        case["passed"] = case["passed"] and case["memory_file_created"]
                    else:
                        service.discard(task["taskId"])
                        case["adoption"] = "discarded"
                cases.append(case)
        finally:
            service.shutdown()

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_scope": "disposable_synthetic_manuscript_only",
        "passed": all(case["passed"] for case in cases),
        "configuration": {
            "dsh_command_name": Path(normalized["dsh_command"]).name,
            "profile": "headless",
            "target_chars": normalized["chapter_target_chars"],
            "configured_timeout_seconds": normalized["dsh_timeout"],
        },
        "cases": cases,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="使用一次性合成正文验收 schema-v2 的真实 DSH AI 工作流。"
    )
    parser.add_argument(
        "--acknowledge-synthetic-remote",
        action="store_true",
        help="确认合成正文会发送给已配置的 DSH，并可能产生远程调用成本。",
    )
    parser.add_argument("--target-chars", type=int, default=600)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if not args.acknowledge_synthetic_remote:
        parser.error("必须显式传入 --acknowledge-synthetic-remote。")
    if not 300 <= args.target_chars <= 2_000:
        parser.error("--target-chars 必须在 300 到 2000 之间。")
    return args


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args(argv)
    report = run_verification(load_config(), target_chars=args.target_chars)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report is not None:
        atomic_write_text(args.report.resolve(), rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
