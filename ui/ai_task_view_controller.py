"""Presentation state for the background AI task lifecycle."""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox


MAX_OUTPUT_ENTRY_CHARS = 24_000
MAX_OUTPUT_BLOCKS = 1_200
TASK_LABELS = {
    "selection_expand": "选区扩写", "style_polish": "选区文风润色", "style_review": "文风审校",
    "expand": "按章纲生成正文",
    "continuation": "续写",
    "check": "设定检查",
    "memory": "记忆更新",
}


def limit_output_entry(text: object, limit: int = MAX_OUTPUT_ENTRY_CHARS) -> str:
    """Keep one output event bounded while retaining its beginning and end."""
    value = str(text or "")
    if len(value) <= limit:
        return value
    if limit < 100:
        return value[:limit]
    marker = "\n\n[… 此条记录过长，已折叠中间内容 …]\n\n"
    available = max(2, limit - len(marker))
    head = max(1, int(available * 0.7))
    tail = max(1, available - head)
    return value[:head] + marker + value[-tail:]


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
        task_panel=None,
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
        self.task_panel = task_panel
        self._has_output = bool(output_panel.toPlainText())
        self._task_started_at: float | None = None
        self._task_kind: str | None = None
        self._status_base = ""
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._refresh_elapsed)

        # QPlainTextEdit otherwise keeps every block for the lifetime of the
        # widget.  The visible panel is a diagnostic stream, not an archive.
        if hasattr(output_panel, "setMaximumBlockCount"):
            output_panel.setMaximumBlockCount(MAX_OUTPUT_BLOCKS)
        if hasattr(output_panel, "setUndoRedoEnabled"):
            output_panel.setUndoRedoEnabled(False)

        ai_controller.started.connect(self._on_started)
        ai_controller.failed.connect(self._on_failed)
        ai_controller.cancelled.connect(self._on_cancelled)
        ai_controller.finished.connect(self._on_finished)

    def append_output(self, text: str) -> None:
        entry = limit_output_entry(text)
        if not entry.strip():
            return
        scroll_bar = self.output_panel.verticalScrollBar()
        follow_tail = scroll_bar.value() >= scroll_bar.maximum() - 4
        if self._has_output:
            self.output_panel.appendPlainText("\n" + "—" * 28)
        self.output_panel.appendPlainText(entry)
        self._has_output = True
        if follow_tail:
            scroll_bar.setValue(scroll_bar.maximum())

    def clear_output(self) -> None:
        self.output_panel.clear()
        self._has_output = False

    def copy_output(self) -> bool:
        text = self.output_panel.toPlainText().strip()
        if not text:
            self.set_status("当前没有可复制的 AI 工作记录")
            return False
        QApplication.clipboard().setText(text)
        self.set_status("AI 工作记录已复制到剪贴板")
        return True

    def set_status(self, message: str) -> None:
        self._status_base = message
        self._display_status(message)

    def _refresh_elapsed(self) -> None:
        if self._task_started_at is None or self._task_kind != "memory":
            return
        if self.task_panel is not None and self.task_panel.state != "running":
            return
        elapsed = max(0, int(time.monotonic() - self._task_started_at))
        self._display_status(f"{self._status_base} · 已用 {elapsed} 秒")

    def _display_status(self, message: str) -> None:
        self.status_message.setText(message)
        if self.task_panel is not None and self.task_panel.state in {"running", "reviewing"}:
            self.task_panel.detail.setText(message)

    def cancel(self) -> bool:
        if not self.ai_controller.cancel():
            return False
        self.cancel_button.setEnabled(False)
        if self.task_panel is not None:
            self.task_panel.set_state("cancelling", "已请求取消，正在等待后台安全结束。")
        self.set_status("正在取消 AI 任务…")
        self.append_output("已请求取消当前 AI 任务。")
        return True

    def _on_started(self, _token) -> None:
        if self.task_panel is not None:
            self.task_panel.start(_token)
        self._task_started_at = time.monotonic()
        self._task_kind = getattr(_token, "kind", None)
        label = TASK_LABELS.get(self._task_kind, "AI")
        if self._task_kind == "memory":
            self._elapsed_timer.start()
        for key in ("expand", "continuation", "check", "memory"):
            self.actions[key].setEnabled(False)
        self.task_progress.show()
        self.cancel_button.show()
        self.ai_indicator.setText(f"●  DSH 处理中 · {label}")
        self.ai_indicator.setObjectName("aiStatusBusy")
        self.ai_indicator.style().unpolish(self.ai_indicator)
        self.ai_indicator.style().polish(self.ai_indicator)
        self.set_status(f"{label}任务已启动，正在后台处理…")
        chapter_id = getattr(_token, "chapter_id", "")
        self.append_output(f"▶ {label}任务已启动" + (f" · {chapter_id}" if chapter_id else ""))
        self.memory_page.set_syncing(True)
        self.window_state_controller.show_output((690, 190))

    def _on_finished(self, _token) -> None:
        self._elapsed_timer.stop()
        label = TASK_LABELS.get(self._task_kind, "AI")
        elapsed = ""
        if self._task_started_at is not None:
            elapsed = f" · 用时 {max(0.0, time.monotonic() - self._task_started_at):.1f} 秒"
        self.append_output(f"— {label}后台任务已结束{elapsed}")
        self.ai_engine_controller.cleanup_retired()
        for key in ("expand", "continuation", "check", "memory"):
            self.actions[key].setEnabled(True)
        self.task_progress.hide()
        self.cancel_button.hide()
        self.cancel_button.setEnabled(True)
        self.ai_indicator.setText("●  DSH 空闲中")
        self.ai_indicator.setObjectName("aiStatus")
        self.ai_indicator.style().unpolish(self.ai_indicator)
        self.ai_indicator.style().polish(self.ai_indicator)
        self.memory_page.set_syncing(False)
        self._task_started_at = None
        self._task_kind = None
        if self.task_panel is not None:
            self.task_panel.finish(_token)
            if self.task_panel.state == "pending":
                self.ai_indicator.setText("●  AI 结果待审阅")

    def _on_cancelled(self, _token) -> None:
        label = TASK_LABELS.get(getattr(_token, "kind", None), "AI")
        self.append_output(f"AI {label}任务已取消，未写入生成结果。")
        self.set_status("AI 任务已取消")
        if self.task_panel is not None:
            self.task_panel.set_state("cancelled", "任务已取消，未写入生成结果。")

    def _on_failed(self, _token, message: str) -> None:
        label = TASK_LABELS.get(getattr(_token, "kind", None), "AI")
        safe_message = limit_output_entry(message, 6_000)
        self.append_output(f"{label}任务失败\n{safe_message}")
        self.set_status("AI 任务失败")
        if self.task_panel is not None:
            self.task_panel.set_state("failed", "任务失败：" + safe_message[:400])
        else:
            QMessageBox.critical(self.parent, "DeepSeek Harness 调用失败", safe_message)
