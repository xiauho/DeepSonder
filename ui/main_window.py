from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QDialog,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import ai_protocol, consistency, memory, prompt_builder
from core.config import load_config, save_config
from core.dsh_client import DSHClient
from core.project import NovelProject
from ui.editor import Editor
from ui.icons import IconTextButton, refresh_button_icons, set_button_icon
from ui.inspector import Inspector
from ui.left_panel import LeftPanel
from ui.memory_page import StoryMemoryPage
from ui.navigation import PrimaryNavigation
from ui.pages import DashboardPage, ExportPage, ReportsPage, SettingsPage
from ui.theme import DARK_COLORS, LIGHT_COLORS, apply_theme, colors_for


class DSHTask(QThread):
    """Run blocking Harness work away from the writing interface."""

    success = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 - surface an actionable UI error
            self.failed.emit(str(exc))
        else:
            self.success.emit(result)


class ContinuationPreviewDialog(QDialog):
    """Preview a validated continuation before it changes the chapter."""

    def __init__(self, text: str, char_count: int, length_ok: bool, parent=None):
        super().__init__(parent)
        self.mode: str | None = None
        self.setWindowTitle("续写结果预览")
        self.resize(760, 560)

        layout = QVBoxLayout(self)
        info = QLabel(
            f"已生成约 {char_count} 字。"
            + ("长度在目标范围内。" if length_ok else "长度超出目标范围，请审阅后决定。")
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        layout.addWidget(editor, 1)

        buttons = QHBoxLayout()
        insert = QPushButton("插入光标处")
        append = QPushButton("追加到正文末尾")
        copy = QPushButton("复制")
        cancel = QPushButton("放弃")
        insert.clicked.connect(lambda: self._finish("insert"))
        append.clicked.connect(lambda: self._finish("append"))
        copy.clicked.connect(lambda: self._copy(text))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(insert)
        buttons.addWidget(append)
        buttons.addWidget(copy)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)

    def _finish(self, mode: str) -> None:
        self.mode = mode
        self.accept()


class ExpansionPreviewDialog(QDialog):
    """Preview a complete chapter draft before replacing the body."""

    def __init__(self, text: str, char_count: int, length_ok: bool, has_existing_content: bool, parent=None):
        super().__init__(parent)
        self.mode: str | None = None
        self.setWindowTitle("扩写结果预览")
        self.resize(760, 560)

        layout = QVBoxLayout(self)
        notice = "将替换当前章节正文。" if has_existing_content else "当前章节正文为空，将写入生成结果。"
        info = QLabel(
            f"已生成约 {char_count} 字。"
            + ("长度在目标范围内。" if length_ok else "长度超出目标范围，请审阅后决定。")
            + f"\n{notice}"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        layout.addWidget(editor, 1)

        buttons = QHBoxLayout()
        replace = QPushButton("替换当前正文")
        copy = QPushButton("复制")
        cancel = QPushButton("放弃")
        replace.clicked.connect(lambda: self._finish("replace"))
        copy.clicked.connect(lambda: self._copy(text))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(replace)
        buttons.addWidget(copy)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)

    def _finish(self, mode: str) -> None:
        self.mode = mode
        self.accept()


class MainWindow(QMainWindow):
    ROUTES = ("dashboard", "writing", "canon", "memory", "reports", "export", "settings")
    ACTION_ICONS = {
        "new_chapter": "add",
        "open_project": "folder_open",
        "save": "save",
        "export": "ios_share",
        "focus": "center_focus_strong",
        "check": "fact_check",
        "memory": "psychology",
        "continue": "auto_awesome",
    }

    def __init__(self, parent=None, config: dict | None = None):
        super().__init__(parent)
        self.project: NovelProject | None = None
        self._task: DSHTask | None = None
        self._connection_task: DSHTask | None = None
        self._task_chapter_id: str | None = None
        self._task_project_root: Path | None = None
        self._task_source_hash: str | None = None
        self._task_cursor_position: int | None = None
        self._task_cursor_anchor: int | None = None
        self._focus_mode = False
        self._current_route = "dashboard"
        self._left_panel_width = 270
        self._inspector_width = 330
        self.config = dict(config) if config is not None else load_config()
        self._action_icon_buttons: dict[str, IconTextButton] = {}
        self._configure_dsh()

        self.setWindowTitle("Novalist")
        self.setMinimumSize(1100, 720)
        self._build_actions()
        self._build_ui()
        self.inspector.set_theme(self.config)
        self.left_panel.set_theme(self.config)
        self._refresh_icons()
        self._build_menus()
        self._build_statusbar()
        self._connect_signals()
        self._show_route("dashboard")

        self.exit_focus_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.exit_focus_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.exit_focus_shortcut.activated.connect(self._exit_focus_mode)
        self.f11_focus_shortcut = QShortcut(QKeySequence("F11"), self)
        self.f11_focus_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.f11_focus_shortcut.activated.connect(self.toggle_focus_mode)

        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.timeout.connect(self._auto_save)
        self._refresh_auto_save_timer()
        QTimer.singleShot(80, self._restore_last_project)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _configure_dsh(self) -> None:
        self.dsh = DSHClient(
            dsh_command=self.config.get("dsh_command", "dsh"),
            launcher_args=self.config.get("dsh_launcher_args") or [],
            profile="headless",
            timeout=self.config.get("dsh_timeout", 600),
            extra_args=self.config.get("dsh_extra_args") or [],
        )
        if self.project is not None:
            self.dsh.set_working_directory(self.project.root)

    def _build_actions(self) -> None:
        self.actions: dict[str, QAction] = {}

        def action(key: str, text: str, slot, shortcut: str | None = None) -> QAction:
            item = QAction(text, self)
            item.triggered.connect(slot)
            if shortcut:
                item.setShortcut(QKeySequence(shortcut))
            self.actions[key] = item
            return item

        action("new_project", "新建项目…", self.new_project, "Ctrl+Shift+N")
        action("open_project", "打开项目…", self.open_project, "Ctrl+Shift+O")
        action("save", "保存", self.save_current_file, "Ctrl+S")
        action("export", "导出作品", lambda: self._show_route("export"), "Ctrl+Shift+E")
        action("settings", "设置", lambda: self._show_route("settings"), "Ctrl+,")
        action("quit", "退出", self.close, "Ctrl+Q")
        action("new_chapter", "新建章节", self.new_chapter, "Ctrl+N")
        action("new_character", "新建角色", self.new_character, "Ctrl+Alt+C")
        action("new_world", "新建世界观条目", self.new_world_entry)
        action("undo", "撤销", lambda: self.editor.text_edit.undo(), "Ctrl+Z")
        action("redo", "重做", lambda: self.editor.text_edit.redo(), "Ctrl+Y")
        action("find", "查找与替换", lambda: self.editor.show_find(), "Ctrl+F")
        action("focus", "专注模式", self.toggle_focus_mode, "Ctrl+K")
        action("navigation", "显示/隐藏资料面板", self.toggle_navigation_panel, "Ctrl+Shift+L")
        action("inspector", "显示/隐藏故事雷达", self.toggle_inspector, "Ctrl+Shift+I")
        action("output", "显示/隐藏 AI 记录", self.toggle_output, "Ctrl+J")
        action("continue", "AI 扩写", self.expand_chapter, "Ctrl+Enter")
        action("check", "一致性检查", self.check_consistency, "Ctrl+Shift+C")
        action("memory", "更新故事记忆", self.update_memory, "Ctrl+Shift+M")

    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("appRoot")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.primary_nav = PrimaryNavigation()
        root.addWidget(self.primary_nav)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        root.addWidget(content, 1)

        self.app_header = QFrame()
        self.app_header.setObjectName("appHeader")
        header_layout = QHBoxLayout(self.app_header)
        header_layout.setContentsMargins(20, 13, 20, 13)
        header_layout.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        self.page_title = QLabel("项目")
        self.page_title.setObjectName("pageTitle")
        self.page_subtitle = QLabel("项目概览与最近创作")
        self.page_subtitle.setObjectName("mutedLabel")
        title_box.addWidget(self.page_title)
        title_box.addWidget(self.page_subtitle)
        header_layout.addLayout(title_box, 1)
        self.ai_indicator = QLabel("●  DSH 空闲中")
        self.ai_indicator.setObjectName("aiStatus")
        header_layout.addWidget(self.ai_indicator)
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("ghostButton")
        self.theme_button.setProperty("material_icon", "dark_mode")
        self.theme_button.setProperty("material_icon_size", 17)
        self.theme_button.clicked.connect(self.toggle_theme)
        header_layout.addWidget(self.theme_button)
        settings_button = IconTextButton("settings", "设置", centered=True)
        settings_button.setObjectName("ghostButton")
        settings_button.setToolTip("打开设置")
        settings_button.clicked.connect(lambda: self._show_route("settings"))
        header_layout.addWidget(settings_button)
        content_layout.addWidget(self.app_header)

        self.action_bar = QFrame()
        self.action_bar.setObjectName("actionBar")
        action_layout = QHBoxLayout(self.action_bar)
        action_layout.setContentsMargins(14, 8, 14, 8)
        action_layout.setSpacing(7)
        action_layout.addWidget(self._action_button("new_chapter", "新章节"))
        action_layout.addWidget(self._action_button("open_project", "打开项目"))
        action_layout.addWidget(self._action_button("save", "保存"))
        action_layout.addWidget(self._action_button("export", "导出"))
        action_layout.addStretch(1)
        focus_button = self._action_button("focus", "专注模式")
        focus_button.setObjectName("ghostButton")
        focus_button.style().unpolish(focus_button)
        focus_button.style().polish(focus_button)
        action_layout.addWidget(focus_button)
        check_button = self._action_button("check", "检查设定")
        check_button.setObjectName("secondaryButton")
        action_layout.addWidget(check_button)
        memory_button = self._action_button("memory", "更新记忆")
        memory_button.setObjectName("secondaryButton")
        action_layout.addWidget(memory_button)
        continue_button = self._action_button("continue", "AI 扩写")
        continue_button.setObjectName("accentButton")
        action_layout.addWidget(continue_button)
        content_layout.addWidget(self.action_bar)

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("pageStack")
        self.dashboard_page = DashboardPage()
        self.writing_page = self._build_writing_page()
        self.memory_page = StoryMemoryPage()
        self.reports_page = ReportsPage()
        self.export_page = ExportPage()
        self.settings_page = SettingsPage(self.config)
        for page in (
            self.dashboard_page,
            self.writing_page,
            self.memory_page,
            self.reports_page,
            self.export_page,
            self.settings_page,
        ):
            self.page_stack.addWidget(page)
        content_layout.addWidget(self.page_stack, 1)

    def _build_writing_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("pageSurface")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        self.outer_splitter = QSplitter(Qt.Orientation.Vertical)
        self.outer_splitter.setObjectName("outerSplitter")
        self.outer_splitter.setHandleWidth(5)
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setObjectName("mainSplitter")
        self.main_splitter.setHandleWidth(5)
        self.main_splitter.setChildrenCollapsible(False)
        self.left_panel = LeftPanel()
        self.editor = Editor()
        self.inspector = Inspector()
        self.main_splitter.addWidget(self.left_panel)
        self.main_splitter.addWidget(self.editor)
        self.main_splitter.addWidget(self.inspector)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes([270, 820, 330])
        for index in range(3):
            self.main_splitter.setCollapsible(index, False)
        self.main_splitter.splitterMoved.connect(self._remember_panel_sizes)

        self.output_container = QFrame()
        self.output_container.setObjectName("outputContainer")
        output_layout = QVBoxLayout(self.output_container)
        output_layout.setContentsMargins(14, 8, 14, 10)
        output_layout.setSpacing(6)
        output_header = QHBoxLayout()
        output_title = QLabel("AI 工作记录")
        output_title.setObjectName("panelTitle")
        clear_button = QPushButton("清空")
        clear_button.setObjectName("ghostButton")
        clear_button.clicked.connect(lambda: self.output_panel.clear())
        close_button = QPushButton("收起")
        close_button.setObjectName("ghostButton")
        close_button.clicked.connect(self.toggle_output)
        output_header.addWidget(output_title)
        output_header.addStretch(1)
        output_header.addWidget(clear_button)
        output_header.addWidget(close_button)
        output_layout.addLayout(output_header)
        self.output_panel = QPlainTextEdit()
        self.output_panel.setObjectName("outputPanel")
        self.output_panel.setReadOnly(True)
        self.output_panel.setPlaceholderText("AI 扩写、设定检查和记忆更新的过程会记录在这里。")
        output_layout.addWidget(self.output_panel, 1)
        self.output_container.hide()

        self.outer_splitter.addWidget(self.main_splitter)
        self.outer_splitter.addWidget(self.output_container)
        self.outer_splitter.setStretchFactor(0, 1)
        self.outer_splitter.setStretchFactor(1, 0)
        self.outer_splitter.setSizes([720, 170])
        page_layout.addWidget(self.outer_splitter, 1)
        return page

    def _action_button(self, key: str, text: str) -> IconTextButton:
        icon_name = self.ACTION_ICONS.get(key)
        button = IconTextButton(icon_name, text, centered=True) if icon_name else IconTextButton("circle", text, centered=True)
        if not icon_name:
            button.findChild(QLabel, "buttonIcon").hide()
        action = self.actions[key]
        button.setToolTip(action.text())
        button.setEnabled(action.isEnabled())
        button.clicked.connect(action.trigger)
        action.changed.connect(lambda item=action, target=button: self._sync_action_button(item, target))
        self._action_icon_buttons[key] = button
        return button

    @staticmethod
    def _sync_action_button(action: QAction, button: IconTextButton) -> None:
        button.setEnabled(action.isEnabled())
        button.setToolTip(action.text())
        button.set_label(action.text())

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction(self.actions["new_project"])
        file_menu.addAction(self.actions["open_project"])
        self.recent_menu = QMenu("最近项目", self)
        file_menu.addMenu(self.recent_menu)
        self._refresh_recent_menu()
        file_menu.addSeparator()
        file_menu.addAction(self.actions["save"])
        file_menu.addAction(self.actions["export"])
        file_menu.addSeparator()
        file_menu.addAction(self.actions["settings"])
        file_menu.addAction(self.actions["quit"])

        edit_menu = self.menuBar().addMenu("编辑")
        edit_menu.addAction(self.actions["undo"])
        edit_menu.addAction(self.actions["redo"])
        edit_menu.addSeparator()
        edit_menu.addAction(self.actions["find"])

        create_menu = self.menuBar().addMenu("创作")
        create_menu.addAction(self.actions["new_chapter"])
        create_menu.addAction(self.actions["new_character"])
        create_menu.addAction(self.actions["new_world"])
        create_menu.addSeparator()
        create_menu.addAction(self.actions["continue"])
        create_menu.addAction(self.actions["check"])
        create_menu.addAction(self.actions["memory"])

        view_menu = self.menuBar().addMenu("视图")
        view_menu.addAction(self.actions["focus"])
        view_menu.addAction(self.actions["navigation"])
        view_menu.addAction(self.actions["inspector"])
        view_menu.addAction(self.actions["output"])

    def _build_statusbar(self) -> None:
        self.status_message = QLabel("打开或创建一个项目，开始今天的写作")
        self.status_message.setObjectName("mutedLabel")
        self.task_progress = QProgressBar()
        self.task_progress.setRange(0, 0)
        self.task_progress.setFixedSize(110, 8)
        self.task_progress.hide()
        self.auto_save_status = QLabel("自动保存已开启")
        self.auto_save_status.setObjectName("mutedLabel")
        self.statusBar().addWidget(self.status_message, 1)
        self.statusBar().addPermanentWidget(self.task_progress)
        self.statusBar().addPermanentWidget(self.auto_save_status)

    def _connect_signals(self) -> None:
        self.primary_nav.route_requested.connect(self._show_route)
        self.primary_nav.new_project_requested.connect(self.new_project)
        self.dashboard_page.new_project_requested.connect(self.new_project)
        self.dashboard_page.open_project_requested.connect(self.open_project)
        self.dashboard_page.import_requested.connect(self._import_markdown_chapters)
        self.dashboard_page.continue_requested.connect(lambda: self._show_route("writing"))
        self.left_panel.file_selected.connect(self._on_file_selected)
        self.left_panel.new_chapter_requested.connect(self.new_chapter)
        self.left_panel.toggle_requested.connect(self.toggle_navigation_panel)
        self.memory_page.sync_requested.connect(self.update_memory)
        self.memory_page.chapter_requested.connect(self._open_memory_chapter)
        self.reports_page.run_requested.connect(self.check_consistency)
        self.export_page.export_requested.connect(self.export_manuscript)
        self.settings_page.save_requested.connect(self._apply_settings)
        self.settings_page.test_requested.connect(self._test_dsh)
        self.editor.dirty_changed.connect(self._on_dirty_changed)
        self.editor.file_saved.connect(lambda _path: self._refresh_current_context())
        self.editor.exit_focus_button.clicked.connect(self._exit_focus_mode)

    # ------------------------------------------------------------------
    # Routing and shared shell
    # ------------------------------------------------------------------
    def _show_route(self, route: str) -> None:
        if route not in self.ROUTES:
            route = "dashboard"
        if route not in {"dashboard", "settings"} and not self._require_project():
            self.primary_nav.set_active(self._current_route)
            return
        if route != self._current_route and self.editor.is_dirty() and not self._save_if_dirty():
            self.primary_nav.set_active(self._current_route)
            return

        if route in {"writing", "canon"}:
            self._focus_mode = False
            self._set_focus_chrome(True)
            self.left_panel.set_scope("canon" if route == "canon" else "all")
            self.page_stack.setCurrentWidget(self.writing_page)
            if route == "canon" and self.project and self.editor.current_category() == "章节":
                canon_paths = [
                    self.project.outline_dir / "main_arc.md",
                    *self.project.list_characters(),
                    *self.project.list_world(),
                    *self.project.list_power(),
                    self.project.canon_dir / "timeline.md",
                ]
                canon_paths = [path for path in canon_paths if path.exists()]
                if canon_paths:
                    self.left_panel.select_path(canon_paths[0])
        elif route == "memory":
            self.memory_page.show_project(self.project)
            self.page_stack.setCurrentWidget(self.memory_page)
            self.action_bar.hide()
        elif route == "reports":
            self.reports_page.show_project(self.project)
            self.page_stack.setCurrentWidget(self.reports_page)
            self.action_bar.hide()
        elif route == "export":
            self.export_page.show_project(self.project)
            self.page_stack.setCurrentWidget(self.export_page)
            self.action_bar.hide()
        elif route == "settings":
            self.settings_page.set_config(self.config)
            self.page_stack.setCurrentWidget(self.settings_page)
            self.action_bar.hide()
        else:
            self.dashboard_page.refresh(self.project)
            self.page_stack.setCurrentWidget(self.dashboard_page)
            self.action_bar.hide()

        self._current_route = route
        self.primary_nav.set_active(route)
        self._update_page_header(route)
        self._update_window_title()

    def _update_page_header(self, route: str) -> None:
        labels = {
            "dashboard": ("项目", "项目概览与最近创作"),
            "writing": ("写作台", "章节正文、故事雷达与 AI 辅助"),
            "canon": ("故事资料", "集中维护会被正文和 AI 引用的故事事实"),
            "memory": ("故事记忆", "章节摘要、人物状态与长期线索"),
            "reports": ("检查报告", "集中查看设定一致性风险"),
            "export": ("导出", "确认章节范围并生成本地文件"),
            "settings": ("设置", "写作体验、DeepSeek Harness 与外观"),
        }
        title, subtitle = labels[route]
        self.page_title.setText(title)
        self.page_subtitle.setText(subtitle if not self.project else f"{self.project.name} · {subtitle}")
        self.theme_button.setText("深色" if self.config.get("theme") == "light" else "浅色")
        self.action_bar.setVisible(route in {"writing", "canon"} and not self._focus_mode)

    def _set_focus_chrome(self, visible: bool) -> None:
        if self._focus_mode:
            visible = False
        self.primary_nav.setVisible(visible)
        self.app_header.setVisible(visible)
        self.menuBar().setVisible(visible)
        self.statusBar().setVisible(visible)
        self.action_bar.setVisible(visible and self._current_route in {"writing", "canon"})
        self.left_panel.setVisible(visible)
        self.inspector.setVisible(visible)
        self.editor.exit_focus_button.setVisible(not visible)
        if not visible:
            self.output_container.hide()

    # ------------------------------------------------------------------
    # Projects and documents
    # ------------------------------------------------------------------
    def open_project(self) -> None:
        start = self.project.root if self.project else Path.cwd() / "projects"
        path = QFileDialog.getExistingDirectory(self, "选择小说项目目录", str(start))
        if path:
            self._load_project(Path(path))

    def _load_project(self, path: Path, quiet: bool = False) -> bool:
        if self._task is not None and self._task.isRunning():
            if not quiet:
                QMessageBox.information(self, "AI 任务仍在进行", "请等待当前 AI 任务完成后再切换项目。")
            return False
        if not NovelProject.is_project(path):
            if not quiet:
                QMessageBox.warning(self, "无法打开", f"该目录不是有效的 Novalist 创作项目：\n{path}")
            return False
        if not self._save_if_dirty():
            return False
        try:
            project = NovelProject(path)
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                QMessageBox.critical(self, "加载失败", str(exc))
            return False

        self.project = project
        self.dsh.set_working_directory(project.root)
        self.editor.clear_document("选择一份故事资料")
        self.left_panel.set_project(project)
        self.memory_page.show_project(project)
        self.reports_page.show_project(project)
        self.export_page.show_project(project)
        self.dashboard_page.refresh(project)
        self.primary_nav.set_project(project.name)
        self.output_panel.clear()
        self.status_message.setText(f"已打开 · {project.name}")
        self._remember_project(project.root)
        self._show_route("dashboard")
        chapters = project.list_chapters()
        if chapters:
            self.left_panel.select_path(chapters[0])
            self._show_route("dashboard")
        return True

    def new_project(self) -> None:
        parent_dir = QFileDialog.getExistingDirectory(self, "选择新项目存放目录")
        if not parent_dir:
            return
        name, ok = QInputDialog.getText(self, "创建新的故事", "作品名称：", text="我的小说")
        if not ok or not name.strip():
            return
        root = Path(parent_dir) / self._safe_name(name.strip())
        if root.exists():
            QMessageBox.warning(self, "项目已存在", f"目标目录已经存在，请换一个作品名称：\n{root}")
            return
        try:
            project = NovelProject.create(root, name=name.strip())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "创建失败", str(exc))
            return
        if self._load_project(project.root):
            self._show_route("writing")

    def save_current_file(self, notify: bool = True) -> bool:
        if not self.editor.current_path():
            if notify:
                QMessageBox.information(self, "保存", "请先打开一份可编辑的故事资料。")
            return False
        if not self.editor.save():
            if notify:
                QMessageBox.warning(self, "保存失败", "文件未能保存，请检查写入权限。")
            return False
        if notify:
            self.status_message.setText("已保存")
        self._update_window_title()
        return True

    def new_chapter(self) -> None:
        if not self._require_project():
            return
        assert self.project is not None
        default_id = f"chapter_{len(self.project.list_chapters()) + 1:02d}"
        title, ok = QInputDialog.getText(self, "新建章节", "章节标题：", text="新章节")
        if not ok or not title.strip():
            return
        chapter_id, ok = QInputDialog.getText(self, "新建章节", "文件标识（建议保留默认值）：", text=default_id)
        if not ok:
            return
        chapter_id = self._safe_name(chapter_id.strip())
        if not chapter_id:
            return
        path = self.project.chapters_dir / f"{chapter_id}.md"
        if path.exists():
            QMessageBox.warning(self, "章节已存在", f"请换一个文件标识：{chapter_id}")
            return
        path.write_text(
            f"# {title.strip()}\n\n## 大纲\n- 本章目标：\n- 核心冲突：\n- 章节钩子：\n\n## 剧情简写\n\n\n## 正文\n\n",
            encoding="utf-8",
        )
        self.left_panel.set_project(self.project)
        self._show_route("writing")
        self.left_panel.select_path(path)
        self.status_message.setText(f"已创建 · {title.strip()}")

    def new_character(self) -> None:
        if not self._require_project():
            return
        assert self.project is not None
        name, ok = QInputDialog.getText(self, "新建角色", "角色姓名：", text="新角色")
        if not ok or not name.strip():
            return
        path = self.project.canon_dir / "characters" / f"{self._safe_name(name.strip())}.md"
        if path.exists():
            QMessageBox.warning(self, "角色已存在", "同名角色卡已经存在。")
            return
        path.write_text(
            f"# {name.strip()}\n\n- 身份：\n- 外貌特征：\n- 性格：\n- 核心欲望：\n- 当前目标：\n- 战力/能力：\n- 关键关系：\n- 秘密：\n",
            encoding="utf-8",
        )
        self.left_panel.set_project(self.project)
        self._show_route("canon")
        self.left_panel.select_path(path)

    def new_world_entry(self) -> None:
        if not self._require_project():
            return
        assert self.project is not None
        title, ok = QInputDialog.getText(self, "新建世界观条目", "条目名称：", text="新设定")
        if not ok or not title.strip():
            return
        path = self.project.canon_dir / "world" / f"{self._safe_name(title.strip())}.md"
        if path.exists():
            QMessageBox.warning(self, "条目已存在", "同名世界观条目已经存在。")
            return
        path.write_text(f"# {title.strip()}\n\n## 核心规则\n\n## 历史与现状\n\n## 对剧情的约束\n", encoding="utf-8")
        self.left_panel.set_project(self.project)
        self._show_route("canon")
        self.left_panel.select_path(path)

    def _on_file_selected(self, category: str, path_str: str) -> None:
        current = self.editor.current_path()
        if current and Path(current) == Path(path_str):
            return
        if not self._save_if_dirty():
            return
        if self.editor.open_file(category, path_str) and self.project:
            if category == "章节":
                self.inspector.show_project(self.project, Path(path_str).stem)
            else:
                self.inspector.show_project(self.project)
            self._show_route("writing" if category == "章节" else "canon")
        self._update_window_title()

    def _open_memory_chapter(self, chapter_id: str) -> None:
        if self.project is None:
            return
        path = self.project.chapters_dir / f"{chapter_id}.md"
        if path.exists():
            self._show_route("writing")
            self.left_panel.select_path(path)

    def _import_markdown_chapters(self) -> None:
        if not self._require_project():
            return
        assert self.project is not None
        files, _ = QFileDialog.getOpenFileNames(
            self, "导入 Markdown 章节", str(Path.cwd()), "Markdown 文档 (*.md *.markdown);;文本文件 (*.txt)"
        )
        if not files:
            return
        imported: list[Path] = []
        for source_name in files:
            source = Path(source_name)
            stem = self._safe_name(source.stem) or "imported_chapter"
            destination = self.project.chapters_dir / f"{stem}.md"
            suffix = 2
            while destination.exists():
                destination = self.project.chapters_dir / f"{stem}_{suffix}.md"
                suffix += 1
            try:
                text = source.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                text = source.read_text(encoding="gb18030")
            if not text.lstrip().startswith("# "):
                text = f"# {source.stem}\n\n## 正文\n\n{text.strip()}\n"
            destination.write_text(text, encoding="utf-8")
            imported.append(destination)
        self.left_panel.set_project(self.project)
        self.dashboard_page.refresh(self.project)
        if imported:
            self._show_route("writing")
            self.left_panel.select_path(imported[0])
            self.status_message.setText(f"已导入 {len(imported)} 个章节")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_manuscript(self, options: object = None) -> None:
        if not self._require_project():
            return
        if not isinstance(options, dict):
            self._show_route("export")
            return
        assert self.project is not None
        if not self._save_if_dirty():
            return
        selected_ids = {str(item) for item in options.get("chapter_ids", [])}
        chapters = [path for path in self.project.list_chapters() if path.stem in selected_ids]
        if not chapters:
            QMessageBox.information(self, "无法导出", "请至少选择一个章节。")
            return
        export_format = "txt" if options.get("format") == "txt" else "md"
        suggested = self.project.root / f"{self._safe_name(self.project.name)}-全书.{export_format}"
        filters = "纯文本 (*.txt);;Markdown 文档 (*.md)" if export_format == "txt" else "Markdown 文档 (*.md);;纯文本 (*.txt)"
        output, selected_filter = QFileDialog.getSaveFileName(self, "导出全书", str(suggested), filters)
        if not output:
            return
        plain_text = output.lower().endswith(".txt") or "纯文本" in selected_filter
        blocks: list[str] = []
        if options.get("include_title"):
            blocks.append(self.project.name if plain_text else f"# {self.project.name}")
        if options.get("include_toc"):
            toc_title = "目录" if plain_text else "## 目录"
            toc_lines = [
                f"{index}. {self.project.load_chapter(path.stem).title}"
                for index, path in enumerate(chapters, 1)
            ]
            blocks.append(toc_title + "\n\n" + "\n".join(toc_lines))
        for path in chapters:
            text = path.read_text(encoding="utf-8")
            if plain_text or options.get("strip"):
                text = re_strip_markdown(text)
            blocks.append(text.strip())
        separator = "\n\n***\n\n" if options.get("separators") else "\n\n\n"
        try:
            Path(output).write_text(separator.join(blocks) + "\n", encoding="utf-8-sig")
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.status_message.setText(f"已导出 {len(chapters)} 章 · {Path(output).name}")
        QMessageBox.information(self, "导出完成", f"全书已导出到：\n{output}")

    # ------------------------------------------------------------------
    # Focus, panels and preferences
    # ------------------------------------------------------------------
    def toggle_focus_mode(self) -> None:
        if self._current_route not in {"writing", "canon"}:
            self._show_route("writing")
        self._focus_mode = not self._focus_mode
        self._set_focus_chrome(not self._focus_mode)
        margins = 80 if self._focus_mode else 18
        self.editor.layout().setContentsMargins(margins, 30 if self._focus_mode else 14, margins, 20 if self._focus_mode else 12)
        self.actions["focus"].setText("退出专注模式" if self._focus_mode else "专注模式")
        if self._focus_mode:
            self.editor.text_edit.setFocus()

    def _exit_focus_mode(self) -> None:
        if self._focus_mode:
            self.toggle_focus_mode()

    def toggle_navigation_panel(self) -> None:
        self._toggle_side_panel(self.left_panel, 0, "_left_panel_width")

    def toggle_inspector(self) -> None:
        self._toggle_side_panel(self.inspector, 2, "_inspector_width")

    def _toggle_side_panel(self, panel: QWidget, index: int, width_attribute: str) -> None:
        if panel.isVisible():
            sizes = self.main_splitter.sizes()
            if len(sizes) > index and sizes[index] > 0:
                setattr(self, width_attribute, sizes[index])
            panel.hide()
            return
        panel.show()
        QTimer.singleShot(0, self._restore_side_panel_sizes)

    def _restore_side_panel_sizes(self) -> None:
        if self.main_splitter.width() <= 0:
            return
        left = self._left_panel_width if self.left_panel.isVisible() else 0
        right = self._inspector_width if self.inspector.isVisible() else 0
        center = max(1, self.main_splitter.width() - left - right)
        self.main_splitter.setSizes([left, center, right])

    def _remember_panel_sizes(self, _position: int, _index: int) -> None:
        sizes = self.main_splitter.sizes()
        if len(sizes) < 3:
            return
        if self.left_panel.isVisible() and sizes[0] >= self.left_panel.minimumWidth():
            self._left_panel_width = sizes[0]
        if self.inspector.isVisible() and sizes[2] >= self.inspector.minimumWidth():
            self._inspector_width = sizes[2]

    def toggle_output(self) -> None:
        self.output_container.setVisible(not self.output_container.isVisible())
        if self.output_container.isVisible():
            self.outer_splitter.setSizes([700, 180])

    def toggle_theme(self) -> None:
        next_theme = "dark" if self.config.get("theme", "light") == "light" else "light"
        self.config["theme"] = next_theme
        self.config.update(LIGHT_COLORS if next_theme == "light" else DARK_COLORS)
        self._apply_settings(self.config, message="主题已切换")

    def _apply_settings(self, config: object, message: str = "设置已保存") -> None:
        if not isinstance(config, dict):
            return
        self.config = dict(config)
        save_config(self.config)
        self._configure_dsh()
        self._refresh_auto_save_timer()
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self.config)
        self.inspector.set_theme(self.config)
        self.left_panel.set_theme(self.config)
        self._refresh_icons()
        self.settings_page.set_config(self.config)
        self._update_page_header(self._current_route)
        self.status_message.setText(message)

    def _test_dsh(self, config: object) -> None:
        if not isinstance(config, dict):
            return
        client = DSHClient(
            dsh_command=str(config.get("dsh_command", "dsh")),
            launcher_args=config.get("dsh_launcher_args") or [],
            profile="headless",
            timeout=min(15, int(config.get("dsh_timeout", 600))),
            extra_args=config.get("dsh_extra_args") or [],
        )
        self.settings_page.set_test_status("正在检查 dsh…")
        self.settings_page.test_button.setEnabled(False)
        self._connection_task = DSHTask(client.check_connection, self)
        self._connection_task.success.connect(lambda result: self.settings_page.set_test_status(str(result)))
        self._connection_task.failed.connect(lambda error: self.settings_page.set_test_status(f"检查失败：{error}"))
        self._connection_task.finished.connect(lambda: self.settings_page.test_button.setEnabled(True))
        self._connection_task.start()

    def _refresh_icons(self) -> None:
        palette = colors_for(self.config)
        muted = palette["muted_text_color"]
        refresh_button_icons(self, muted)
        for _key, button in self._action_icon_buttons.items():
            color = "#FFFFFF" if button.objectName() == "accentButton" else muted
            button.set_icon_color(color)
        if hasattr(self, "theme_button"):
            theme_icon = "dark_mode" if self.config.get("theme") == "light" else "light_mode"
            set_button_icon(self.theme_button, theme_icon, muted, 17)

    # ------------------------------------------------------------------
    # Auto-save and AI tasks
    # ------------------------------------------------------------------
    def _auto_save(self) -> None:
        if self.editor.is_dirty() and self.editor.current_path() and self.save_current_file(notify=False):
            self.auto_save_status.setText("刚刚自动保存")

    def _refresh_auto_save_timer(self) -> None:
        enabled = bool(self.config.get("auto_save", True))
        seconds = max(5, int(self.config.get("auto_save_interval", 30) or 30))
        if enabled:
            self._auto_save_timer.start(seconds * 1000)
        else:
            self._auto_save_timer.stop()
        self.auto_save_status.setText("自动保存已开启" if enabled else "自动保存已关闭")

    def _on_dirty_changed(self, _dirty: bool) -> None:
        self._update_window_title()

    def _save_if_dirty(self) -> bool:
        return not self.editor.is_dirty() or self.save_current_file(notify=False)

    def _require_project_and_chapter(self) -> str | None:
        if not self._require_project():
            return None
        chapter_id = self.editor.current_chapter_id()
        if chapter_id is None:
            QMessageBox.information(self, "需要章节", "请先从资料树打开一个章节。")
            self._show_route("writing")
            return None
        return chapter_id

    def _ensure_ai_notice(self) -> bool:
        if self.config.get("ai_notice_acknowledged", False):
            return True
        notice = QMessageBox(self)
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

    def expand_chapter(self) -> None:
        self._show_route("writing")
        chapter_id = self._require_project_and_chapter()
        if chapter_id is None or not self._ensure_ai_notice() or not self.save_current_file(notify=False):
            return
        assert self.project is not None
        system_prompt, user_prompt = prompt_builder.build_expansion_prompt(
            self.project,
            chapter_id,
            target_chars=int(self.config.get("expand_target_chars", 2000)),
        )
        project, dsh = self.project, self.dsh
        target_chars = int(self.config.get("expand_target_chars", 2000))

        def task():
            raw = dsh.generate(system_prompt, user_prompt)
            try:
                ai_protocol.parse_expansion(
                    raw,
                    min_chars=round(target_chars * 0.85),
                    max_chars=round(target_chars * 1.15),
                )
            except ai_protocol.AIProtocolError:
                retry_system, retry_user = prompt_builder.build_expansion_retry_prompt(
                    project,
                    chapter_id,
                    target_chars=target_chars,
                )
                return dsh.generate(
                    retry_system,
                    retry_user,
                    timeout_override=min(int(dsh.timeout), 45),
                )
            return raw

        if not self._prepare_task(chapter_id, f"正在扩写 · {chapter_id}"):
            return
        self._start_task(task, self._on_expansion_done)

    def continue_writing(self) -> None:
        """Backward-compatible entry point for older shortcuts or callers."""
        self.expand_chapter()

    def check_consistency(self) -> None:
        self._show_route("writing")
        chapter_id = self._require_project_and_chapter()
        if chapter_id is None or not self._ensure_ai_notice() or not self.save_current_file(notify=False):
            return
        assert self.project is not None
        project = self.project
        if not self._prepare_task(chapter_id, f"正在检查设定 · {chapter_id}"):
            return
        self._start_task(lambda: consistency.run_consistency_check(project, chapter_id, self.dsh), self._on_check_done)

    def update_memory(self) -> None:
        self._show_route("writing")
        chapter_id = self._require_project_and_chapter()
        if chapter_id is None or not self._ensure_ai_notice() or not self.save_current_file(notify=False):
            return
        assert self.project is not None
        project, dsh = self.project, self.dsh

        def task():
            summary_system, summary_user = prompt_builder.build_summary_prompt(project, chapter_id)
            summary_raw = dsh.generate(summary_system, summary_user)
            summary_result = ai_protocol.parse_summary_result(summary_raw)
            state_system, state_user = prompt_builder.build_state_update_prompt(project, chapter_id)
            state_raw = dsh.generate_json(state_system, state_user)
            state_result = ai_protocol.parse_story_state_result(state_raw)
            completion = f"{summary_result.completion_message}；{state_result.completion_message}"
            return summary_result.text, state_result.state, completion

        if not self._prepare_task(chapter_id, f"正在提炼章节摘要与故事状态 · {chapter_id}"):
            return
        self._start_task(task, self._on_memory_done)

    def _prepare_task(self, chapter_id: str, message: str) -> bool:
        if self._task is not None and self._task.isRunning():
            QMessageBox.information(self, "AI 正在工作", "当前任务完成后再试一次。")
            return False
        self._task_chapter_id = chapter_id
        self._task_project_root = self.project.root if self.project else None
        self._task_source_hash = self._editor_source_hash()
        self._task_cursor_position, self._task_cursor_anchor = self.editor.cursor_snapshot()
        self._append_output(message)
        return True

    def _start_task(self, fn, on_success) -> None:
        if self._task is not None and self._task.isRunning():
            QMessageBox.information(self, "AI 正在工作", "当前任务完成后再试一次。")
            return
        for key in ("continue", "check", "memory"):
            self.actions[key].setEnabled(False)
        self.task_progress.show()
        self.ai_indicator.setText("●  DSH 处理中")
        self.ai_indicator.setObjectName("aiStatusBusy")
        self.ai_indicator.style().unpolish(self.ai_indicator)
        self.ai_indicator.style().polish(self.ai_indicator)
        self.status_message.setText("DeepSeek Harness 正在整理故事上下文…")
        self.memory_page.set_syncing(True)
        self.output_container.show()
        self.outer_splitter.setSizes([690, 190])
        self._task = DSHTask(fn, self)
        self._task.success.connect(on_success)
        self._task.failed.connect(self._on_task_failed)
        self._task.finished.connect(self._on_task_finished)
        self._task.start()

    def _on_task_finished(self) -> None:
        for key in ("continue", "check", "memory"):
            self.actions[key].setEnabled(True)
        self.task_progress.hide()
        self.ai_indicator.setText("●  DSH 空闲中")
        self.ai_indicator.setObjectName("aiStatus")
        self.ai_indicator.style().unpolish(self.ai_indicator)
        self.ai_indicator.style().polish(self.ai_indicator)
        self.memory_page.set_syncing(False)

    def _on_task_failed(self, message: str) -> None:
        self._append_output(f"任务失败\n{message}")
        self.status_message.setText("AI 任务失败")
        QMessageBox.critical(self, "DeepSeek Harness 调用失败", message)

    def _task_context_matches(self) -> bool:
        return bool(
            self.project
            and self._task_project_root
            and self.project.root.resolve() == self._task_project_root.resolve()
            and self.editor.current_chapter_id() == self._task_chapter_id
            and self._task_source_hash == self._editor_source_hash()
        )

    def _on_expansion_done(self, result: str) -> None:
        target = int(self.config.get("expand_target_chars", 2000))
        try:
            parsed = ai_protocol.parse_expansion(
                result,
                min_chars=round(target * 0.85),
                max_chars=round(target * 1.15),
            )
        except ai_protocol.AIProtocolError as exc:
            self._append_output(f"扩写结果无效\n{exc}\n原始返回：\n{result}")
            self.status_message.setText("扩写结果无效，未写入正文")
            QMessageBox.warning(self, "扩写结果无效", str(exc))
            return

        self._notify_ai_complete(f"{parsed.completion_message}，等待你确认写入。")
        self._append_output(f"扩写结果已通过格式校验（约 {parsed.char_count} 字），等待确认")
        chapter = self.project.load_chapter(self._task_chapter_id) if self.project else None
        dialog = ExpansionPreviewDialog(
            parsed.text,
            parsed.char_count,
            parsed.length_ok,
            bool(chapter and chapter.content.strip()),
            self,
        )
        dialog.exec()
        if dialog.mode is None:
            self._append_output("扩写结果已放弃，未修改正文。")
            self.status_message.setText("扩写结果已放弃")
            return
        if not self._task_context_matches():
            self._append_output("扩写结果未写入：章节内容或当前章节已发生变化。")
            self.status_message.setText("章节已变化，扩写结果仅保留在 AI 记录中")
            QMessageBox.warning(
                self,
                "章节已发生变化",
                "生成期间当前章节内容发生了变化，结果未自动写入。你可以使用预览中的复制按钮手动采用。",
            )
            return
        if chapter and chapter.content.strip():
            answer = QMessageBox.question(
                self,
                "确认替换正文",
                "当前章节已有正文，替换操作会覆盖现有正文。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._append_output("扩写结果未替换正文。")
                self.status_message.setText("已取消替换，扩写结果仅保留在 AI 记录中")
                return
        self.editor.replace_chapter_body(parsed.text)
        self.status_message.setText("扩写已替换当前正文，请审阅后保存")
        self._append_output("扩写结果已替换当前章节正文，尚未自动保存。")

    def _on_check_done(self, result: str) -> None:
        try:
            report = ai_protocol.parse_consistency_report(result)
            rendered = ai_protocol.format_consistency_report(report)
        except ai_protocol.AIProtocolError as exc:
            self._append_output(f"一致性检查结果无效\n{exc}\n原始返回：\n{result}")
            self.status_message.setText("一致性检查结果无效")
            QMessageBox.warning(self, "检查结果无效", str(exc))
            return
        completion_message = str(report.get("completion_message") or "一致性检查任务已完成")
        self._notify_ai_complete(completion_message)
        self._append_output(f"一致性检查完成\n{rendered}")
        self.inspector.show_text("一致性检查", rendered)
        self.reports_page.show_result(rendered)
        self.status_message.setText("一致性检查完成")

    def _on_memory_done(self, result: tuple[str, dict, str]) -> None:
        summary, new_state, completion_message = result
        if not isinstance(new_state, dict):
            QMessageBox.warning(self, "更新失败", "AI 返回的故事状态不是有效对象。")
            return
        self._notify_ai_complete(f"{completion_message}，等待确认写入。")
        answer = QMessageBox.question(
            self,
            "确认更新长期记忆",
            f"章节摘要：\n{summary}\n\n确认写入章节摘要和故事状态吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._append_output("记忆更新已取消，未修改项目数据。")
            return
        if not self._task_context_matches() or self.project is None:
            self._append_output("记忆更新结果已丢弃：项目或章节已切换。")
            return
        chapter_id = self._task_chapter_id
        if not chapter_id:
            return
        memory.save_chapter_summary(self.project, chapter_id, summary)
        memory.apply_state_update(self.project, new_state)
        self._append_output(f"长期记忆已更新\n{summary}")
        self.inspector.show_project(self.project, chapter_id)
        self.memory_page.show_project(self.project)
        self.dashboard_page.refresh(self.project)
        self.status_message.setText("长期记忆已更新")

    def _notify_ai_complete(self, message: str) -> None:
        message = str(message or "AI 任务已完成").strip()
        self._append_output(f"✅ {message}")
        self.status_message.setText(message)
        QMessageBox.information(self, "AI 任务完成", message)

    def _editor_source_hash(self) -> str | None:
        if not self.editor.current_path():
            return None
        content = self.editor.text_edit.toPlainText().encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    # ------------------------------------------------------------------
    # Helpers and lifecycle
    # ------------------------------------------------------------------
    def _append_output(self, text: str) -> None:
        if self.output_panel.toPlainText():
            self.output_panel.appendPlainText("\n" + "—" * 28)
        self.output_panel.appendPlainText(text)

    def _require_project(self) -> bool:
        if self.project is None:
            QMessageBox.information(self, "尚未打开项目", "请先打开或新建一个小说项目。")
            return False
        return True

    def _refresh_current_context(self) -> None:
        if self.project and self.editor.current_chapter_id():
            self.inspector.show_project(self.project, self.editor.current_chapter_id())
        if self.project:
            self.dashboard_page.refresh(self.project)
            self.export_page.render_preview()

    def _remember_project(self, path: Path) -> None:
        resolved = str(path.resolve())
        recent = [p for p in self.config.get("recent_projects", []) if p != resolved]
        self.config["recent_projects"] = [resolved] + recent[:7]
        self.config["last_project"] = resolved
        save_config(self.config)
        self._refresh_recent_menu()

    def _refresh_recent_menu(self) -> None:
        if not hasattr(self, "recent_menu"):
            return
        self.recent_menu.clear()
        paths = [Path(p) for p in self.config.get("recent_projects", []) if Path(p).exists()]
        if not paths:
            empty = self.recent_menu.addAction("暂无最近项目")
            empty.setEnabled(False)
            return
        for path in paths:
            item = self.recent_menu.addAction(path.name)
            item.setToolTip(str(path))
            item.triggered.connect(lambda _checked=False, p=path: self._load_project(p))

    def _restore_last_project(self) -> None:
        last = self.config.get("last_project")
        if last and Path(last).exists():
            self._load_project(Path(last), quiet=True)

    def _update_window_title(self) -> None:
        self.setWindowTitle("Novalist")

    @staticmethod
    def _safe_name(value: str) -> str:
        forbidden = '<>:"/\\|?*'
        cleaned = "".join("_" if char in forbidden else char for char in value).strip(" .")
        return cleaned or "untitled"

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.editor.is_dirty() and not self.save_current_file(notify=False):
            answer = QMessageBox.question(
                self,
                "文件尚未保存",
                "当前内容保存失败，仍要退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        if self._task is not None and self._task.isRunning():
            QMessageBox.information(self, "AI 任务仍在进行", "请等待当前 AI 任务完成后再退出，以免丢失生成结果。")
            event.ignore()
            return
        event.accept()


def re_strip_markdown(text: str) -> str:
    """Produce a clean reading copy while preserving paragraph structure."""
    lines = []
    in_outline = False
    for line in text.splitlines():
        if line.strip() == "## 大纲":
            in_outline = True
            continue
        if line.strip() == "## 正文":
            in_outline = False
            continue
        if in_outline:
            continue
        if line.startswith("# "):
            line = line[2:].strip()
        lines.append(line)
    return "\n".join(lines)
