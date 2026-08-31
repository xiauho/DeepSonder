"""Background coordination for secure update downloads."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, Signal

from core.task_controller import AITaskCancelled
from core.update_download_service import (
    DownloadProgress,
    UpdateDownloadCancelled,
    VerifiedUpdate,
    download_and_verify_update,
)
from core.update_service import UpdateInfo
from core.version import AppVersion
from ui.ai_task_runner import DSHTask


class UpdateDownloadController(QObject):
    """Download one release away from the UI thread without installing it."""

    started = Signal(object)
    progress_changed = Signal(int, int)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()

    def __init__(
        self,
        parent=None,
        *,
        task_factory: Callable = DSHTask,
        downloader: Callable = download_and_verify_update,
    ) -> None:
        super().__init__(parent)
        self._task_factory = task_factory
        self._downloader = downloader
        self._task = None

    def is_running(self) -> bool:
        return self._task is not None and self._task.isRunning()

    def download(self, release: UpdateInfo, current_version: AppVersion) -> bool:
        if self._task is not None:
            return False

        def worker(cancel_event):
            try:
                return self._downloader(
                    release,
                    current_version=current_version,
                    cancel_event=cancel_event,
                    progress=self._emit_progress,
                )
            except UpdateDownloadCancelled as exc:
                raise AITaskCancelled(str(exc)) from exc

        self._task = self._task_factory(worker, parent=self)
        self._task.success.connect(self._on_success)
        self._task.failed.connect(self._on_failed)
        self._task.cancelled.connect(self.cancelled)
        self._task.finished.connect(self._on_finished)
        self.started.emit(release)
        self._task.start()
        return True

    def cancel(self) -> bool:
        if self._task is None or not self._task.isRunning():
            return False
        self._task.cancel()
        return True

    def _emit_progress(self, progress: DownloadProgress) -> None:
        self.progress_changed.emit(progress.downloaded, progress.total)

    def _on_success(self, result: object) -> None:
        if isinstance(result, VerifiedUpdate):
            self.succeeded.emit(result)
        else:
            self.failed.emit("更新下载返回了无法识别的结果。")

    def _on_failed(self, message: str) -> None:
        self.failed.emit(str(message))

    def _on_finished(self) -> None:
        task = self._task
        self._task = None
        self.finished.emit()
        if task is not None:
            task.deleteLater()
