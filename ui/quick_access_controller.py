"""Reuse existing document/action routes without bypassing their safety checks."""
from pathlib import Path

from PySide6.QtWidgets import QDialog

from ui.quick_access import QuickAccessDialog, QuickEntry


class QuickAccessController:
    PROJECT_COMMANDS = frozenset(("export", "trash", "new_chapter", "delete_chapter",
        "new_character", "character_sync", "new_world", "new_power", "new_timeline", "manage_timeline", "focus_directory"))
    DOCUMENT_COMMANDS = frozenset(("save", "undo", "redo", "find", "focus_editor", "locate_document"))

    def __init__(self, window):
        self.window = window

    def entries(self):
        window = self.window
        window.workspace_state.capture()
        entries = []
        project = window.project
        if project is not None:
            root = project.root.resolve()
            for child in window.left_panel.document_items():
                path = Path(child.data(0, window.left_panel.PATH_ROLE))
                try:
                    relative = path.resolve().relative_to(root).as_posix()
                except (ValueError, OSError):
                    continue
                category = child.data(0, window.left_panel.CATEGORY_ROLE)
                title = child.data(0, window.left_panel.TITLE_ROLE) or child.text(0)
                entries.append(QuickEntry(str(path), title, f"{category} · {relative}",
                    category=category, project_root=str(root)))
            entries.append(QuickEntry("timeline", "时间线", "查看全部故事事件与作者编排的时间轴",
                category="时间线", project_root=str(root), feature="timeline"))
            record = window.workspace_state.data["projects"].get(str(root), {})
            documents = record.get("documents", {}) if isinstance(record, dict) else {}
            recent = list(reversed(documents)) if isinstance(documents, dict) else []
            rank = {str(root / relative): index for index, relative in enumerate(recent)}
            entries.sort(key=lambda entry: rank.get(entry.key, len(rank)))
        for key, action in window.actions.items():
            if key in {"quick_open", "commands"}:
                continue
            shortcut = action.shortcut().toString()
            if key in {"expand", "continuation", "check", "memory", "character_sync", "selection_expand", "style_polish", "style_review", "style_exceptions", "style_library"}:
                group = "AI 创作"
            elif key in {"save", "undo", "redo", "find"}:
                group = "文档编辑"
            elif key in {"focus", "navigation", "inspector", "output", "focus_editor", "focus_directory", "locate_document"}:
                group = "视图与导航"
            else:
                group = "项目与应用"
            tip = action.toolTip()
            detail = group + (" · " + tip if tip and tip != action.text() else "")
            if key == "expand":
                detail += " · 原 AI 扩写"
            entries.append(QuickEntry(key, action.text().replace("&", ""), detail,
                                      mode="commands", shortcut=shortcut))
        return entries

    def unavailable(self, entry):
        window = self.window
        if entry.mode == "documents":
            if window.project is None or str(window.project.root.resolve()) != entry.project_root:
                return "项目已切换，请重新搜索当前项目。"
            if entry.feature == "timeline":
                return ""
            try:
                path = Path(entry.key).resolve()
                path.relative_to(window.project.root.resolve())
                if not path.is_file():
                    return "文件已不存在，请重新搜索或在目录中选择其他文档。"
            except (OSError, ValueError):
                return "文档路径不在当前项目内或无法读取。"
            return ""
        action = window.actions.get(entry.key)
        if action is None:
            return "此命令已不可用。"
        if entry.key in self.PROJECT_COMMANDS and window.project is None:
            return "请先打开或新建项目。"
        if entry.key in self.DOCUMENT_COMMANDS and not window.editor.current_path():
            return "请先打开一个章节或资料文档。"
        if not action.isEnabled():
            hint = action.statusTip() or action.toolTip()
            if hint and hint != action.text():
                return hint
            return "当前状态下不可用，请先完成进行中的任务或选择适用文档。"
        return ""

    def activate(self, entry):
        window = self.window
        reason = self.unavailable(entry)
        if reason:
            window.status_message.setText(reason)
            return False
        if entry.feature == "timeline":
            return window.manage_timeline()
        if entry.mode == "commands":
            window.actions[entry.key].trigger()
            return True
        if window.project and Path(entry.key).resolve() == window.project.style_guide_path.resolve():
            return window.ai_workflow_controller.manage_style_library()
        focused = window.window_state_controller.focus_mode
        window._on_file_selected(entry.category, entry.key)
        if window.editor.current_path() != entry.key:
            window.status_message.setText("未切换文档，请先处理当前文档的保存问题。")
            return False
        route = window.story_navigation_controller.route_for_category(entry.category)
        if window.window_state_controller.current_route != route and not window._show_route(route):
            return False
        window.left_panel.reveal_path(entry.key)
        if focused and not window.window_state_controller.focus_mode:
            window.window_state_controller.toggle_focus_mode()
        if window.editor.is_story_plan():
            window.editor.story_plan_form.focus_field()
        elif window.editor.view_mode() == "preview":
            window.editor.preview_browser.setFocus()
        else:
            window.editor.text_edit.setFocus()
        return True

    def open(self, mode):
        window = self.window
        window._refresh_ai_actions()
        dialog = QuickAccessDialog(self.entries(), self.unavailable, mode=mode, parent=window)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_entry is not None:
                self.activate(dialog.selected_entry)
        finally:
            dialog.deleteLater()
