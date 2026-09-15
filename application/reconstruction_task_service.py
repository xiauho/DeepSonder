"""Background lifecycle for cancellable schema-v2 reconstruction scans."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
from typing import Any, Callable
from uuid import uuid4

from core.project_v2_schema import ProjectV2Descriptor

from .reconstruction_service import ReconstructionCancelled, ReconstructionService


TERMINAL_RECONSTRUCTION_STATES = {"succeeded", "failed", "cancelled"}
EventSink = Callable[[str, dict[str, Any]], None]


@dataclass
class ReconstructionTaskRecord:
    task_id: str
    project_root: str
    source_revision: str
    requested_mode: str = "local"
    status: str = "queued"
    stage: str = "等待执行"
    progress: int = 0
    started_at: str = field(default_factory=lambda: _now())
    finished_at: str | None = None
    batch_id: str = ""
    error: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)


class ReconstructionTaskService:
    """Run one local reconstruction task without blocking serialized RPC."""

    def __init__(
        self,
        *,
        reconstruction_service: ReconstructionService | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self.reconstruction_service = reconstruction_service or ReconstructionService()
        self._event_sink = event_sink or (lambda _name, _data: None)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="novalist-reconstruct")
        self._lock = threading.RLock()
        self._active: ReconstructionTaskRecord | None = None
        self._recent: ReconstructionTaskRecord | None = None
        self._closed = False

    def set_event_sink(self, sink: EventSink) -> None:
        self._event_sink = sink

    def start(
        self,
        project: ProjectV2Descriptor,
        *,
        extraction_mode: str = "local",
        remote_consent: bool = False,
    ) -> dict[str, Any]:
        if extraction_mode not in {"local", "dsh"}:
            raise ValueError("识别模式必须是 local 或 dsh。")
        if extraction_mode == "dsh" and not remote_consent:
            raise ValueError("使用 DSH 增强识别前必须明确同意发送正文证据片段。")
        with self._lock:
            if self._closed:
                raise RuntimeError("正文识别任务服务正在关闭。")
            if self.is_running():
                raise RuntimeError("已有正文识别任务正在运行。")
            source_revision = self.reconstruction_service.snapshot(project).source_revision
            task = ReconstructionTaskRecord(
                task_id=uuid4().hex,
                project_root=str(project.root),
                source_revision=source_revision,
                requested_mode=extraction_mode,
            )
            self._active = task
            self._recent = task
        self._emit(task)
        self._executor.submit(self._run, task, project)
        return reconstruction_task_dto(task)

    def status(self) -> dict[str, Any]:
        with self._lock:
            active = self._active if self._active and self._active.status not in TERMINAL_RECONSTRUCTION_STATES else None
            return {
                "active": reconstruction_task_dto(active) if active else None,
                "recent": reconstruction_task_dto(self._recent) if self._recent else None,
            }

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._recent
            if task is None or task.task_id != task_id:
                raise ValueError("正文识别任务不存在。")
            if task.status in TERMINAL_RECONSTRUCTION_STATES:
                return reconstruction_task_dto(task)
            task.cancel_event.set()
            task.status = "cancel_requested"
            task.stage = "正在取消"
        self._emit(task)
        return reconstruction_task_dto(task)

    def is_running(self) -> bool:
        with self._lock:
            return self._active is not None and self._active.status not in TERMINAL_RECONSTRUCTION_STATES

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            if self._active and self._active.status not in TERMINAL_RECONSTRUCTION_STATES:
                self._active.cancel_event.set()
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _run(self, task: ReconstructionTaskRecord, project: ProjectV2Descriptor) -> None:
        self._update(task, "running", "正在准备正文", 5)
        try:
            batch = self.reconstruction_service.generate(
                project,
                extraction_mode=task.requested_mode,
                remote_consent=task.requested_mode == "dsh",
                cancel_event=task.cancel_event,
                progress_callback=lambda stage, progress: self._update(task, "running", stage, progress),
            )
            if task.cancel_event.is_set():
                raise ReconstructionCancelled()
        except ReconstructionCancelled:
            self._finish(task, "cancelled", "任务已取消")
        except Exception as exc:
            self._finish(task, "failed", "识别失败", error=str(exc))
        else:
            with self._lock:
                task.batch_id = str(batch["batch_id"])
            self._finish(task, "succeeded", "等待审核")

    def _update(self, task: ReconstructionTaskRecord, status: str, stage: str, progress: int) -> None:
        with self._lock:
            if task.cancel_event.is_set() and status == "running":
                return
            task.status, task.stage = status, stage
            task.progress = max(task.progress, min(99, int(progress)))
        self._emit(task)

    def _finish(self, task: ReconstructionTaskRecord, status: str, stage: str, *, error: str = "") -> None:
        with self._lock:
            task.status, task.stage, task.progress = status, stage, 100
            task.error = error
            task.finished_at = _now()
            if self._active is task:
                self._active = None
        self._emit(task)

    def _emit(self, task: ReconstructionTaskRecord) -> None:
        self._event_sink("reconstruction.taskUpdated", {"task": reconstruction_task_dto(task)})


def reconstruction_task_dto(task: ReconstructionTaskRecord) -> dict[str, Any]:
    return {
        "taskId": task.task_id,
        "projectRoot": task.project_root,
        "sourceRevision": task.source_revision,
        "requestedMode": task.requested_mode,
        "status": task.status,
        "stage": task.stage,
        "progress": task.progress,
        "startedAt": task.started_at,
        "finishedAt": task.finished_at,
        "batchId": task.batch_id,
        "error": task.error,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
