"""Presentation state for the background AI task lifecycle."""

from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox


class AITaskViewController(QObject):
    """Keep AI task controls and progress feedback out of MainWindow."""

    def __init__(
        self,
        *,
        ai_controller,
        ai_engine_controller,
        actions: dict,
        task_progress,
        cancel_button,
        ai_indicator,
        status_message,
        memory_page,
        output_panel,
        window_state_controller,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ai_controller = ai_controller
        self.ai_engine_controller = ai_engine_controller
        self.actions = actions
        self.task_progress = task_progress
        self.cancel_button = cancel_button
        self.ai_indicator = ai_indicator
        self.status_message = status_message
        self.memory_page = memory_page
        self.output_panel = output_panel
        self.window_state_controller = window_state_controller
        self.parent = parent

        ai_controller.started.connect(self._on_started)
        ai_controller.failed.connect(self._on_failed)
        ai_controller.cancelled.connect(self._on_cancelled)
        ai_controller.finished.connect(self._on_finished)

    def append_output(self, text: str) -> None:
        if self.output_panel.toPlainText():
            self.output_panel.appendPlainText("\n" + "—" * 28)
        self.output_panel.appendPlainText(text)

    def set_status(self, message: str) -> None:
        self.status_message.setText(message)

    def cancel(self) -> bool:
        if not self.ai_controller.cancel():
            return False
        self.cancel_button.setEnabled(False)
        self.set_status("正在取消 AI 任务…")
        self.append_output("已请求取消当前 AI 任务。")
        return True

    def _on_started(self, _token) -> None:
        for key in ("continue", "check", "memory"):
            self.actions[key].setEnabled(False)
        self.task_progress.show()
        self.cancel_button.show()
        self.ai_indicator.setText("●  DSH 处理中")
        self.ai_indicator.setObjectName("aiStatusBusy")
        self.ai_indicator.style().unpolish(self.ai_indicator)
        self.ai_indicator.style().polish(self.ai_indicator)
        self.set_status("DeepSeek Harness 正在整理故事上下文…")
        self.memory_page.set_syncing(True)
        self.window_state_controller.show_output((690, 190))

    def _on_finished(self, _token) -> None:
        self.ai_engine_controller.cleanup_retired()
        for key in ("continue", "check", "memory"):
            self.actions[key].setEnabled(True)
        self.task_progress.hide()
        self.cancel_button.hide()
        self.cancel_button.setEnabled(True)
        self.ai_indicator.setText("●  DSH 空闲中")
        self.ai_indicator.setObjectName("aiStatus")
        self.ai_indicator.style().unpolish(self.ai_indicator)
        self.ai_indicator.style().polish(self.ai_indicator)
        self.memory_page.set_syncing(False)

    def _on_cancelled(self, _token) -> None:
        self.append_output("AI 任务已取消，未写入生成结果。")
        self.set_status("AI 任务已取消")

    def _on_failed(self, _token, message: str) -> None:
        self.append_output(f"任务失败\n{message}")
        self.set_status("AI 任务失败")
        QMessageBox.critical(self.parent, "DeepSeek Harness 调用失败", message)
