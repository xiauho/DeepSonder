"""Framework-independent lifecycle for long-running, review-first AI tasks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Any, Callable
from uuid import uuid4

from core.ai_result_service import AIResultService
from core.ai_workflow import AIWorkflowService
from core.config import get_chapter_target_chars, load_config, normalize_config
from core.context_report import PromptContextReport
from core.dsh_client import DSHClient
from core.project import NovelProject
from core.task_context import AIContextSnapshot
from core.task_controller import AITaskCancelled

from .document_service import DocumentService, DocumentSnapshot


AI_KINDS = {"expand", "continuation", "check", "memory", "connection"}
TERMINAL_STATES = {"succeeded", "failed", "cancelled", "applied", "discarded"}
EventSink = Callable[[str, dict[str, Any]], None]
ExecutionFactory = Callable[
    [str, NovelProject | None, str, dict[str, Any], threading.Event, Callable[[PromptContextReport], None]],
    tuple[dict[str, Any], object | None],
]


@dataclass
class AITaskRecord:
    task_id: str
    kind: str
    chapter_id: str
    project_root: str
    source_revision: str | None
    context_snapshot: AIContextSnapshot | None
    status: str = "queued"
    stage: str = "等待执行"
    progress: int = 0
    started_at: str = field(default_factory=lambda: _now())
    finished_at: str | None = None
    error: str = ""
    result: dict[str, Any] | None = None
    internal_result: object | None = field(default=None, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)


class AITaskService:
    """Run one foreground AI task and retain bounded review results."""

    RESULT_LIMIT = 12

    def __init__(
        self,
        *,
        event_sink: EventSink | None = None,
        execution_factory: ExecutionFactory | None = None,
        config_loader: Callable[[], dict] = load_config,
    ) -> None:
        self._event_sink = event_sink or (lambda _name, _data: None)
        self._execution_factory = execution_factory or self._execute_production
        self._config_loader = config_loader
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="novalist-ai")
        self._lock = threading.RLock()
        self._tasks: dict[str, AITaskRecord] = {}
        self._active_task_id: str | None = None
        self._closed = False

    def set_event_sink(self, sink: EventSink) -> None:
        self._event_sink = sink

    def status(self) -> dict[str, Any]:
        with self._lock:
            active = self._tasks.get(self._active_task_id or "")
            recent = sorted(self._tasks.values(), key=lambda item: item.started_at, reverse=True)
            return {
                "active": task_dto(active) if active and active.status not in TERMINAL_STATES else None,
                "recent": [task_dto(item) for item in recent[: self.RESULT_LIMIT]],
                "supportedKinds": sorted(AI_KINDS),
            }

    def is_running(self) -> bool:
        with self._lock:
            task = self._tasks.get(self._active_task_id or "")
            return task is not None and task.status not in TERMINAL_STATES

    def start(
        self,
        project: NovelProject | None,
        kind: str,
        chapter_id: str = "",
        *,
        source_revision: str | None = None,
        options: dict[str, Any] | None = None,
        notice_accepted: bool = False,
    ) -> dict[str, Any]:
        kind = str(kind or "").strip()
        if kind not in AI_KINDS:
            raise ValueError("不支持的 AI 任务类型。")
        if not notice_accepted:
            raise ValueError("开始 AI 任务前必须确认数据处理告知。")
        if kind != "connection" and project is None:
            raise ValueError("AI 创作任务需要一个已打开的项目。")
        chapter_id = str(chapter_id or "").strip()
        context: AIContextSnapshot | None = None
        project_root = ""
        if kind != "connection":
            assert project is not None
            if not chapter_id:
                raise ValueError("AI 创作任务缺少章节标识。")
            chapter_path = project.chapters_dir / f"{chapter_id}.md"
            if not chapter_path.is_file():
                raise FileNotFoundError(chapter_path)
            actual_revision = DocumentService.revision_for_path(chapter_path)
            if not source_revision or source_revision != actual_revision:
                raise ValueError("章节版本已经变化，请保存并重新打开后再启动 AI 任务。")
            context = AIContextSnapshot.capture(project, chapter_id, None, task_kind=kind)
            project_root = str(project.root.resolve())
        with self._lock:
            if self._closed:
                raise RuntimeError("AI 任务服务正在关闭。")
            if self.is_running():
                raise RuntimeError("已有 AI 任务正在运行。")
            task = AITaskRecord(
                task_id=uuid4().hex,
                kind=kind,
                chapter_id=chapter_id,
                project_root=project_root,
                source_revision=source_revision,
                context_snapshot=context,
            )
            self._tasks[task.task_id] = task
            self._active_task_id = task.task_id
            self._trim_results()
        self._emit(task)
        self._executor.submit(self._run, task, project, dict(options or {}))
        return task_dto(task)

    def cancel(self, task_id: str) -> dict[str, Any]:
        task = self._task(task_id)
        with self._lock:
            if task.status in TERMINAL_STATES:
                return task_dto(task)
            task.cancel_event.set()
            task.status = "cancel_requested"
            task.stage = "正在取消"
            task.progress = max(task.progress, 10)
        self._emit(task)
        return task_dto(task)

    def result(self, task_id: str) -> dict[str, Any]:
        task = self._task(task_id)
        if task.status not in {"succeeded", "applied"} or task.result is None:
            raise ValueError("该任务尚无可审阅结果。")
        return {"task": task_dto(task), "result": task.result}

    def discard(self, task_id: str) -> dict[str, Any]:
        task = self._task(task_id)
        with self._lock:
            if task.status != "succeeded":
                raise ValueError("只能放弃等待审阅的 AI 结果。")
            task.status = "discarded"
            task.stage = "结果已放弃"
            task.result = None
            task.internal_result = None
        self._emit(task)
        return task_dto(task)

    def apply_writing_result(
        self,
        project: NovelProject,
        document_service: DocumentService,
        task_id: str,
    ) -> DocumentSnapshot:
        task = self._review_task(project, task_id, {"expand", "continuation"})
        assert task.result is not None
        chapter_path = project.chapters_dir / f"{task.chapter_id}.md"
        raw = chapter_path.read_text(encoding="utf-8")
        chapter = project.load_chapter(task.chapter_id)
        generated = str(task.result.get("text") or "").strip()
        body = generated if task.kind == "expand" else "\n\n".join(
            part for part in (chapter.content.strip(), generated) if part
        )
        updated = NovelProject.replace_chapter_body(raw, body)
        saved = document_service.save_document(
            project,
            "章节",
            chapter_path,
            updated,
            expected_revision=task.source_revision,
        )
        self._mark_applied(task)
        return saved

    def commit_memory_result(self, project: NovelProject, task_id: str) -> dict[str, Any]:
        task = self._review_task(project, task_id, {"memory"})
        if task.internal_result is None:
            raise ValueError("记忆提案不可用。")
        committed = AIResultService.commit_memory_proposal(
            project, task.chapter_id, task.internal_result
        )
        self._mark_applied(task)
        return {
            "chapterId": task.chapter_id,
            "currentChapter": committed.expected_chapter,
            "invalidatedChapters": list(committed.invalidated_chapters),
        }

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            task = self._tasks.get(self._active_task_id or "")
            if task and task.status not in TERMINAL_STATES:
                task.cancel_event.set()
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _run(self, task: AITaskRecord, project: NovelProject | None, options: dict[str, Any]) -> None:
        self._update(task, "running", "正在准备上下文", 10)
        try:
            public, internal = self._execution_factory(
                task.kind,
                project,
                task.chapter_id,
                options,
                task.cancel_event,
                lambda report: self._on_context_report(task, report),
            )
            if task.cancel_event.is_set():
                raise AITaskCancelled()
        except AITaskCancelled:
            self._finish(task, "cancelled", "任务已取消", 100)
        except Exception as exc:
            self._finish(task, "failed", "任务失败", 100, error=str(exc))
        else:
            with self._lock:
                task.result = public
                task.internal_result = internal
            self._finish(task, "succeeded", "等待审阅", 100)

    def _execute_production(
        self,
        kind: str,
        project: NovelProject | None,
        chapter_id: str,
        options: dict[str, Any],
        cancel_event: threading.Event,
        report_callback: Callable[[PromptContextReport], None],
    ) -> tuple[dict[str, Any], object | None]:
        config = normalize_config(self._config_loader())
        client = DSHClient(
            dsh_command=config["dsh_command"],
            launcher_args=config["dsh_launcher_args"],
            profile="headless",
            timeout=config["dsh_timeout"],
            extra_args=config["dsh_extra_args"],
            file_prompt_budget=config["dsh_file_prompt_budget"],
            task_file_max_bytes=config["dsh_task_file_max_bytes"],
            input_token_budget=config["ai_input_token_budget"],
            runtime_reserve_tokens=config["ai_runtime_reserve_tokens"],
            model_context_window_tokens=config["ai_model_context_window_tokens"],
            context_strategy=config["ai_context_strategy"],
            report_callback=report_callback,
        )
        client.use_isolated_workspace()
        try:
            if kind == "connection":
                return {
                    "type": "connection",
                    "message": client.check_connection(cancel_event=cancel_event),
                }, None
            assert project is not None
            workflow = AIWorkflowService(
                client,
                input_token_budget=config["ai_input_token_budget"],
                chunk_token_budget=config["ai_chunk_token_budget"],
                chunk_overlap_tokens=config["ai_chunk_overlap_tokens"],
            )
            history = config["ai_context_history_chapters"]
            history_mode = config["ai_history_mode"]
            remote = config["ai_history_remote_enabled"]
            if kind == "expand":
                run = workflow.expand(
                    project,
                    chapter_id,
                    target_chars=get_chapter_target_chars(config),
                    history_chapters=history,
                    history_mode=history_mode,
                    history_remote_enabled=remote,
                    selected_foreshadowing=tuple(options.get("selectedForeshadowing", ())),
                    selected_power=tuple(options.get("selectedPower", ())),
                    cancel_event=cancel_event,
                )
                parsed = AIResultService.parse_expansion(
                    run.raw_output, run.target_chars, chapter_id=chapter_id,
                    selected_foreshadowing=tuple(options.get("selectedForeshadowing", ())),
                )
                return _writing_result("replace", parsed.text, parsed.char_count, run), run
            if kind == "continuation":
                run = workflow.continue_chapter(
                    project,
                    chapter_id,
                    target_chapter_chars=get_chapter_target_chars(config),
                    history_chapters=history,
                    history_mode=history_mode,
                    history_remote_enabled=remote,
                    cancel_event=cancel_event,
                )
                parsed = AIResultService.parse_continuation(run.raw_output, run.requested_chars)
                return _writing_result("append", parsed.text, parsed.char_count, run), run
            if kind == "check":
                raw = workflow.check(
                    project, chapter_id, cancel_event=cancel_event,
                    history_remote_enabled=remote,
                )
                report, formatted = AIResultService.parse_consistency(raw, expected_chapter_id=chapter_id)
                return {"type": "consistency", "report": report, "formatted": formatted}, report
            proposal = workflow.update_memory(project, chapter_id, cancel_event=cancel_event)
            return {
                "type": "memory",
                "summary": proposal.summary,
                "preview": proposal.preview_text(),
                "hasBlockers": proposal.has_blockers,
                "conflictCount": len(proposal.conflicts),
                "patchCount": len(proposal.patches),
                "cacheHit": proposal.cache_hit,
            }, proposal
        finally:
            client.cleanup()

    def _review_task(self, project: NovelProject, task_id: str, kinds: set[str]) -> AITaskRecord:
        task = self._task(task_id)
        if task.kind not in kinds or task.status != "succeeded" or task.result is None:
            raise ValueError("该 AI 结果不能执行此采用操作。")
        if str(project.root.resolve()).casefold() != task.project_root.casefold():
            raise ValueError("AI 结果不属于当前项目。")
        if task.context_snapshot is None or not task.context_snapshot.matches(
            project, task.chapter_id, None, task_kind=task.kind
        ):
            raise ValueError("任务完成后项目上下文已经变化，请重新生成。")
        return task

    def _mark_applied(self, task: AITaskRecord) -> None:
        with self._lock:
            task.status = "applied"
            task.stage = "结果已采用"
            task.result = None
            task.internal_result = None
        self._emit(task)

    def _on_context_report(self, task: AITaskRecord, report: PromptContextReport) -> None:
        self._update(task, "running", "模型正在处理", 45)
        self._event_sink("ai.contextReport", {"taskId": task.task_id, "report": report.to_dict()})

    def _update(self, task: AITaskRecord, status: str, stage: str, progress: int) -> None:
        with self._lock:
            task.status, task.stage, task.progress = status, stage, progress
        self._emit(task)

    def _finish(self, task: AITaskRecord, status: str, stage: str, progress: int, *, error: str = "") -> None:
        with self._lock:
            task.status, task.stage, task.progress = status, stage, progress
            task.error = error
            task.finished_at = _now()
            if self._active_task_id == task.task_id:
                self._active_task_id = None
        self._emit(task)

    def _emit(self, task: AITaskRecord) -> None:
        try:
            self._event_sink("ai.taskUpdated", {"task": task_dto(task)})
        except Exception:
            # A closed UI channel must not change the task's terminal state.
            pass

    def _task(self, task_id: str) -> AITaskRecord:
        with self._lock:
            task = self._tasks.get(str(task_id or ""))
        if task is None:
            raise ValueError("AI 任务不存在。")
        return task

    def _trim_results(self) -> None:
        if len(self._tasks) <= self.RESULT_LIMIT:
            return
        removable = sorted(
            (item for item in self._tasks.values() if item.status in TERMINAL_STATES),
            key=lambda item: item.started_at,
        )
        for item in removable[: max(0, len(self._tasks) - self.RESULT_LIMIT)]:
            self._tasks.pop(item.task_id, None)


def task_dto(task: AITaskRecord | None) -> dict[str, Any] | None:
    if task is None:
        return None
    return {
        "taskId": task.task_id,
        "kind": task.kind,
        "chapterId": task.chapter_id,
        "status": task.status,
        "stage": task.stage,
        "progress": task.progress,
        "startedAt": task.started_at,
        "finishedAt": task.finished_at,
        "error": task.error,
        "hasResult": task.status == "succeeded" and task.result is not None,
    }


def _writing_result(mode: str, text: str, char_count: int, run: object) -> dict[str, Any]:
    return {
        "type": "writing",
        "mode": mode,
        "text": text,
        "charCount": char_count,
        "targetChars": int(getattr(run, "target_chars", getattr(run, "requested_chars", 0))),
        "minChars": int(getattr(run, "min_chars", 0)),
        "maxChars": int(getattr(run, "max_chars", 0)),
        "reviewMinChars": int(getattr(run, "review_min_chars", 0)),
        "reviewMaxChars": int(getattr(run, "review_max_chars", 0)),
        "lengthStatus": str(getattr(run, "length_status", "qualified")),
        "warning": str(getattr(run, "supplement_warning", "")),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
