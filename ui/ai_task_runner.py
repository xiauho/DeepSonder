"""Qt task runner for background AI workflows."""

from __future__ import annotations

from threading import Event
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal

from core.task_controller import AITaskCancelled, AITaskController, AITaskToken


class DSHTask(QThread):
    """Run one blocking callable away from the writing interface."""

    success = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, fn, cancel_event: Event | None = None, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancel_event = cancel_event or Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            result = self._fn(self._cancel_event)
        except AITaskCancelled:
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - surface an actionable UI error
            self.failed.emit(str(exc))
        else:
            self.success.emit(result)


class AITaskRunner(QObject):
    """Own AI task threads and expose one normalized result stream."""

    started = Signal(object)
    succeeded = Signal(object, object)
    failed = Signal(object, str)
    cancelled = Signal(object)
    finished = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.controller = AITaskController()
        self._thread: DSHTask | None = None
        self._token: AITaskToken | None = None

    @property
    def current_token(self) -> AITaskToken | None:
        return self._token

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(self, kind: str, chapter_id: str, worker: Callable) -> AITaskToken | None:
        token = self.controller.start(kind, chapter_id)
        if token is None:
            return None
        self._token = token
        self._thread = DSHTask(worker, token.cancel_event, self)
        self._thread.success.connect(self._on_success)
        self._thread.failed.connect(self._on_failed)
        self._thread.cancelled.connect(self._on_cancelled)
        self._thread.finished.connect(self._on_finished)
        self.started.emit(token)
        self._thread.start()
        return token

    def cancel(self) -> bool:
        token = self._token
        if token is None or self._thread is None or not self.is_running():
            return False
        if not self.controller.request_cancel(token):
            return False
        self._thread.cancel()
        return True

    def _is_current(self, token: AITaskToken | None) -> bool:
        return self.controller.is_current(token)

    def _on_success(self, result) -> None:
        token = self._token
        if self._is_current(token) and not token.cancel_event.is_set():
            self.succeeded.emit(token, result)

    def _on_failed(self, message: str) -> None:
        token = self._token
        if self._is_current(token):
            self.failed.emit(token, message)

    def _on_cancelled(self) -> None:
        token = self._token
        if self._is_current(token):
            self.cancelled.emit(token)

    def _on_finished(self) -> None:
        token = self._token
        if not self._is_current(token):
            return
        self.controller.finish(token)
        self._thread = None
        self.finished.emit(token)
