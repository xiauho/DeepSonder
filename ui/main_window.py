from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
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
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import load_config
from core.project import NovelProject
from ui.ai_controller import AIController
from ui.ai_engine_controller import AIEngineController
from ui.ai_task_view_controller import AITaskViewController
from ui.ai_workflow_controller import AIWorkflowController
from ui.appearance_controller import AppearanceController
from ui.document_controller import DocumentController
from ui.export_controller import ExportController
from ui.editor import Editor
from ui.icons import IconTextButton
from ui.inspector import Inspector
from ui.left_panel import LeftPanel
from ui.memory_page import StoryMemoryPage
from ui.navigation import PrimaryNavigation
from ui.pages import DashboardPage, ExportPage, ReportsPage, SettingsPage
from ui.project_session import ProjectSession
from ui.project_lifecycle_controller import ProjectLifecycleController, ProjectSwitchCancelled
from ui.settings_controller import SettingsController
from ui.story_navigation_controller import StoryNavigationController
from ui.view_refresh_controller import ViewRefreshController
from ui.window_state_controller import WindowStateController


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
        self.project_session = ProjectSession(self)
        self.ai_controller = AIController(self)
        self.config = dict(config) if config is not None else load_config()
        self._action_icon_buttons: dict[str, IconTextButton] = {}
        self.ai_engine_controller = AIEngineController(
            self.config,
            self.ai_controller.is_running,
            self,
        )

        self.setWindowTitle("Novalist")
        self.setMinimumSize(1100, 720)
        self._build_actions()
        self._build_ui()
        self.document_controller = DocumentController(
            self.editor,
            self.project_session,
            self,
        )
        self.project_lifecycle_controller = ProjectLifecycleController(
            project_session=self.project_session,
            config=self.config,
            is_task_running=self.ai_controller.is_running,
            save_if_dirty=self._save_if_dirty,
            parent=self,
        )
        self.settings_controller = SettingsController(
            self.config,
            self.document_controller,
            self.ai_engine_controller,
            self,
        )
        self.export_controller = ExportController(self.project_session, self)
        self.export_page.set_export_controller(self.export_controller)
        self.view_refresh_controller = ViewRefreshController(
            self.project_session,
            self.editor,
            self.left_panel,
            self.inspector,
            self.dashboard_page,
            self.memory_page,
            self.reports_page,
            self.export_page,
            self,
        )
        self.window_state_controller = WindowStateController(
            primary_nav=self.primary_nav,
            page_stack=self.page_stack,
            pages={
                "dashboard": self.dashboard_page,
                "memory": self.memory_page,
                "reports": self.reports_page,
                "export": self.export_page,
                "settings": self.settings_page,
            },
            writing_page=self.writing_page,
            action_bar=self.action_bar,
            app_header=self.app_header,
            menu_bar=self.menuBar(),
            status_bar=self.statusBar(),
            left_panel=self.left_panel,
            inspector=self.inspector,
            editor=self.editor,
            output_container=self.output_container,
            main_splitter=self.main_splitter,
            outer_splitter=self.outer_splitter,
            focus_action=self.actions["focus"],
            exit_focus_button=self.editor.exit_focus_button,
            parent=self,
        )
        self.story_navigation_controller = StoryNavigationController(
            project_session=self.project_session,
            editor=self.editor,
            left_panel=self.left_panel,
            show_route=lambda route: self._show_route(route),
            parent=self,
        )
        self.appearance_controller = AppearanceController(
            root=self,
            inspector=self.inspector,
            left_panel=self.left_panel,
            settings_page=self.settings_page,
            theme_button=self.theme_button,
            action_icon_buttons=self._action_icon_buttons,
            parent=self,
        )
        self.main_splitter.splitterMoved.connect(
            self.window_state_controller.remember_panel_sizes
        )
        self.appearance_controller.apply(self.config)
        self._build_menus()
        self._build_statusbar()
        self.ai_task_view_controller = AITaskViewController(
            ai_controller=self.ai_controller,
            ai_engine_controller=self.ai_engine_controller,
            actions=self.actions,
            task_progress=self.task_progress,
            cancel_button=self.cancel_task_button,
            ai_indicator=self.ai_indicator,
            status_message=self.status_message,
            memory_page=self.memory_page,
            output_panel=self.output_panel,
            window_state_controller=self.window_state_controller,
            parent=self,
        )
        self.ai_workflow_controller = AIWorkflowController(
            config=self.config,
            project_session=self.project_session,
            editor=self.editor,
            document_controller=self.document_controller,
            ai_controller=self.ai_controller,
            ai_engine_controller=self.ai_engine_controller,
            inspector=self.inspector,
            reports_page=self.reports_page,
            go_to_writing=lambda: self._show_route("writing"),
            parent=self,
        )
        self.ai_workflow_controller.output_requested.connect(
            self.ai_task_view_controller.append_output
        )
        self.ai_workflow_controller.status_requested.connect(
            self.ai_task_view_controller.set_status
        )
        self._connect_signals()
        self.project_session.project_changed.connect(self._on_project_changed)
        self._show_route("dashboard")

        self.exit_focus_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.exit_focus_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.exit_focus_shortcut.activated.connect(self._exit_focus_mode)
        self.f11_focus_shortcut = QShortcut(QKeySequence("F11"), self)
        self.f11_focus_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.f11_focus_shortcut.activated.connect(self.toggle_focus_mode)

        self._refresh_auto_save_timer()
        QTimer.singleShot(80, self._restore_last_project)

    @property
    def project(self) -> NovelProject | None:
        return self.project_session.project

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
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
        self.cancel_task_button = QPushButton("取消 AI")
        self.cancel_task_button.setObjectName("ghostButton")
        self.cancel_task_button.setAutoDefault(False)
        self.cancel_task_button.clicked.connect(self.cancel_ai_task)
        self.cancel_task_button.hide()
        self.auto_save_status = QLabel("自动保存已开启")
        self.auto_save_status.setObjectName("mutedLabel")
        self.statusBar().addWidget(self.status_message, 1)
        self.statusBar().addPermanentWidget(self.task_progress)
        self.statusBar().addPermanentWidget(self.cancel_task_button)
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
        self.settings_controller.config_changed.connect(self._on_settings_changed)
        self.settings_controller.connection_started.connect(self._on_dsh_test_started)
        self.settings_controller.connection_succeeded.connect(self._on_dsh_test_succeeded)
        self.settings_controller.connection_failed.connect(self._on_dsh_test_failed)
        self.settings_controller.connection_finished.connect(self._on_dsh_test_finished)
        self.editor.dirty_changed.connect(self._on_dirty_changed)
        self.document_controller.document_saved.connect(
            lambda _path: self.view_refresh_controller.refresh_current_context()
        )
        self.document_controller.auto_saved.connect(self._on_auto_saved)
        self.editor.exit_focus_button.clicked.connect(self._exit_focus_mode)

    # ------------------------------------------------------------------
    # Routing and shared shell
    # ------------------------------------------------------------------
    def _show_route(self, route: str) -> None:
        if route not in self.ROUTES:
            route = "dashboard"
        if route not in {"dashboard", "settings"} and not self._require_project():
            self.primary_nav.set_active(self.window_state_controller.current_route)
            return
        if (
            route != self.window_state_controller.current_route
            and self.editor.is_dirty()
            and not self._save_if_dirty()
        ):
            self.primary_nav.set_active(self.window_state_controller.current_route)
            return

        if route != "settings":
            self.view_refresh_controller.refresh_route(route)
        else:
            self.settings_page.set_config(self.config)
        self.window_state_controller.activate_route(route)
        if route == "canon":
            self.story_navigation_controller.select_default_canon()

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

    # ------------------------------------------------------------------
    # Projects and documents
    # ------------------------------------------------------------------
    def open_project(self) -> None:
        start = self.project.root if self.project else Path.cwd() / "projects"
        path = QFileDialog.getExistingDirectory(self, "选择小说项目目录", str(start))
        if path:
            self._load_project(Path(path))

    def _load_project(self, path: Path, quiet: bool = False) -> bool:
        try:
            project = self.project_lifecycle_controller.load(path)
        except ProjectSwitchCancelled:
            return False
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                QMessageBox.critical(self, "加载失败", str(exc))
            return False

        self._show_route("dashboard")
        chapters = project.list_chapters()
        if chapters:
            self.left_panel.select_path(chapters[0])
            self._show_route("dashboard")
        return True

    def _on_project_changed(self, project: NovelProject | None) -> None:
        """Update shell state after the view refresh controller switches projects."""
        self.primary_nav.set_project(project.name if project else None)
        self.output_panel.clear()
        self.status_message.setText(
            f"已打开 · {project.name}" if project else "尚未打开项目"
        )
        self._update_page_header(self.window_state_controller.current_route)

    def new_project(self) -> None:
        parent_dir = QFileDialog.getExistingDirectory(self, "选择新项目存放目录")
        if not parent_dir:
            return
        name, ok = QInputDialog.getText(self, "创建新的故事", "作品名称：", text="我的小说")
        if not ok or not name.strip():
            return
        try:
            project = self.project_lifecycle_controller.create_and_load(
                Path(parent_dir), name.strip()
            )
        except FileExistsError as exc:
            QMessageBox.warning(self, "项目已存在", f"目标目录已经存在，请换一个作品名称：\n{exc}")
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "创建失败", str(exc))
            return
        if project is not None:
            self._show_route("writing")

    def save_current_file(self, notify: bool = True) -> bool:
        if not self.editor.current_path():
            if notify:
                QMessageBox.information(self, "保存", "请先打开一份可编辑的故事资料。")
            return False
        if not self.document_controller.save():
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
        default_id = self.document_controller.next_chapter_id()
        title, ok = QInputDialog.getText(self, "新建章节", "章节标题：", text="新章节")
        if not ok or not title.strip():
            return
        chapter_id, ok = QInputDialog.getText(self, "新建章节", "文件标识（建议保留默认值）：", text=default_id)
        if not ok:
            return
        chapter_id = chapter_id.strip()
        if not chapter_id:
            return
        try:
            path = self.document_controller.create_chapter(title, chapter_id)
        except FileExistsError:
            QMessageBox.warning(self, "章节已存在", f"请换一个文件标识：{chapter_id}")
            return
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "创建失败", str(exc))
            return
        self._show_route("writing")
        self.left_panel.select_path(path)
        self.status_message.setText(f"已创建 · {title.strip()}")

    def new_character(self) -> None:
        if not self._require_project():
            return
        name, ok = QInputDialog.getText(self, "新建角色", "角色姓名：", text="新角色")
        if not ok or not name.strip():
            return
        try:
            path = self.document_controller.create_character(name)
        except FileExistsError:
            QMessageBox.warning(self, "角色已存在", "同名角色卡已经存在。")
            return
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "创建失败", str(exc))
            return
        self._show_route("canon")
        self.left_panel.select_path(path)

    def new_world_entry(self) -> None:
        if not self._require_project():
            return
        title, ok = QInputDialog.getText(self, "新建世界观条目", "条目名称：", text="新设定")
        if not ok or not title.strip():
            return
        try:
            path = self.document_controller.create_world_entry(title)
        except FileExistsError:
            QMessageBox.warning(self, "条目已存在", "同名世界观条目已经存在。")
            return
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "创建失败", str(exc))
            return
        self._show_route("canon")
        self.left_panel.select_path(path)

    def _on_file_selected(self, category: str, path_str: str) -> None:
        current = self.editor.current_path()
        if current and Path(current) == Path(path_str):
            return
        if self.document_controller.open_file(category, path_str) and self.project:
            self.view_refresh_controller.refresh_inspector(self.project)
            self._show_route(
                self.story_navigation_controller.route_for_category(category)
            )
        self._update_window_title()

    def _open_memory_chapter(self, chapter_id: str) -> None:
        self.story_navigation_controller.open_memory_chapter(chapter_id)

    def _import_markdown_chapters(self) -> None:
        if not self._require_project():
            return
        assert self.project is not None
        files, _ = QFileDialog.getOpenFileNames(
            self, "导入 Markdown 章节", str(Path.cwd()), "Markdown 文档 (*.md *.markdown);;文本文件 (*.txt)"
        )
        if not files:
            return
        try:
            imported = self.document_controller.import_markdown(files)
        except (OSError, UnicodeError, RuntimeError) as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
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
        try:
            chapters = self.export_controller.selected_chapters(options)
        except (RuntimeError, ValueError) as exc:
            QMessageBox.information(self, "无法导出", str(exc))
            return
        if not chapters:
            QMessageBox.information(self, "无法导出", "请至少选择一个章节。")
            return
        export_format = "txt" if options.get("format") == "txt" else "md"
        suggested = self.export_controller.suggested_path(options)
        filters = "纯文本 (*.txt);;Markdown 文档 (*.md)" if export_format == "txt" else "Markdown 文档 (*.md);;纯文本 (*.txt)"
        output, selected_filter = QFileDialog.getSaveFileName(self, "导出全书", str(suggested), filters)
        if not output:
            return
        plain_text = output.lower().endswith(".txt") or "纯文本" in selected_filter
        try:
            result = self.export_controller.export(
                options,
                Path(output),
                format_name="txt" if plain_text else "md",
            )
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.status_message.setText(f"已导出 {result.chapter_count} 章 · {result.path.name}")
        QMessageBox.information(self, "导出完成", f"全书已导出到：\n{output}")

    # ------------------------------------------------------------------
    # Focus, panels and preferences
    # ------------------------------------------------------------------
    def toggle_focus_mode(self) -> None:
        if self.window_state_controller.current_route not in {"writing", "canon"}:
            self._show_route("writing")
        self.window_state_controller.toggle_focus_mode()

    def _exit_focus_mode(self) -> None:
        self.window_state_controller.exit_focus_mode()

    def toggle_navigation_panel(self) -> None:
        self.window_state_controller.toggle_navigation_panel()

    def toggle_inspector(self) -> None:
        self.window_state_controller.toggle_inspector()

    def toggle_output(self) -> None:
        self.window_state_controller.toggle_output()

    def toggle_theme(self) -> None:
        self.settings_controller.toggle_theme()

    def _apply_settings(self, config: object, message: str = "设置已保存") -> None:
        if not isinstance(config, dict):
            return
        self.settings_controller.apply(config, message=message)

    def _on_settings_changed(self, config: dict, message: str) -> None:
        self.config = dict(config)
        self.project_lifecycle_controller.set_config(self.config)
        self.ai_workflow_controller.set_config(self.config)
        self.appearance_controller.apply(self.config)
        self._update_page_header(self.window_state_controller.current_route)
        self.status_message.setText(message)

    def _test_dsh(self, config: object) -> None:
        if not isinstance(config, dict):
            return
        self.settings_controller.test_dsh(config)

    def _on_dsh_test_started(self) -> None:
        self.settings_page.set_test_status("正在检查 dsh…")
        self.settings_page.test_button.setEnabled(False)

    def _on_dsh_test_succeeded(self, result: str) -> None:
        self.settings_page.set_test_status(str(result))

    def _on_dsh_test_failed(self, error: str) -> None:
        self.settings_page.set_test_status(f"检查失败：{error}")

    def _on_dsh_test_finished(self) -> None:
        self.settings_page.test_button.setEnabled(True)

    # ------------------------------------------------------------------
    # Auto-save and AI tasks
    # ------------------------------------------------------------------
    def _refresh_auto_save_timer(self) -> None:
        enabled = bool(self.config.get("auto_save", True))
        seconds = max(5, int(self.config.get("auto_save_interval", 30) or 30))
        self.document_controller.configure_auto_save(enabled, seconds)
        self.auto_save_status.setText("自动保存已开启" if enabled else "自动保存已关闭")

    def _on_auto_saved(self, _path: str) -> None:
        self.auto_save_status.setText("刚刚自动保存")

    def _on_dirty_changed(self, _dirty: bool) -> None:
        self._update_window_title()

    def _save_if_dirty(self) -> bool:
        return self.document_controller.save_if_dirty()

    def expand_chapter(self) -> None:
        self.ai_workflow_controller.expand()

    def continue_writing(self) -> None:
        """Backward-compatible entry point for older shortcuts or callers."""
        self.expand_chapter()

    def check_consistency(self) -> None:
        self.ai_workflow_controller.check()

    def update_memory(self) -> None:
        self.ai_workflow_controller.update_memory()

    def cancel_ai_task(self) -> None:
        self.ai_task_view_controller.cancel()

    # ------------------------------------------------------------------
    # Helpers and lifecycle
    # ------------------------------------------------------------------
    def _require_project(self) -> bool:
        if self.project is None:
            QMessageBox.information(self, "尚未打开项目", "请先打开或新建一个小说项目。")
            return False
        return True

    def _refresh_recent_menu(self) -> None:
        if not hasattr(self, "recent_menu"):
            return
        self.recent_menu.clear()
        paths = self.project_lifecycle_controller.recent_projects()

        if not paths:
            empty = self.recent_menu.addAction("暂无最近项目")
            empty.setEnabled(False)
            return
        for path in paths:
            item = self.recent_menu.addAction(path.name)
            item.setToolTip(str(path))
            item.triggered.connect(lambda _checked=False, p=path: self._load_project(p))

    def _restore_last_project(self) -> None:
        last = self.project_lifecycle_controller.safe_project_path(
            self.config.get("last_project")
        )
        if last is not None:
            self._load_project(last, quiet=True)

    def _update_window_title(self) -> None:
        self.setWindowTitle("Novalist")

    @staticmethod
    def _safe_name(value: str) -> str:
        return ProjectLifecycleController.safe_name(value)

    @staticmethod
    def _safe_project_path(value: object) -> Path | None:
        """Return a readable Novalist project path without leaking OS errors."""
        return ProjectLifecycleController.safe_project_path(value)

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
        if self.ai_controller.is_running():
            QMessageBox.information(self, "AI 任务仍在进行", "请等待当前 AI 任务完成后再退出，以免丢失生成结果。")
            event.ignore()
            return
        self.ai_engine_controller.cleanup()
        event.accept()
