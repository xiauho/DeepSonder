"""Application-level coordinator for background AI tasks."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, Signal

from core.project import NovelProject
from core.task_context import AIContextSnapshot
from core.task_controller import AITaskToken
from ui.ai_task_runner import AITaskRunner


class AIController(QObject):
    """Own AI task lifecycle and the snapshot used to guard its results.

    The main window still decides how results are displayed and confirmed.
    This controller keeps task execution concerns together so UI code does not
    need to know about the worker thread or snapshot implementation.
    """

    started = Signal(object)
    succeeded = Signal(object, object)
    failed = Signal(object, str)
    cancelled = Signal(object)
    finished = Signal(object)

    def __init__(self, parent=None, task_runner: AITaskRunner | None = None):
        super().__init__(parent)
        self._task_runner = task_runner or AITaskRunner(self)
        self._task_chapter_id: str | None = None
        self._task_context_snapshot: AIContextSnapshot | None = None
        self._task_context_data: object | None = None
        # A worker can finish before the UI has finished reviewing its result.
        # Keep snapshots by token so a nested modal event loop cannot make a
        # valid result look stale by clearing the currently running task state.
        self._result_snapshots: dict[str, AIContextSnapshot] = {}
        self._result_context_data: dict[str, object | None] = {}
        self._pending_result_tokens: set[str] = set()

        self._task_runner.started.connect(self.started)
        self._task_runner.succeeded.connect(self._on_task_succeeded)
        self._task_runner.failed.connect(self.failed)
        self._task_runner.cancelled.connect(self.cancelled)
        self._task_runner.finished.connect(self._on_task_finished)

    @property
    def task_runner(self) -> AITaskRunner:
        """Expose the runner for diagnostics and focused tests."""
        return self._task_runner

    @property
    def task_chapter_id(self) -> str | None:
        return self._task_chapter_id

    def is_running(self) -> bool:
        return self._task_runner.is_running()

    def start(
        self,
        kind: str,
        project: NovelProject,
        chapter_id: str,
        editor_text: str | None,
        worker: Callable,
        task_context: object | None = None,
    ) -> AITaskToken | None:
        """Capture context and start one background task.

        Returning ``None`` means another task is already active. Context is
        captured before the worker starts, so result handlers can use the same
        task identity throughout the request.
        """
        if self.is_running():
            return None
        snapshot = AIContextSnapshot.capture(
            project,
            chapter_id,
            editor_text,
            task_context=task_context,
            task_kind=kind,
        )
        token = self._task_runner.start(kind, chapter_id, worker)
        if token is None:
            return None
        self._task_chapter_id = chapter_id
        self._task_context_snapshot = snapshot
        self._task_context_data = task_context
        self._result_snapshots[token.task_id] = snapshot
        self._result_context_data[token.task_id] = task_context
        return token

    def cancel(self) -> bool:
        return self._task_runner.cancel()

    def context_matches(
        self,
        project: NovelProject | None,
        chapter_id: str,
        editor_text: str | None,
        token: AITaskToken | None = None,
    ) -> bool:
        snapshot = (
            self._result_snapshots.get(token.task_id)
            if token is not None
            else self._task_context_snapshot
        )
        if project is None or snapshot is None:
            return False
        task_context = (
            self._result_context_data.get(token.task_id)
            if token is not None
            else self._task_context_data
        )
        return snapshot.matches(
            project,
            chapter_id,
            editor_text,
            task_context=task_context,
            task_kind=token.kind if token is not None else None,
        )

    def release_result(self, token: AITaskToken | None) -> None:
        """Release the snapshot retained while the UI reviews one result."""
        if token is not None:
            self._result_snapshots.pop(token.task_id, None)
            self._result_context_data.pop(token.task_id, None)
            self._pending_result_tokens.discard(token.task_id)

    def _on_task_succeeded(self, token: AITaskToken, result) -> None:
        self._pending_result_tokens.add(token.task_id)
        self.succeeded.emit(token, result)

    def _on_task_finished(self, token: AITaskToken) -> None:
        self._task_chapter_id = None
        self._task_context_snapshot = None
        self._task_context_data = None
        if token.task_id not in self._pending_result_tokens:
            # Failed and cancelled tasks have no result-review phase.
            self._result_snapshots.pop(token.task_id, None)
            self._result_context_data.pop(token.task_id, None)
        self.finished.emit(token)
