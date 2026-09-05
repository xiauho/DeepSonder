"""Application settings, theme and DSH connection-test coordination."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from core.config import normalize_config, save_config
from core.dsh_client import DSHClient
from ui.ai_task_runner import DSHTask
from ui.ai_engine_controller import AIEngineController
from ui.document_controller import DocumentController
from ui.theme import apply_theme
from core.theme_tokens import DARK_COLORS, LIGHT_COLORS


class SettingsController(QObject):
    """Keep settings mutations in one place and expose UI-safe signals."""

    config_changed = Signal(object, str)
    connection_started = Signal()
    connection_succeeded = Signal(str)
    connection_failed = Signal(str)
    connection_finished = Signal()

    def __init__(
        self,
        config: dict,
        document_controller: DocumentController,
        engine_controller: AIEngineController | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._config = normalize_config(config)
        self._document_controller = document_controller
        self._engine_controller = engine_controller
        self._connection_task: DSHTask | None = None
        self._connection_client: DSHClient | None = None
        self._apply_runtime_settings()

    @property
    def config(self) -> dict:
        return dict(self._config)

    def synchronize(self, config: dict) -> None:
        """Adopt metadata persisted by another application controller."""
        self._config = normalize_config(config)

    def apply(self, config: dict, message: str = "设置已保存") -> dict:
        self._config = normalize_config(config)
        save_config(self._config)
        self._apply_runtime_settings()
        self.config_changed.emit(self.config, message)
        return self.config

    def toggle_theme(self) -> dict:
        next_theme = "dark" if self._config.get("theme", "light") == "light" else "light"
        updated = self.config
        updated["theme"] = next_theme
        updated.update(DARK_COLORS if next_theme == "dark" else LIGHT_COLORS)
        return self.apply(updated, message="主题已切换")

    def test_dsh(self, config: dict | None = None) -> bool:
        if self._connection_task is not None and self._connection_task.isRunning():
            return False
        test_config = normalize_config(config or self._config)
        client = DSHClient(
            dsh_command=test_config["dsh_command"],
            launcher_args=test_config["dsh_launcher_args"],
            profile="headless",
            timeout=min(15, int(test_config["dsh_timeout"])),
            extra_args=test_config["dsh_extra_args"],
            file_prompt_budget=test_config["dsh_file_prompt_budget"],
            task_file_max_bytes=test_config["dsh_task_file_max_bytes"],
            input_token_budget=test_config["ai_input_token_budget"],
            runtime_reserve_tokens=test_config["ai_runtime_reserve_tokens"],
            model_context_window_tokens=test_config["ai_model_context_window_tokens"],
            context_strategy=test_config["ai_context_strategy"],
        )
        client.use_isolated_workspace()
        self._connection_client = client
        self._connection_task = DSHTask(
            lambda _cancel_event: client.check_connection(),
            parent=self,
        )
        self._connection_task.success.connect(
            lambda result: self.connection_succeeded.emit(str(result))
        )
        self._connection_task.failed.connect(
            lambda error: self.connection_failed.emit(str(error))
        )
        self._connection_task.finished.connect(self._on_connection_finished)
        self.connection_started.emit()
        self._connection_task.start()
        return True

    def _apply_runtime_settings(self) -> None:
        self._document_controller.configure_auto_save(
            bool(self._config.get("auto_save", True)),
            int(self._config.get("auto_save_interval", 30)),
        )
        if self._engine_controller is not None:
            self._engine_controller.configure(self._config)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app, self._config)

    def _on_connection_finished(self) -> None:
        task = self._connection_task
        self._connection_task = None
        client = self._connection_client
        self._connection_client = None
        if client is not None:
            client.cleanup()
        self.connection_finished.emit()
        if task is not None:
            task.deleteLater()
