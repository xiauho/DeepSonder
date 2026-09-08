"""UI-level orchestration for the application's AI use cases."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QDialog, QMessageBox

from core import ai_protocol
from core.character_cards import create_character_cards, missing_character_cards
from core.chapter_memory import ChapterMemoryProposal
from core.continuation import (
    ContinuationNotAvailable,
    ContinuationRunResult,
    continuation_target_chars,
)
from core.ai_result_service import AIResultService
from core.ai_workflow import AIWorkflowService
from core.config import get_chapter_target_chars, save_config
from core.expansion import ExpansionRunResult
from core.length_policy import assess_length
from core.prose_supplement import ProseSupplementRunResult
from core.project_data import ProjectDataStore
from core.text_anchor import enrich_report_anchors, resolve_text_anchor
from core.text_metrics import count_content_chars
from core.token_budget import (
    DEFAULT_CHUNK_OVERLAP_TOKENS,
    DEFAULT_CHUNK_TOKEN_BUDGET,
    DEFAULT_INPUT_TOKEN_BUDGET,
)
from ui.ai_result_coordinator import AIResultCoordinator
from ui.chapter_selection_dialog import ChapterSelectionDialog
from ui.expansion_context_selection_dialog import ExpansionContextSelectionDialog


MAX_MANUAL_SUPPLEMENT_ATTEMPTS = 3


@dataclass(frozen=True)
class PendingForeshadowingResolution:
    project_root: str
    chapter_id: str
    note_ids: tuple[str, ...]


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
        go_to_writing: Callable[[], bool],
        save_if_dirty: Callable[[], bool] | None = None,
        left_panel=None,
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
        self.save_if_dirty = save_if_dirty
        self.left_panel = left_panel
        self.parent = parent
        self.ai_result_service = AIResultService()
        self.ai_result_coordinator = AIResultCoordinator(parent)
        self._pending_foreshadowing_resolutions: dict[
            str, PendingForeshadowingResolution
        ] = {}
        self._plain_text_fallback_count = 0

        ai_controller.succeeded.connect(self._on_task_succeeded)
        report_signal = getattr(ai_engine_controller, "context_reported", None)
        if report_signal is not None and hasattr(report_signal, "connect"):
            report_signal.connect(self._on_context_reported)
        document_controller.document_saved.connect(self._on_document_saved)
        project_session.project_changed.connect(self._on_project_changed)

    def set_config(self, config: dict) -> None:
        self.config = dict(config)

    def expand(self) -> None:
        request = self._prepare_request()
        if request is None:
            return
        project, chapter_id, workflow = request
        selection = self._choose_expansion_context(project, chapter_id)
        if selection is None:
            return
        selected_foreshadowing, selected_power = selection
        selected_foreshadowing = tuple(deepcopy(selected_foreshadowing))
        selected_power = tuple(str(path) for path in selected_power)
        target_chars = get_chapter_target_chars(self.config)
        history_chapters = int(self.config.get("ai_context_history_chapters", 5))
        history_mode = str(self.config.get("ai_history_mode", "custom"))
        history_remote_enabled = bool(self.config.get("ai_history_remote_enabled", True))
        self._start(
            "expand",
            chapter_id,
            f"正在扩写 · {chapter_id}",
            lambda cancel_event: workflow.expand(
                project,
                chapter_id,
                target_chars=target_chars,
                history_chapters=history_chapters,
                history_mode=history_mode,
                history_remote_enabled=history_remote_enabled,
                selected_foreshadowing=selected_foreshadowing,
                selected_power=selected_power,
                cancel_event=cancel_event,
            ),
            task_context={
                "target_chars": target_chars,
                "selected_foreshadowing": selected_foreshadowing,
                "selected_power": selected_power,
            },
        )

    def continue_chapter(self) -> None:
        request = self._prepare_request()
        if request is None:
            return
        project, chapter_id, workflow = request
        target_chars = get_chapter_target_chars(self.config)
        try:
            chapter = project.load_chapter(chapter_id)
            current_chars = count_content_chars(chapter.content)
            requested_chars = continuation_target_chars(current_chars, target_chars)
        except ContinuationNotAvailable as exc:
            QMessageBox.information(self.parent, "暂不适合续写", str(exc))
            self._emit_status(str(exc))
            return
        except (OSError, UnicodeError, ValueError) as exc:
            QMessageBox.warning(self.parent, "读取章节失败", str(exc))
            return
        history_chapters = int(self.config.get("ai_context_history_chapters", 5))
        history_mode = str(self.config.get("ai_history_mode", "custom"))
        history_remote_enabled = bool(self.config.get("ai_history_remote_enabled", True))
        self._start(
            "continuation",
            chapter_id,
            f"正在续写 · {chapter_id} · 目标约 {requested_chars} 字",
            lambda cancel_event: workflow.continue_chapter(
                project,
                chapter_id,
                target_chapter_chars=target_chars,
                history_chapters=history_chapters,
                history_mode=history_mode,
                history_remote_enabled=history_remote_enabled,
                cancel_event=cancel_event,
            ),
            task_context={
                "current_tail": chapter.content[-300:],
                "current_chars": current_chars,
                "requested_chars": requested_chars,
                "target_chapter_chars": target_chars,
            },
        )

    def check(self) -> None:
        request = self._prepare_request()
        if request is None:
            return
        project, chapter_id, workflow = request
        history_remote_enabled = bool(self.config.get("ai_history_remote_enabled", True))
        self._start(
            "check",
            chapter_id,
            f"正在检查设定 · {chapter_id}",
            lambda cancel_event: workflow.check(
                project,
                chapter_id,
                cancel_event=cancel_event,
                history_remote_enabled=history_remote_enabled,
            ),
        )

    def check_from_reports(self) -> None:
        """Choose a chapter and run consistency checking without leaving reports."""
        project = self.project_session.project
        if project is None:
            QMessageBox.information(self.parent, "尚未打开项目", "请先打开或新建一个小说项目。")
            return
        try:
            chapters = []
            for path in project.list_chapters():
                chapter = project.load_chapter(path.stem)
                chapters.append((path.stem, chapter.title or path.stem, path))
        except (OSError, UnicodeError, ValueError) as exc:
            QMessageBox.warning(self.parent, "读取章节失败", str(exc))
            return
        if not chapters:
            QMessageBox.information(self.parent, "没有章节", "当前项目还没有可检查的章节。")
            return
        current_chapter_id = self.editor.current_chapter_id()
        if not current_chapter_id:
            previous = getattr(self.reports_page, "last_report_chapter_id", None)
            current_chapter_id = previous() if callable(previous) else None
        dialog = ChapterSelectionDialog(
            chapters,
            default_chapter_id=current_chapter_id,
            parent=self.parent,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._emit_status("已取消一致性检查")
            return
        chapter_id = dialog.selected_chapter_id()
        if not chapter_id:
            QMessageBox.information(self.parent, "需要选择章节", "请选择一个章节后再运行检查。")
            return
        if not self._ensure_ai_notice() or not self._save_dirty_document():
            return
        dsh = self.ai_engine_controller.client
        if dsh is None:
            QMessageBox.warning(
                self.parent,
                "AI 未就绪",
                "当前没有可用的 dsh 客户端，请先检查设置。",
            )
            return
        try:
            chapter = project.load_chapter(chapter_id)
        except (OSError, UnicodeError, ValueError) as exc:
            QMessageBox.warning(self.parent, "读取章节失败", str(exc))
            return
        title = chapter.title or chapter_id
        workflow = self._workflow(dsh)
        history_remote_enabled = bool(self.config.get("ai_history_remote_enabled", True))
        self._start(
            "check",
            chapter_id,
            f"正在检查设定 · {title}",
            lambda cancel_event: workflow.check(
                project,
                chapter_id,
                cancel_event=cancel_event,
                history_remote_enabled=history_remote_enabled,
            ),
            task_context={
                "origin": "reports",
                "chapter_title": title,
                "source_text": chapter.raw,
            },
            editor_text=chapter.raw,
        )

    def update_memory(self, *, force_refresh: bool = False) -> None:
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
                force_refresh=force_refresh,
            ),
        )

    def jump_to_issue(self, issue: object) -> bool:
        """Open the reported chapter and reveal its exact quoted passage."""
        if not isinstance(issue, dict):
            return False
        project = self.project_session.project
        chapter_id = str(issue.get("chapter_id") or "").strip()
        quote = str(issue.get("chapter_quote") or "").strip()
        if project is None or not chapter_id or not quote:
            QMessageBox.information(self.parent, "无法定位正文", "该问题没有可用的章节原句锚点。")
            return False
        if not self.go_to_writing():
            return False
        path = project.chapters_dir / f"{chapter_id}.md"
        if not path.exists():
            QMessageBox.warning(self.parent, "无法定位正文", f"章节文件不存在：{path.name}")
            return False
        if not self.document_controller.open_file("章节", path):
            return False
        if self.left_panel is not None:
            reveal = getattr(self.left_panel, "reveal_path", None)
            if callable(reveal):
                reveal(path)
        resolution = resolve_text_anchor(self.editor.text_edit.toPlainText(), issue.get("chapter_anchor") or quote)
        if resolution.start is None or resolution.end is None:
            QMessageBox.warning(
                self.parent,
                "正文原句已变化",
                "已打开对应章节，但报告中的原句在当前正文中找不到唯一位置，请重新运行检查。",
            )
            return False
        self.editor.reveal_range(resolution.start, resolution.end)
        self._emit_status("已跳转到一致性问题所在正文")
        return True

    def repair_issue(self, issue: object) -> None:
        if not isinstance(issue, dict):
            return
        if issue.get("recommended_target") != "chapter" or issue.get("repairability") != "automatic":
            QMessageBox.information(self.parent, "暂不支持自动修复", "该问题需要人工判断后处理。")
            return
        project = self.project_session.project
        chapter_id = str(issue.get("chapter_id") or "").strip()
        quote = str(issue.get("chapter_quote") or "").strip()
        if project is None or not chapter_id or not quote:
            QMessageBox.information(self.parent, "无法修复", "该问题没有可用的正文原句锚点。")
            return
        if not self.go_to_writing():
            return
        if not self._ensure_ai_notice():
            return
        path = project.chapters_dir / f"{chapter_id}.md"
        if not path.exists() or not self.document_controller.open_file("章节", path):
            return
        # Repair prompts read the authoritative project files in the worker;
        # persist an already-open dirty target before capturing that context.
        if not self._save_current_file():
            return
        if self.left_panel is not None:
            reveal = getattr(self.left_panel, "reveal_path", None)
            if callable(reveal):
                reveal(path)
        resolution = resolve_text_anchor(self.editor.text_edit.toPlainText(), issue.get("chapter_anchor") or quote)
        if resolution.start is None or resolution.end is None or resolution.status == "ambiguous":
            QMessageBox.warning(self.parent, "无法安全修复", "正文原句不再唯一匹配，请重新运行检查后再试。")
            return
        current_text = self.editor.text_edit.toPlainText()
        current_quote = current_text[resolution.start : resolution.end]
        issue_for_task = dict(issue)
        issue_for_task["chapter_quote"] = current_quote
        issue_for_task["chapter_anchor"] = {
            "quote": current_quote,
            "start": resolution.start,
            "end": resolution.end,
        }
        dsh = self.ai_engine_controller.client
        if dsh is None:
            QMessageBox.warning(self.parent, "AI 未就绪", "当前没有可用的 dsh 客户端，请先检查设置。")
            return
        workflow = self._workflow(dsh)
        self._start(
            "repair",
            chapter_id,
            f"正在生成修复方案 · {chapter_id}",
            lambda cancel_event: workflow.repair_consistency(
                project, chapter_id, issue_for_task, cancel_event=cancel_event
            ),
            task_context={"issue": issue_for_task},
        )

    def _prepare_request(self):
        project = self.project_session.project
        if project is None:
            QMessageBox.information(self.parent, "尚未打开项目", "请先打开或新建一个小说项目。")
            return None
        if not self.go_to_writing():
            return None
        chapter_id = self.editor.current_chapter_id()
        if chapter_id is None:
            QMessageBox.information(self.parent, "需要章节", "请先从资料树打开一个章节。")
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
        return project, chapter_id, self._workflow(dsh)

    def _workflow(self, dsh) -> AIWorkflowService:
        """Create a workflow with the same normalized token policy as the UI."""
        return AIWorkflowService(
            dsh,
            input_token_budget=int(
                self.config.get("ai_input_token_budget", DEFAULT_INPUT_TOKEN_BUDGET)
            ),
            chunk_token_budget=int(
                self.config.get("ai_chunk_token_budget", DEFAULT_CHUNK_TOKEN_BUDGET)
            ),
            chunk_overlap_tokens=int(
                self.config.get(
                    "ai_chunk_overlap_tokens",
                    DEFAULT_CHUNK_OVERLAP_TOKENS,
                )
            ),
        )

    def _save_current_file(self) -> bool:
        if not self.editor.current_path():
            QMessageBox.information(self.parent, "保存", "请先打开一份可编辑的故事资料。")
            return False
        save = self.save_if_dirty or self.document_controller.save_if_dirty
        if not save():
            QMessageBox.warning(self.parent, "保存失败", "文件未能保存，请检查写入权限。")
            return False
        return True

    def _save_dirty_document(self) -> bool:
        """Save the visible document only when it has unsaved changes."""
        if not self.editor.is_dirty():
            return True
        save = self.save_if_dirty or self.document_controller.save_if_dirty
        if save():
            return True
        QMessageBox.warning(self.parent, "保存失败", "文件未能保存，请检查写入权限。")
        return False

    def _ensure_ai_notice(self) -> bool:
        if self.config.get("ai_notice_acknowledged", False):
            return True
        notice = QMessageBox(self.parent)
        notice.setIcon(QMessageBox.Icon.Information)
        notice.setWindowTitle("使用 AI 功能前请确认")
        notice.setText("AI 功能会将创作内容发送给本机配置的 dsh / DeepSeek Harness 处理。")
        notice.setInformativeText(
            "发送内容可能包括当前章节、故事大纲、写作风格指南、角色与世界观设定、章节摘要和故事状态。\n\n"
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

    def _choose_expansion_context(
        self, project, chapter_id: str
    ) -> tuple[list[dict], list[str]] | None:
        store = ProjectDataStore(project)
        try:
            notes = store.load_foreshadowing(status="open")
            core_system_paths = store.list_core_systems()
            power_paths = store.list_non_core_systems()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self.parent, "读取扩写资料失败", str(exc))
            return None
        dialog = ExpansionContextSelectionDialog(
            notes,
            power_paths,
            chapter_id,
            core_system_paths=core_system_paths,
            core_power_path=store.core_power_path,
            style_guide_path=store.style_guide_path,
            style_guide_active=bool(store.load_style_guide()),
            parent=self.parent,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._emit_status("已取消扩写，未启动 AI 任务")
            return None
        selected_notes = dialog.selected_notes()
        selected_power = dialog.selected_power_paths()
        style_status = "已自动应用项目写作风格" if store.load_style_guide() else "项目写作风格尚未填写"
        self._emit_output(
            f"本次扩写已选择 {len(selected_notes)} 条伏笔、自动纳入 {len(core_system_paths)} 项核心体系、"
            f"手动选择 {max(0, len(selected_power) - len(core_system_paths))} 项非核心体系；"
            f"其余体系将作为低优先级背景资料；{style_status}。"
        )
        return selected_notes, selected_power

    def _start(
        self,
        kind: str,
        chapter_id: str,
        message: str,
        worker,
        *,
        task_context: object | None = None,
        editor_text: str | None = None,
    ) -> bool:
        project = self.project_session.project
        if project is None:
            return False
        self._emit_output(message)
        token = self.ai_controller.start(
            kind,
            project,
            chapter_id,
            self.editor.text_edit.toPlainText() if editor_text is None else editor_text,
            worker,
            task_context=task_context,
        )
        if token is None:
            QMessageBox.information(self.parent, "AI 正在工作", "当前任务完成后再试一次。")
            return False
        begin_report = getattr(self.inspector, "begin_context_report", None)
        if callable(begin_report):
            begin_report(kind, chapter_id)
        return True

    def _on_context_reported(self, report) -> None:
        show_report = getattr(self.inspector, "show_context_report", None)
        if callable(show_report):
            show_report(report)

    def _on_task_succeeded(self, token, result) -> None:
        try:
            if token.kind == "expand":
                self._on_expansion_done(token, result)
            elif token.kind == "continuation":
                self._on_continuation_done(token, result)
            elif token.kind == "writing_supplement":
                self._on_writing_supplement_done(token, result)
            elif token.kind == "check":
                self._on_check_done(token, result)
            elif token.kind == "repair":
                self._on_repair_done(token, result)
            elif token.kind == "memory":
                self._on_memory_done(token, result)
        finally:
            # The worker's finished signal can be processed while a preview or
            # confirmation dialog is open.  Release the token-scoped snapshot
            # only after the result handler has made its commit decision.
            self.ai_controller.release_result(token)

    def _on_expansion_done(self, token, result: ExpansionRunResult) -> None:
        raw = result.raw_output
        first_raw = result.first_raw_output
        self._record_plain_text_fallbacks(result.plain_text_fallback_count)
        if first_raw is not None:
            self._emit_output(
                "首次返回未通过协议校验，已自动纠偏重试。首次原始返回（可人工挽救）：\n"
                f"{first_raw}"
            )
        task_context_getter = getattr(self.ai_controller, "result_context", None)
        task_context = task_context_getter(token) if callable(task_context_getter) else None
        target = result.target_chars
        selected_foreshadowing = ()
        if isinstance(task_context, dict):
            raw_selected = task_context.get("selected_foreshadowing")
            if isinstance(raw_selected, (list, tuple)):
                selected_foreshadowing = tuple(
                    note for note in raw_selected if isinstance(note, dict)
                )
        try:
            parsed = self.ai_result_service.parse_expansion(
                raw,
                target,
                chapter_id=token.chapter_id,
                selected_foreshadowing=selected_foreshadowing,
            )
        except ai_protocol.AIProtocolError as exc:
            self._emit_output(f"扩写结果无效\n{exc}\n原始返回：\n{raw}")
            self._emit_status("扩写结果无效，未写入正文")
            QMessageBox.warning(self.parent, "扩写结果无效", str(exc))
            return

        if parsed.feedback_warning:
            self._emit_output(parsed.feedback_warning)
        if result.supplement_warning:
            self._emit_output(result.supplement_warning)

        self._emit_output(
            f"✅ {parsed.completion_message}；本次目标 {result.target_chars} 字，"
            f"最终约 {parsed.char_count} 字，等待确认写入"
        )
        self._emit_status("扩写已完成，等待确认写入")
        chapter_id = token.chapter_id
        project = self.project_session.project
        chapter = project.load_chapter(chapter_id) if project and chapter_id else None
        has_existing_content = bool(chapter and chapter.content.strip())
        foreshadowing_titles = {
            str(note.get("id") or ""): str(note.get("title") or "未命名伏笔")
            for note in selected_foreshadowing
        }
        outcome = self.ai_result_coordinator.confirm_expansion(
            text=parsed.text,
            char_count=parsed.char_count,
            length_ok=parsed.length_ok,
            has_existing_content=has_existing_content,
            context_matches=lambda: self._task_context_matches(token),
            replace_body=self.editor.replace_chapter_body,
            target_chars=result.target_chars,
            min_chars=result.min_chars,
            max_chars=result.max_chars,
            initial_char_count=result.initial_char_count,
            supplement_attempted=result.supplement_attempted,
            supplement_added_chars=result.supplement_added_chars,
            supplement_warning=result.supplement_warning,
            review_min_chars=result.review_min_chars,
            review_max_chars=result.review_max_chars,
            length_status=result.length_status,
            supplement_attempt_count=result.supplement_attempt_count,
            can_retry_supplement=(
                result.supplement_attempt_count < MAX_MANUAL_SUPPLEMENT_ATTEMPTS
            ),
            original_draft_text=result.original_draft_text,
            original_draft_char_count=result.original_draft_char_count,
            correction_history=result.correction_history,
            foreshadowing_feedback=parsed.foreshadowing_feedback,
            foreshadowing_titles=foreshadowing_titles,
            foreshadowing_warning=parsed.feedback_warning,
        )
        if outcome.status == "supplement_requested":
            self._queue_manual_supplement(
                token,
                writing_kind="expand",
                candidate_text=(
                    str(outcome.value) if isinstance(outcome.value, str) else parsed.text
                ),
                supplement_target_chars=result.target_chars,
                metadata={
                    "target_chars": result.target_chars,
                    "initial_char_count": result.initial_char_count,
                    "supplement_added_total": result.supplement_added_chars,
                    "supplement_attempt_count": result.supplement_attempt_count,
                    "length_retry_attempted": result.length_retry_attempted,
                    "length_retry_applied": result.length_retry_applied,
                    "original_draft_text": result.original_draft_text,
                    "original_draft_char_count": result.original_draft_char_count,
                    "correction_history": result.correction_history,
                },
            )
            return
        if outcome.status == "cancelled":
            self._emit_output("扩写结果未确认写入，未修改正文。")
            self._emit_status("扩写结果已放弃")
            return
        if outcome.status == "stale":
            self._emit_output("扩写结果未写入：章节内容或当前章节已发生变化。")
            self._emit_status("章节已变化，扩写结果仅保留在 AI 记录中")
            return
        action = outcome.action or ("替换" if has_existing_content else "写入")
        current_path = self.editor.current_path()
        if current_path:
            path_key = self._path_key(current_path)
            resolution_ids = tuple(
                str(note_id)
                for note_id in (outcome.value or ())
                if str(note_id or "").strip()
            )
            if resolution_ids and project is not None and chapter_id:
                self._pending_foreshadowing_resolutions[path_key] = (
                    PendingForeshadowingResolution(
                        project_root=str(project.root.resolve()).casefold(),
                        chapter_id=chapter_id,
                        note_ids=resolution_ids,
                    )
                )
                self._emit_output(
                    f"已暂存 {len(resolution_ids)} 条伏笔状态修改，将在章节保存成功后应用。"
                )
            else:
                self._pending_foreshadowing_resolutions.pop(path_key, None)
        self._emit_status(f"扩写已{action}当前正文，请审阅后保存")
        self._emit_output(f"已确认{action}扩写结果，尚未自动保存。")

    def _on_continuation_done(
        self, token, result: ContinuationRunResult
    ) -> None:
        self._record_plain_text_fallbacks(result.plain_text_fallback_count)
        if result.first_raw_output is not None:
            self._emit_output(
                "首次返回未通过续写协议校验，已自动纠偏重试。首次原始返回（可人工挽救）：\n"
                f"{result.first_raw_output}"
            )
        try:
            parsed = self.ai_result_service.parse_continuation(
                result.raw_output,
                result.requested_chars,
            )
        except ai_protocol.AIProtocolError as exc:
            self._emit_output(f"续写结果无效\n{exc}\n原始返回：\n{result.raw_output}")
            self._emit_status("续写结果无效，未追加正文")
            QMessageBox.warning(self.parent, "续写结果无效", str(exc))
            return
        if parsed.protocol_warning:
            self._emit_output(parsed.protocol_warning)
        task_context_getter = getattr(self.ai_controller, "result_context", None)
        task_context = task_context_getter(token) if callable(task_context_getter) else None
        current_tail = ""
        if isinstance(task_context, dict):
            current_tail = str(task_context.get("current_tail") or "")
        self._emit_output(
            f"✅ {parsed.completion_message}；续写格式有效，最终新增约 {parsed.char_count} 字，等待确认追加"
        )
        self._emit_status("续写已完成，等待确认追加")
        outcome = self.ai_result_coordinator.confirm_continuation(
            text=parsed.text,
            current_tail=current_tail,
            current_chars=result.current_chars,
            requested_chars=result.requested_chars,
            generated_chars=parsed.char_count,
            target_chapter_chars=result.target_chapter_chars,
            length_ok=result.length_status == "qualified",
            context_matches=lambda: self._task_context_matches(token),
            append_body=self.editor.append_chapter_body,
            min_chars=result.min_chars,
            max_chars=result.max_chars,
            review_min_chars=result.review_min_chars,
            review_max_chars=result.review_max_chars,
            run_target_chars=result.run_target_chars,
            initial_generated_chars=result.initial_generated_chars,
            supplement_added_chars=result.supplement_added_chars,
            supplement_warning=result.supplement_warning,
            length_status=result.length_status,
            supplement_attempt_count=result.supplement_attempt_count,
            can_retry_supplement=(
                result.supplement_attempt_count < MAX_MANUAL_SUPPLEMENT_ATTEMPTS
            ),
            original_draft_text=result.original_draft_text,
            original_draft_char_count=result.original_draft_char_count,
            correction_history=result.correction_history,
        )
        if outcome.status == "supplement_requested":
            self._queue_manual_supplement(
                token,
                writing_kind="continuation",
                candidate_text=(
                    str(outcome.value) if isinstance(outcome.value, str) else parsed.text
                ),
                supplement_target_chars=result.requested_chars,
                metadata={
                    "current_chars": result.current_chars,
                    "requested_chars": result.requested_chars,
                    "target_chapter_chars": result.target_chapter_chars,
                    "run_target_chars": result.run_target_chars,
                    "initial_generated_chars": result.initial_generated_chars,
                    "supplement_added_total": result.supplement_added_chars,
                    "supplement_attempt_count": result.supplement_attempt_count,
                    "current_tail": current_tail,
                    "length_retry_attempted": result.length_retry_attempted,
                    "length_retry_applied": result.length_retry_applied,
                    "original_draft_text": result.original_draft_text,
                    "original_draft_char_count": result.original_draft_char_count,
                    "correction_history": result.correction_history,
                },
            )
            return
        if outcome.status == "cancelled":
            self._emit_output("续写结果已放弃，未修改正文。")
            self._emit_status("续写结果已放弃")
            return
        if outcome.status == "stale":
            self._emit_output("续写结果未追加：章节内容或相关资料已经发生变化。")
            self._emit_status("章节已变化，续写结果仅保留在 AI 记录中")
            return
        self._emit_output("已确认追加续写结果，尚未自动保存。")
        self._emit_status("续写已追加到当前正文，请审阅后保存")

    def _queue_manual_supplement(
        self,
        token,
        *,
        writing_kind: str,
        candidate_text: str,
        supplement_target_chars: int,
        metadata: dict,
    ) -> None:
        """Start an author-requested supplement after the current result is released."""
        context_getter = getattr(self.ai_controller, "result_context", None)
        original_context = context_getter(token) if callable(context_getter) else None
        task_context = dict(original_context) if isinstance(original_context, dict) else {}
        task_context.update(metadata)
        task_context.update(
            {
                "writing_kind": str(writing_kind),
                "candidate_text": str(candidate_text),
                "supplement_target_chars": int(supplement_target_chars),
            }
        )
        chapter_id = token.chapter_id
        self._emit_status("正在准备重新补写")

        def launch_when_idle() -> None:
            if self.ai_controller.is_running():
                QTimer.singleShot(50, launch_when_idle)
                return
            project = self.project_session.project
            dsh = self.ai_engine_controller.client
            if project is None or dsh is None:
                QMessageBox.warning(self.parent, "无法补写", "项目或 AI 引擎已经不可用。")
                return
            workflow = self._workflow(dsh)
            chapter = project.load_chapter(chapter_id)
            story_constraints = "\n".join(
                (
                    f"章节：{chapter.title}",
                    "本章大纲：" + (chapter.outline or "（暂无）"),
                    "剧情简写：" + (chapter.plot_brief or "（暂无）"),
                )
            )[:6000]
            task_label = (
                "chapter_expansion_supplement"
                if writing_kind == "expand"
                else "continuation_supplement"
            )
            self._start(
                "writing_supplement",
                chapter_id,
                f"正在重新补写 · {chapter_id}",
                lambda cancel_event: workflow.supplement_prose(
                    chapter_id,
                    candidate_text,
                    supplement_target_chars,
                    cancel_event=cancel_event,
                    task_kind=task_label,
                    story_constraints=story_constraints,
                ),
                task_context=task_context,
            )

        QTimer.singleShot(0, launch_when_idle)

    def _on_writing_supplement_done(
        self,
        token,
        result: ProseSupplementRunResult,
    ) -> None:
        context_getter = getattr(self.ai_controller, "result_context", None)
        context = context_getter(token) if callable(context_getter) else None
        if not isinstance(context, dict):
            QMessageBox.warning(self.parent, "补写结果无效", "缺少原始候选正文信息。")
            return
        attempts = int(context.get("supplement_attempt_count", 0)) + 1
        total_added = int(context.get("supplement_added_total", 0)) + result.added_char_count
        warning = result.warning
        history = list(context.get("correction_history") or ())
        if result.applied:
            history.append(f"手动差额补写：新增 {result.added_char_count} 字，已安全应用。")
        else:
            history.append("手动差额补写未应用：" + (warning or "没有有效插入项。"))
        writing_kind = str(context.get("writing_kind") or "")
        if writing_kind == "expand":
            target = int(context.get("target_chars", result.final_char_count))
            assessment = assess_length(result.final_char_count, target)
            if not assessment.is_qualified and not warning:
                warning = "重新补写后仍未进入理想范围，可继续补写或仍然采用。"
            expansion_result = ExpansionRunResult(
                raw_output=(
                    f"<NOVEL_TEXT>\n{result.text}\n</NOVEL_TEXT>\n"
                    "<NOVALIST_TASK_DONE>扩写任务已完成</NOVALIST_TASK_DONE>"
                ),
                first_raw_output=None,
                plain_text_fallback_count=0,
                target_chars=target,
                min_chars=assessment.preferred_min,
                max_chars=assessment.preferred_max,
                initial_char_count=int(
                    context.get("initial_char_count", result.initial_char_count)
                ),
                final_char_count=result.final_char_count,
                supplement_attempted=True,
                supplement_applied=result.applied,
                supplement_added_chars=total_added,
                supplement_warning=warning,
                review_min_chars=assessment.review_min,
                review_max_chars=assessment.review_max,
                length_status=assessment.status,
                supplement_attempt_count=attempts,
                length_retry_attempted=bool(
                    context.get("length_retry_attempted", False)
                ),
                length_retry_applied=bool(context.get("length_retry_applied", False)),
                original_draft_text=str(context.get("original_draft_text") or ""),
                original_draft_char_count=int(
                    context.get("original_draft_char_count", 0)
                ),
                correction_history=tuple(history),
            )
            self._on_expansion_done(token, expansion_result)
            return
        if writing_kind == "continuation":
            current_chars = int(context.get("current_chars", 0))
            requested_chars = int(
                context.get("requested_chars", result.final_char_count)
            )
            run_target = int(
                context.get("run_target_chars", current_chars + requested_chars)
            )
            projected = current_chars + result.final_char_count
            assessment = assess_length(projected, run_target)
            if not assessment.is_qualified and not warning:
                warning = "重新补写后仍未进入理想范围，可继续补写或仍然采用。"
            continuation_result = ContinuationRunResult(
                raw_output=(
                    f"<NOVEL_TEXT>\n{result.text}\n</NOVEL_TEXT>\n"
                    "<NOVALIST_TASK_DONE>续写任务已完成</NOVALIST_TASK_DONE>"
                ),
                first_raw_output=None,
                plain_text_fallback_count=0,
                current_chars=current_chars,
                requested_chars=requested_chars,
                target_chapter_chars=int(
                    context.get("target_chapter_chars", run_target)
                ),
                run_target_chars=run_target,
                min_chars=assessment.preferred_min,
                max_chars=assessment.preferred_max,
                review_min_chars=assessment.review_min,
                review_max_chars=assessment.review_max,
                initial_generated_chars=int(
                    context.get("initial_generated_chars", result.initial_char_count)
                ),
                final_generated_chars=result.final_char_count,
                projected_final_chars=projected,
                length_status=assessment.status,
                supplement_attempted=True,
                supplement_applied=result.applied,
                supplement_added_chars=total_added,
                supplement_warning=warning,
                supplement_attempt_count=attempts,
                length_retry_attempted=bool(
                    context.get("length_retry_attempted", False)
                ),
                length_retry_applied=bool(context.get("length_retry_applied", False)),
                original_draft_text=str(context.get("original_draft_text") or ""),
                original_draft_char_count=int(
                    context.get("original_draft_char_count", 0)
                ),
                correction_history=tuple(history),
            )
            self._on_continuation_done(token, continuation_result)
            return
        QMessageBox.warning(self.parent, "补写结果无效", "无法识别原始写作任务。")

    def _on_document_saved(self, saved_path: str) -> None:
        path_key = self._path_key(saved_path)
        pending = self._pending_foreshadowing_resolutions.get(path_key)
        if pending is None:
            return
        project = self.project_session.project
        if project is None or str(project.root.resolve()).casefold() != pending.project_root:
            return
        expected_path = project.chapters_dir / f"{pending.chapter_id}.md"
        if self._path_key(expected_path) != path_key:
            return
        try:
            updated = ProjectDataStore(project).resolve_foreshadowing(
                pending.note_ids,
                pending.chapter_id,
            )
        except OSError as exc:
            self._emit_output(f"伏笔状态暂未更新，将在下次保存时重试：{exc}")
            QMessageBox.warning(self.parent, "伏笔状态更新失败", str(exc))
            return
        except (KeyError, ValueError) as exc:
            self._pending_foreshadowing_resolutions.pop(path_key, None)
            self._emit_output(f"伏笔状态未更新：{exc}")
            QMessageBox.warning(self.parent, "伏笔状态未更新", str(exc))
            return

        self._pending_foreshadowing_resolutions.pop(path_key, None)
        if not updated:
            self._emit_output("伏笔状态无需更新，相关伏笔可能已由作者处理。")
            return
        foreshadowing_path = project.memory_dir / "foreshadowing.json"
        self.project_session.notify_data_changed([foreshadowing_path], kind="memory")
        titles = "、".join(str(note.get("title") or "未命名伏笔") for note in updated)
        self._emit_output(f"章节已保存；已将伏笔标记为已回收：{titles}")
        self._emit_status(f"章节已保存并更新 {len(updated)} 条伏笔状态")

    def _on_project_changed(self, _project) -> None:
        self._pending_foreshadowing_resolutions.clear()

    @staticmethod
    def _path_key(path: Path | str) -> str:
        return str(Path(path).resolve()).casefold()

    def _on_check_done(self, token, result: str | None = None) -> None:
        # Keep the old private-call shape usable for lightweight integrations;
        # normal signal delivery always supplies the task token.
        if result is None:
            result = str(token or "")
            token = None
        if token is not None and not self._task_context_matches(token):
            message = "一致性检查结果已过期：项目或当前章节在检查期间发生了变化，请重新运行检查。"
            self._emit_output(message)
            self._emit_status("一致性检查结果已过期")
            QMessageBox.warning(self.parent, "检查结果已过期", message)
            return
        try:
            report, rendered = self.ai_result_service.parse_consistency(
                result,
                expected_chapter_id=getattr(token, "chapter_id", None),
            )
        except ai_protocol.AIProtocolError as exc:
            self._emit_output(f"一致性检查结果无效\n{exc}\n原始返回：\n{result}")
            self._emit_status("一致性检查结果无效")
            QMessageBox.warning(self.parent, "检查结果无效", str(exc))
            return
        task_context_getter = getattr(self.ai_controller, "result_context", None)
        task_context = task_context_getter(token) if callable(task_context_getter) else None
        source_text = self.editor.text_edit.toPlainText()
        if isinstance(task_context, dict) and task_context.get("origin") == "reports":
            source_text = str(task_context.get("source_text") or "")
            report["chapter_title"] = str(task_context.get("chapter_title") or "")
        elif token is not None and self.project_session.project is not None:
            try:
                report["chapter_title"] = self.project_session.project.load_chapter(
                    token.chapter_id
                ).title
            except (OSError, UnicodeError, ValueError):
                pass
        report = enrich_report_anchors(report, source_text)
        rendered = ai_protocol.format_consistency_report(report)
        completion_message = str(report.get("completion_message") or "一致性检查任务已完成")
        self._notify_complete(completion_message)
        self._emit_output(f"一致性检查结果\n{rendered}")
        self.inspector.show_text("一致性检查", rendered)
        self.reports_page.show_result(
            report,
            rendered,
            project=self.project_session.project,
        )

    def _on_repair_done(self, token, result) -> None:
        task_context_getter = getattr(self.ai_controller, "result_context", None)
        task_context = task_context_getter(token) if callable(task_context_getter) else None
        issue = task_context.get("issue") if isinstance(task_context, dict) else None
        if not isinstance(issue, dict):
            self._emit_status("修复结果缺少问题上下文，未写入正文")
            return
        if not self._task_context_matches(token):
            message = "修复结果已过期：项目或当前章节在生成期间发生了变化，请重新生成。"
            self._emit_output(message)
            self._emit_status("修复结果已过期")
            QMessageBox.warning(self.parent, "修复结果已过期", message)
            return
        quote = str(issue.get("chapter_quote") or "")
        try:
            parsed = self.ai_result_service.parse_consistency_repair(
                result,
                expected_chapter_id=token.chapter_id,
                expected_issue_id=str(issue.get("issue_id") or ""),
                expected_original=quote,
            )
        except ai_protocol.AIProtocolError as exc:
            self._emit_output(f"AI 修复结果无效\n{exc}\n原始返回：\n{result}")
            self._emit_status("AI 修复结果无效，未修改正文")
            QMessageBox.warning(self.parent, "AI 修复结果无效", str(exc))
            return
        if parsed.status != "ready":
            labels = {
                "choice_required": "需要人工选择",
                "not_applicable": "无需修改正文",
                "insufficient_context": "证据不足",
            }
            message = f"AI 未生成可直接写回的方案：{labels.get(parsed.status, parsed.status)}。\n{parsed.explanation}"
            self._emit_output(message)
            self._emit_status("AI 修复需要人工判断，未修改正文")
            QMessageBox.information(self.parent, "AI 修复建议", message)
            return
        resolution = resolve_text_anchor(
            self.editor.text_edit.toPlainText(), issue.get("chapter_anchor") or quote
        )
        if resolution.start is None or resolution.end is None or resolution.status == "ambiguous":
            self._emit_status("正文原句已变化，修复未写入")
            QMessageBox.warning(self.parent, "无法安全修复", "正文原句不再唯一匹配，请重新运行检查。")
            return
        self._emit_output(f"✅ {parsed.completion_message}，等待预览确认")
        self._emit_status("AI 修复方案已生成，等待确认")
        outcome = self.ai_result_coordinator.confirm_repair(
            expected_original=parsed.expected_original,
            replacement=parsed.replacement,
            explanation=parsed.explanation,
            preserved_facts=parsed.preserved_facts,
            context_matches=lambda: self._task_context_matches(token),
            apply_replacement=lambda: self.editor.replace_range_if_matches(
                resolution.start,
                resolution.end,
                parsed.expected_original,
                parsed.replacement,
            ),
        )
        if outcome.status == "cancelled":
            self._emit_output("AI 修复方案已放弃，未修改正文。")
            self._emit_status("AI 修复已放弃")
            return
        if outcome.status == "stale":
            self._emit_output("AI 修复未写入：章节内容或当前章节已发生变化。")
            self._emit_status("章节已变化，修复结果未写入")
            return
        if outcome.status != "committed":
            self._emit_output(f"AI 修复未写入：{outcome.error or '替换失败'}")
            self._emit_status("AI 修复未写入")
            return
        self._emit_output("已应用一处最小正文修复，尚未自动保存。")
        self._emit_status("AI 修复已应用，请审阅后保存")

    def _on_memory_done(self, token, proposal: ChapterMemoryProposal) -> None:
        self._emit_output(
            f"✅ {proposal.completion_message}；"
            f"生成 {len(proposal.patches)} 条状态 Patch、"
            f"发现 {len(proposal.conflicts)} 条冲突或警告"
        )
        chapter_id = token.chapter_id
        project = self.project_session.project
        if not chapter_id or project is None:
            self._emit_output("记忆提案已取消，未修改项目数据。")
            return
        if proposal.has_blockers:
            details = proposal.preview_text()
            box = QMessageBox(self.parent)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("记忆提案存在阻断冲突")
            box.setText("为避免覆盖不一致状态，本次未写入。")
            box.setInformativeText(details)
            box.setDetailedText(proposal.conflict_evidence_text())
            retry_button = box.addButton(
                "重新生成提案",
                QMessageBox.ButtonRole.ActionRole,
            )
            box.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(retry_button)
            box.exec()
            self._emit_output("记忆提案存在阻断冲突，故事状态未写入。")
            self._emit_status("记忆更新被冲突检测阻止")
            if box.clickedButton() is retry_button:
                self._emit_output("正在跳过旧提案缓存并重新生成记忆提案。")
                QTimer.singleShot(
                    0,
                    lambda: self.update_memory(force_refresh=True),
                )
            return
        downstream = self.ai_result_service.downstream_memory_chapters(
            project,
            chapter_id,
        )
        preview_details = proposal.preview_text()
        if downstream:
            preview_details += (
                "\n\n采用影响：以下后续章节记忆及摘要将被标记失效并移除，"
                "需要按顺序重新更新：\n- "
                + "\n- ".join(downstream)
            )
        outcome = self.ai_result_coordinator.confirm_memory(
            summary=proposal.summary,
            details=preview_details,
            patch_count=len(proposal.patches),
            conflict_count=len(proposal.conflicts),
            context_matches=lambda: self._task_context_matches(token),
            commit=lambda: self.ai_result_service.commit_memory_proposal(
                project,
                chapter_id,
                proposal,
            ),
        )
        if outcome.status == "cancelled":
            self._emit_output("记忆更新已取消，未修改项目数据。")
            return
        if outcome.status == "stale":
            self._emit_output("记忆提案已丢弃：项目或章节已发生变化。")
            return
        if outcome.status == "failed":
            self._emit_output(f"记忆提案写入失败：{outcome.error or '未知错误'}")
            self._emit_status("长期记忆更新失败")
            QMessageBox.critical(
                self.parent,
                "更新失败",
                outcome.error or "长期记忆写入失败。",
            )
            return
        commit_result = outcome.value
        if commit_result is None:
            return
        self._emit_output(f"长期记忆已通过事实 Patch 更新\n{proposal.summary}")
        if commit_result.invalidated_chapters:
            self._emit_output(
                "以下后续章节记忆已失效，请按顺序重新运行“更新记忆”："
                + "、".join(commit_result.invalidated_chapters)
            )
        self.project_session.notify_data_changed(
            [
                project.memory_dir / "story_state.json",
                project.memory_dir / "chapter_summaries.json",
            ],
            kind="memory",
        )
        self._offer_missing_character_cards(project, commit_result.merged_state)
        self._emit_status("长期记忆已更新")

    def _offer_missing_character_cards(self, project, story_state: dict) -> None:
        candidates = missing_character_cards(project, story_state)
        if not candidates:
            return
        names = "、".join(candidate.name for candidate in candidates)
        answer = QMessageBox.question(
            self.parent,
            "发现未建卡角色",
            f"故事记忆中发现 {len(candidates)} 位尚未建立角色卡的追踪角色：\n{names}\n\n"
            "是否创建草稿角色卡？创建后仍可在左侧角色栏中继续完善。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._emit_output(f"已保留未建卡追踪角色：{names}")
            return
        try:
            created = create_character_cards(project, candidates)
        except (OSError, ValueError) as exc:
            self._emit_output(f"角色卡草稿创建失败：{exc}")
            QMessageBox.warning(self.parent, "创建角色卡失败", str(exc))
            return
        if not created:
            self._emit_output("未创建新的角色卡，已有文件未被覆盖。")
            return
        self.project_session.notify_data_changed(created, kind="canon")
        self._emit_output(
            f"已创建 {len(created)} 张角色卡草稿："
            + "、".join(path.stem for path in created)
        )

    def _task_context_matches(self, token) -> bool:
        task_context_getter = getattr(self.ai_controller, "result_context", None)
        task_context = task_context_getter(token) if callable(task_context_getter) else None
        if isinstance(task_context, dict) and task_context.get("origin") == "reports":
            # The report-page workflow is intentionally independent of the
            # document visible in the writing editor.  Its source snapshot is
            # the selected chapter loaded from disk; a new unsaved edit is
            # conservatively treated as stale.
            if self.editor.is_dirty():
                return False
            source_text = str(task_context.get("source_text") or "")
            return self.ai_controller.context_matches(
                self.project_session.project,
                token.chapter_id,
                source_text,
                token,
            )
        return self.ai_controller.context_matches(
            self.project_session.project,
            self.editor.current_chapter_id() or "",
            self.editor.text_edit.toPlainText(),
            token,
        )

    def _notify_complete(self, message: str) -> None:
        message = str(message or "AI 任务已完成").strip()
        self._emit_output(f"✅ {message}")
        self._emit_status(message)
        QMessageBox.information(self.parent, "AI 任务完成", message)

    def _record_plain_text_fallbacks(self, count: int) -> None:
        count = max(0, int(count))
        if not count:
            return
        self._plain_text_fallback_count += count
        self._emit_output(
            "协议诊断：本次扩写触发纯正文兼容回退 "
            f"{count} 次；本会话累计 {self._plain_text_fallback_count} 次。"
        )

    def _emit_output(self, message: str) -> None:
        self.output_requested.emit(message)

    def _emit_status(self, message: str) -> None:
        self.status_requested.emit(message)
