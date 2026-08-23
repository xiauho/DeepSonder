"""UI-level orchestration for the application's AI use cases."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from core import ai_protocol
from core.ai_result_service import AIResultService
from core.ai_workflow import AIWorkflowService
from core.config import save_config
from ui.ai_result_coordinator import AIResultCoordinator


class AIWorkflowController(QObject):
    """Coordinate AI preconditions, task starts, confirmations and results.

    Background execution remains in ``AIController`` and data validation or
    persistence remains in ``AIResultService``. This class only joins those
    pieces for the desktop application's expand/check/memory use cases.
    """

    output_requested = Signal(str)
    status_requested = Signal(str)

    def __init__(
        self,
        *,
        config: dict,
        project_session,
        editor,
        document_controller,
        ai_controller,
        ai_engine_controller,
        inspector,
        reports_page,
        go_to_writing: Callable[[], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.config = dict(config)
        self.project_session = project_session
        self.editor = editor
        self.document_controller = document_controller
        self.ai_controller = ai_controller
        self.ai_engine_controller = ai_engine_controller
        self.inspector = inspector
        self.reports_page = reports_page
        self.go_to_writing = go_to_writing
        self.parent = parent
        self.ai_result_service = AIResultService()
        self.ai_result_coordinator = AIResultCoordinator(parent)

        ai_controller.succeeded.connect(self._on_task_succeeded)

    def set_config(self, config: dict) -> None:
        self.config = dict(config)

    def expand(self) -> None:
        request = self._prepare_request()
        if request is None:
            return
        project, chapter_id, workflow = request
        target_chars = int(self.config.get("expand_target_chars", 2000))
        self._start(
            "expand",
            chapter_id,
            f"正在扩写 · {chapter_id}",
            lambda cancel_event: workflow.expand(
                project,
                chapter_id,
                target_chars=target_chars,
                cancel_event=cancel_event,
            ),
        )

    def check(self) -> None:
        request = self._prepare_request()
        if request is None:
            return
        project, chapter_id, workflow = request
        self._start(
            "check",
            chapter_id,
            f"正在检查设定 · {chapter_id}",
            lambda cancel_event: workflow.check(project, chapter_id, cancel_event=cancel_event),
        )

    def update_memory(self) -> None:
        request = self._prepare_request()
        if request is None:
            return
        project, chapter_id, workflow = request
        self._start(
            "memory",
            chapter_id,
            f"正在提炼章节摘要与故事状态 · {chapter_id}",
            lambda cancel_event: workflow.update_memory(
                project,
                chapter_id,
                cancel_event=cancel_event,
            ),
        )

    def _prepare_request(self):
        project = self.project_session.project
        if project is None:
            QMessageBox.information(self.parent, "尚未打开项目", "请先打开或新建一个小说项目。")
            return None
        self.go_to_writing()
        chapter_id = self.editor.current_chapter_id()
        if chapter_id is None:
            QMessageBox.information(self.parent, "需要章节", "请先从资料树打开一个章节。")
            self.go_to_writing()
            return None
        if not self._ensure_ai_notice() or not self._save_current_file():
            return None
        dsh = self.ai_engine_controller.client
        if dsh is None:
            QMessageBox.warning(
                self.parent,
                "AI 未就绪",
                "当前没有可用的 dsh 客户端，请先检查设置。",
            )
            return None
        return project, chapter_id, AIWorkflowService(dsh)

    def _save_current_file(self) -> bool:
        if not self.editor.current_path():
            QMessageBox.information(self.parent, "保存", "请先打开一份可编辑的故事资料。")
            return False
        if not self.document_controller.save():
            QMessageBox.warning(self.parent, "保存失败", "文件未能保存，请检查写入权限。")
            return False
        return True

    def _ensure_ai_notice(self) -> bool:
        if self.config.get("ai_notice_acknowledged", False):
            return True
        notice = QMessageBox(self.parent)
        notice.setIcon(QMessageBox.Icon.Information)
        notice.setWindowTitle("使用 AI 功能前请确认")
        notice.setText("AI 功能会将创作内容发送给本机配置的 dsh / DeepSeek Harness 处理。")
        notice.setInformativeText(
            "发送内容可能包括当前章节、故事大纲、角色与世界观设定、章节摘要和故事状态。\n\n"
            "请勿提交无权处理的作品或敏感个人信息。AI 输出可能存在错误，使用或公开前请自行审阅。"
        )
        notice.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        notice.button(QMessageBox.StandardButton.Ok).setText("了解并继续")
        notice.button(QMessageBox.StandardButton.Cancel).setText("暂不使用")
        if notice.exec() != QMessageBox.StandardButton.Ok:
            return False
        self.config["ai_notice_acknowledged"] = True
        save_config(self.config)
        return True

    def _start(self, kind: str, chapter_id: str, message: str, worker) -> bool:
        if self.ai_controller.is_running():
            QMessageBox.information(self.parent, "AI 正在工作", "当前任务完成后再试一次。")
            return False
        project = self.project_session.project
        if project is None:
            return False
        self._emit_output(message)
        if self.ai_controller.start(
            kind,
            project,
            chapter_id,
            self.editor.text_edit.toPlainText(),
            worker,
        ) is None:
            QMessageBox.information(self.parent, "AI 正在工作", "当前任务完成后再试一次。")
            return False
        return True

    def _on_task_succeeded(self, token, result) -> None:
        if token.kind == "expand":
            self._on_expansion_done(result)
        elif token.kind == "check":
            self._on_check_done(result)
        elif token.kind == "memory":
            self._on_memory_done(result)

    def _on_expansion_done(self, result: tuple[str, str | None]) -> None:
        raw, first_raw = result
        if first_raw is not None:
            self._emit_output(
                "首次返回未通过协议校验，已自动纠偏重试。首次原始返回（可人工挽救）：\n"
                f"{first_raw}"
            )
        target = int(self.config.get("expand_target_chars", 2000))
        try:
            parsed = self.ai_result_service.parse_expansion(raw, target)
        except ai_protocol.AIProtocolError as exc:
            self._emit_output(f"扩写结果无效\n{exc}\n原始返回：\n{raw}")
            self._emit_status("扩写结果无效，未写入正文")
            QMessageBox.warning(self.parent, "扩写结果无效", str(exc))
            return

        self._emit_output(
            f"✅ {parsed.completion_message}；扩写结果已通过格式校验（约 {parsed.char_count} 字），等待确认写入"
        )
        self._emit_status("扩写已完成，等待确认写入")
        chapter_id = self.ai_controller.task_chapter_id
        project = self.project_session.project
        chapter = project.load_chapter(chapter_id) if project and chapter_id else None
        has_existing_content = bool(chapter and chapter.content.strip())
        outcome = self.ai_result_coordinator.confirm_expansion(
            text=parsed.text,
            char_count=parsed.char_count,
            length_ok=parsed.length_ok,
            has_existing_content=has_existing_content,
            context_matches=self._task_context_matches,
            replace_body=self.editor.replace_chapter_body,
        )
        if outcome.status == "cancelled":
            self._emit_output("扩写结果未确认写入，未修改正文。")
            self._emit_status("扩写结果已放弃")
            return
        if outcome.status == "stale":
            self._emit_output("扩写结果未写入：章节内容或当前章节已发生变化。")
            self._emit_status("章节已变化，扩写结果仅保留在 AI 记录中")
            return
        action = outcome.action or ("替换" if has_existing_content else "写入")
        self._emit_status(f"扩写已{action}当前正文，请审阅后保存")
        self._emit_output(f"已确认{action}扩写结果，尚未自动保存。")

    def _on_check_done(self, result: str) -> None:
        try:
            report, rendered = self.ai_result_service.parse_consistency(result)
        except ai_protocol.AIProtocolError as exc:
            self._emit_output(f"一致性检查结果无效\n{exc}\n原始返回：\n{result}")
            self._emit_status("一致性检查结果无效")
            QMessageBox.warning(self.parent, "检查结果无效", str(exc))
            return
        completion_message = str(report.get("completion_message") or "一致性检查任务已完成")
        self._notify_complete(completion_message)
        self._emit_output(f"一致性检查完成\n{rendered}")
        self.inspector.show_text("一致性检查", rendered)
        self.reports_page.show_result(rendered)
        self._emit_status("一致性检查完成")

    def _on_memory_done(self, result: tuple[str, dict, str]) -> None:
        summary, new_state, completion_message = result
        try:
            draft = self.ai_result_service.prepare_memory(summary, new_state)
        except ValueError as exc:
            QMessageBox.warning(self.parent, "更新失败", str(exc))
            return
        self._emit_output(f"✅ {completion_message}；记忆结果已生成，等待确认写入")
        self._emit_status("记忆更新已完成，等待确认写入")
        chapter_id = self.ai_controller.task_chapter_id
        project = self.project_session.project
        if not chapter_id or project is None:
            self._emit_output("记忆更新已取消，未修改项目数据。")
            return
        outcome = self.ai_result_coordinator.confirm_memory(
            summary=draft.summary,
            context_matches=self._task_context_matches,
            commit=lambda: self.ai_result_service.commit_memory(project, chapter_id, draft),
        )
        if outcome.status == "cancelled":
            self._emit_output("记忆更新已取消，未修改项目数据。")
            return
        if outcome.status == "stale":
            self._emit_output("记忆更新结果已丢弃：项目或章节已切换。")
            return
        if outcome.status == "failed":
            self._emit_output(f"记忆更新失败：{outcome.error or '未知错误'}")
            self._emit_status("长期记忆更新失败")
            QMessageBox.critical(self.parent, "更新失败", outcome.error or "长期记忆写入失败。")
            return
        commit_result = outcome.value
        if commit_result is None:
            return
        if commit_result.chapter_was_corrected:
            self._emit_output(
                f"AI 返回的 current_chapter={commit_result.received_chapter} 与当前章节不符，"
                f"已按章节 {commit_result.expected_chapter} 修正。"
            )
        self._emit_output(f"长期记忆已更新\n{draft.summary}")
        self.project_session.notify_data_changed()
        self._emit_status("长期记忆已更新")

    def _task_context_matches(self) -> bool:
        return self.ai_controller.context_matches(
            self.project_session.project,
            self.editor.current_chapter_id() or "",
            self.editor.text_edit.toPlainText(),
        )

    def _notify_complete(self, message: str) -> None:
        message = str(message or "AI 任务已完成").strip()
        self._emit_output(f"✅ {message}")
        self._emit_status(message)
        QMessageBox.information(self.parent, "AI 任务完成", message)

    def _emit_output(self, message: str) -> None:
        self.output_requested.emit(message)

    def _emit_status(self, message: str) -> None:
        self.status_requested.emit(message)
