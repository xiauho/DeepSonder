"""Background update-check coordination and scheduling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from PySide6.QtCore import QObject, Signal

from core.config import normalize_config, save_config
from core.update_service import UpdateCheckResult, check_for_updates
from core.version import load_current_version
from ui.ai_task_runner import DSHTask


AUTO_CHECK_INTERVAL = timedelta(hours=24)


def should_auto_check(config: dict, now: datetime | None = None) -> bool:
    """Return whether an opted-in automatic check is due."""
    if not bool(config.get("auto_check_updates", False)):
        return False
    last_value = str(config.get("last_update_check_at") or "").strip()
    if not last_value:
        return True
    try:
        last_check = datetime.fromisoformat(last_value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if last_check.tzinfo is None:
        last_check = last_check.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current - last_check >= AUTO_CHECK_INTERVAL


class UpdateController(QObject):
    """Run one GitHub update lookup without blocking the Qt main thread."""

    started = Signal(bool)
    result_ready = Signal(object, bool)
    failed = Signal(str, bool)
    finished = Signal(bool)
    config_changed = Signal(object)

    def __init__(
        self,
        config: dict,
        parent=None,
        *,
        task_factory: Callable = DSHTask,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = normalize_config(config)
        self._task_factory = task_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._task = None
        self._manual = False

    @property
    def config(self) -> dict:
        return dict(self._config)

    def set_config(self, config: dict) -> None:
        self._config = normalize_config(config)

    def is_running(self) -> bool:
        return self._task is not None and self._task.isRunning()

    def check_automatically(self) -> bool:
        if not should_auto_check(self._config, self._clock()):
            return False
        return self.check(manual=False)

    def check(self, *, manual: bool) -> bool:
        if self._task is not None:
            return False
        try:
            current_version = load_current_version()
        except ValueError as exc:
            if manual:
                self.failed.emit(str(exc), True)
            return False

        channel = self._config.get("update_channel", "beta")
        self._manual = bool(manual)
        self._task = self._task_factory(
            lambda _cancel_event: check_for_updates(current_version, channel),
            parent=self,
        )
        self._task.success.connect(self._on_success)
        self._task.failed.connect(self._on_failed)
        self._task.finished.connect(self._on_finished)
        self.started.emit(self._manual)
        self._task.start()
        return True

    def skip_version(self, tag_name: str) -> None:
        self._config["skipped_update_version"] = str(tag_name or "").strip()
        self._persist_config()

    def should_present(self, result: UpdateCheckResult, *, manual: bool) -> bool:
        if not result.update_available or result.latest is None:
            return False
        if manual:
            return True
        skipped = str(self._config.get("skipped_update_version") or "").strip()
        return skipped != result.latest.tag_name

    def _on_success(self, result: object) -> None:
        if not isinstance(result, UpdateCheckResult):
            self.failed.emit("更新检查返回了无法识别的结果。", self._manual)
            return
        checked_at = self._clock()
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        self._config["last_update_check_at"] = checked_at.astimezone(
            timezone.utc
        ).isoformat().replace("+00:00", "Z")
        self._persist_config()
        self.result_ready.emit(result, self._manual)

    def _on_failed(self, message: str) -> None:
        self.failed.emit(str(message), self._manual)

    def _on_finished(self) -> None:
        task = self._task
        manual = self._manual
        self._task = None
        self.finished.emit(manual)
        if task is not None:
            task.deleteLater()

    def _persist_config(self) -> None:
        try:
            save_config(self._config)
        except OSError:
            pass
        self.config_changed.emit(self.config)
