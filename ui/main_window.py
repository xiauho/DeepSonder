from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
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
    QProgressDialog,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.chapter_sections import chapter_body_text
from core.character_card_sync import (
    CharacterCardSyncError,
    apply_character_card_sync,
    build_character_card_sync_proposal,
)
from core.config import get_chapter_target_chars, load_config
from core.continuation import MIN_CONTINUATION_CHARS
from core.project import NovelProject
from core.project_data import ChapterIdConflictError, ProjectDataStore
from core.text_metrics import count_content_chars
from core.update_download_service import VerifiedUpdate
from core.update_install_service import (
    UpdateInstallLaunchError,
    automatic_install_unavailable_reason,
    launch_verified_update_install,
)
from core.update_service import UpdateCheckResult, UpdateInfo
from core.version import AppVersion, load_current_version
from ui.ai_controller import AIController
from ui.ai_engine_controller import AIEngineController
from ui.ai_task_view_controller import AITaskViewController
from ui.ai_workflow_controller import AIWorkflowController
from ui.appearance_controller import AppearanceController
from ui.character_card_sync_dialog import (
    CharacterCardSyncPreviewDialog,
    CharacterCardSyncSelectionDialog,
)
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
from ui.project_setup_dialog import ProjectSetupDialog
from ui.settings_controller import SettingsController
from ui.story_navigation_controller import StoryNavigationController
from ui.trash_dialog import TrashDialog
from ui.update_controller import UpdateController
from ui.update_download_controller import UpdateDownloadController
from ui.update_dialog import UpdateDialog, no_update_notice
from ui.view_refresh_controller import ViewRefreshController
from ui.window_state_controller import WindowStateController


def fallback_chapter_after_delete(
    chapters: list[Path],
    deleted_index: int,
    current_deleted: bool,
) -> Path | None:
    """Choose a neighboring chapter only when the open chapter was deleted."""
    if not current_deleted:
        return None
    remaining = chapters[:deleted_index] + chapters[deleted_index + 1 :]
    if not remaining:
        return None
    return remaining[min(deleted_index, len(remaining) - 1)]


class MainWindow(QMainWindow):
    ROUTES = ("dashboard", "writing", "canon", "memory", "reports", "export", "settings")
    DELETE_CHAPTER_SHORTCUT = "Ctrl+Shift+Delete"
    ACTION_ICONS = {
        "new_chapter": "add",
        "delete_chapter": "delete",
        "open_project": "folder_open",
        "save": "save",
        "export": "ios_share",
        "focus": "center_focus_strong",
        "check": "fact_check",
        "memory": "psychology",
    }
    ACTION_BUTTON_LABELS = {
        "new_chapter": "新建章节",
        "open_project": "打开项目",
        "save": "保存",
        "export": "导出",
        "focus": "专注模式",
        "check": "一致性检查",
        "memory": "更新故事记忆",
    }

    def __init__(self, parent=None, config: dict | None = None):
        super().__init__(parent)
        self.project_session = ProjectSession(self)
        self.ai_controller = AIController(self)
        self.config = dict(config) if config is not None else load_config()
        self._action_icon_buttons: dict[str, IconTextButton] = {}
        self.project_setup_dialog: ProjectSetupDialog | None = None
        self.update_dialog: UpdateDialog | None = None
        self.update_download_progress: QProgressDialog | None = None
        self._pending_update_install: VerifiedUpdate | None = None
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
        self.update_controller = UpdateController(self.config, self)
        self.update_download_controller = UpdateDownloadController(self)
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
            ai_creation_button=self.ai_creation_button,
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
            save_if_dirty=self._save_if_dirty,
            left_panel=self.left_panel,
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
        self._refresh_ai_actions()
        self._show_route("dashboard")

        self.exit_focus_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.exit_focus_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.exit_focus_shortcut.activated.connect(self._exit_focus_mode)
        self.f11_focus_shortcut = QShortcut(QKeySequence("F11"), self)
        self.f11_focus_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.f11_focus_shortcut.activated.connect(self.toggle_focus_mode)

        self._refresh_auto_save_timer()
        QTimer.singleShot(80, self._restore_last_project)
        QTimer.singleShot(1500, self.update_controller.check_automatically)

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
        action("trash", "回收站…", self.open_trash)
        self.actions["trash"].setEnabled(False)
        action("settings", "设置", lambda: self._show_route("settings"), "Ctrl+,")
        action("quit", "退出", self.close, "Ctrl+Q")
        action("new_chapter", "新建章节", self.new_chapter, "Ctrl+N")
        action(
            "delete_chapter",
            "移入回收站",
            self.delete_current_chapter,
            self.DELETE_CHAPTER_SHORTCUT,
        )
        self.actions["delete_chapter"].setEnabled(False)
        action("new_character", "新建角色", self.new_character, "Ctrl+Alt+C")
        action("character_sync", "同步角色档案…", lambda: self.sync_character_card())
        action("new_world", "新建世界观条目", self.new_world_entry)
        action("new_power", "新建体系设定", self.new_power_entry)
        action("new_timeline", "新建时间线", self.new_timeline)
        action("undo", "撤销", lambda: self.editor.undo(), "Ctrl+Z")
        action("redo", "重做", lambda: self.editor.redo(), "Ctrl+Y")
        action("find", "查找与替换", lambda: self.editor.show_find(), "Ctrl+F")
        action("focus", "专注模式", self.toggle_focus_mode, "Ctrl+K")
        action("navigation", "显示/隐藏资料面板", self.toggle_navigation_panel, "Ctrl+Shift+L")
        action("inspector", "显示/隐藏故事雷达", self.toggle_inspector, "Ctrl+Shift+I")
        action("output", "显示/隐藏 AI 记录", self.toggle_output, "Ctrl+J")
        action("expand", "AI 扩写", self.expand_chapter, "Ctrl+Enter")
        action("continuation", "AI 续写", self.continue_chapter, "Ctrl+Alt+Enter")
        action("check", "一致性检查", self.check_consistency, "Ctrl+Shift+C")
        action("memory", "更新故事记忆", self.update_memory, "Ctrl+Shift+M")
        action("check_updates", "检查更新…", self.check_for_updates)
        action("about", "关于 Novalist", self.show_about)

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
        action_layout.addWidget(self._action_button("new_chapter"))
        action_layout.addWidget(self._action_button("open_project"))
        action_layout.addWidget(self._action_button("save"))
        action_layout.addWidget(self._action_button("export"))
        action_layout.addStretch(1)
        focus_button = self._action_button("focus")
        focus_button.setObjectName("ghostButton")
        focus_button.style().unpolish(focus_button)
        focus_button.style().polish(focus_button)
        action_layout.addWidget(focus_button)
        check_button = self._action_button("check")
        check_button.setObjectName("secondaryButton")
        action_layout.addWidget(check_button)
        memory_button = self._action_button("memory")
        memory_button.setObjectName("secondaryButton")
        action_layout.addWidget(memory_button)
        self.ai_creation_button = QToolButton()
        self.ai_creation_button.setObjectName("accentButton")
        self.ai_creation_button.setText("AI 创作")
        self.ai_creation_button.setAccessibleName("AI 创作")
        self.ai_creation_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.ai_creation_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.ai_creation_menu = QMenu(self.ai_creation_button)
        self.ai_creation_menu.addAction(self.actions["expand"])
        self.ai_creation_menu.addAction(self.actions["continuation"])
        self.ai_creation_button.setMenu(self.ai_creation_menu)
        self.ai_creation_button.setProperty("material_icon", "auto_awesome")
        self.ai_creation_button.setProperty("material_icon_size", 17)
        self.actions["expand"].changed.connect(self._sync_ai_creation_button)
        self.actions["continuation"].changed.connect(self._sync_ai_creation_button)
        action_layout.addWidget(self.ai_creation_button)
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
        copy_button = QPushButton("复制")
        copy_button.setObjectName("ghostButton")
        copy_button.clicked.connect(lambda: self.ai_task_view_controller.copy_output())
        clear_button = QPushButton("清空")
        clear_button.setObjectName("ghostButton")
        clear_button.clicked.connect(lambda: self.ai_task_view_controller.clear_output())
        close_button = QPushButton("收起")
        close_button.setObjectName("ghostButton")
        close_button.clicked.connect(self.toggle_output)
        output_header.addWidget(output_title)
        output_header.addStretch(1)
        output_header.addWidget(copy_button)
        output_header.addWidget(clear_button)
        output_header.addWidget(close_button)
        output_layout.addLayout(output_header)
        self.output_panel = QPlainTextEdit()
        self.output_panel.setObjectName("outputPanel")
        self.output_panel.setReadOnly(True)
        self.output_panel.setPlaceholderText("AI 创作、设定检查和记忆更新的过程会记录在这里。")
        output_layout.addWidget(self.output_panel, 1)
        self.output_container.hide()

        self.outer_splitter.addWidget(self.main_splitter)
        self.outer_splitter.addWidget(self.output_container)
        self.outer_splitter.setStretchFactor(0, 1)
        self.outer_splitter.setStretchFactor(1, 0)
        self.outer_splitter.setSizes([720, 170])
        page_layout.addWidget(self.outer_splitter, 1)
        return page

    def _action_button(self, key: str) -> IconTextButton:
        text = self.ACTION_BUTTON_LABELS.get(key, self.actions[key].text())
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

    def _sync_ai_creation_button(self) -> None:
        if not hasattr(self, "ai_creation_button"):
            return
        self.ai_creation_button.setEnabled(
            self.actions["expand"].isEnabled()
            or self.actions["continuation"].isEnabled()
        )

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
        file_menu.addAction(self.actions["trash"])
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
        create_menu.addAction(self.actions["delete_chapter"])
        create_menu.addAction(self.actions["new_character"])
        create_menu.addAction(self.actions["character_sync"])
        create_menu.addAction(self.actions["new_world"])
        create_menu.addAction(self.actions["new_power"])
        create_menu.addAction(self.actions["new_timeline"])
        create_menu.addSeparator()
        create_menu.addAction(self.actions["expand"])
        create_menu.addAction(self.actions["continuation"])
        create_menu.addAction(self.actions["check"])
        create_menu.addAction(self.actions["memory"])

        view_menu = self.menuBar().addMenu("视图")
        view_menu.addAction(self.actions["focus"])
        view_menu.addAction(self.actions["navigation"])
        view_menu.addAction(self.actions["inspector"])
        view_menu.addAction(self.actions["output"])

        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction(self.actions["check_updates"])
        help_menu.addSeparator()
        help_menu.addAction(self.actions["about"])

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
        self.primary_nav.trash_requested.connect(self.open_trash)
        self.dashboard_page.new_project_requested.connect(self.new_project)
        self.dashboard_page.open_project_requested.connect(self.open_project)
        self.dashboard_page.import_requested.connect(self._import_markdown_chapters)
        self.dashboard_page.continue_requested.connect(lambda: self._show_route("writing"))
        self.dashboard_page.recent_chapter_requested.connect(
            self._open_memory_chapter
        )
        self.dashboard_page.new_chapter_requested.connect(self.new_chapter)
        self.dashboard_page.outline_requested.connect(self._open_dashboard_main_arc)
        self.dashboard_page.memory_requested.connect(lambda: self._show_route("memory"))
        self.dashboard_page.canon_requested.connect(lambda: self._show_route("canon"))
        self.left_panel.file_selected.connect(self._on_file_selected)
        self.left_panel.new_chapter_requested.connect(self.new_chapter)
        self.left_panel.new_canon_requested.connect(self.new_canon_entry)
        self.left_panel.system_importance_requested.connect(self.set_system_importance)
        self.left_panel.delete_chapter_requested.connect(self.delete_chapter_by_path)
        self.left_panel.delete_character_requested.connect(self.delete_character_by_path)
        self.left_panel.sync_character_requested.connect(self.sync_character_card)
        self.left_panel.delete_canon_requested.connect(self.delete_canon_by_path)
        self.left_panel.new_timeline_requested.connect(self.new_timeline)
        self.left_panel.toggle_requested.connect(self.toggle_navigation_panel)
        self.memory_page.sync_requested.connect(self.update_memory)
        self.memory_page.chapter_requested.connect(self._open_memory_chapter)
        self.memory_page.foreshadowing_changed.connect(self.project_session.notify_data_changed)
        self.reports_page.run_requested.connect(
            self.ai_workflow_controller.check_from_reports
        )
        self.reports_page.jump_requested.connect(self.ai_workflow_controller.jump_to_issue)
        self.reports_page.repair_requested.connect(self.ai_workflow_controller.repair_issue)
        self.export_page.export_requested.connect(self.export_manuscript)
        self.settings_page.save_requested.connect(self._apply_settings)
        self.settings_page.test_requested.connect(self._test_dsh)
        self.settings_controller.config_changed.connect(self._on_settings_changed)
        self.settings_controller.connection_started.connect(self._on_dsh_test_started)
        self.settings_controller.connection_succeeded.connect(self._on_dsh_test_succeeded)
        self.settings_controller.connection_failed.connect(self._on_dsh_test_failed)
        self.settings_controller.connection_finished.connect(self._on_dsh_test_finished)
        self.update_controller.started.connect(self._on_update_check_started)
        self.update_controller.result_ready.connect(self._on_update_check_result)
        self.update_controller.failed.connect(self._on_update_check_failed)
        self.update_controller.finished.connect(self._on_update_check_finished)
        self.update_controller.config_changed.connect(self._on_update_config_changed)
        self.update_download_controller.started.connect(self._on_update_download_started)
        self.update_download_controller.progress_changed.connect(
            self._on_update_download_progress
        )
        self.update_download_controller.succeeded.connect(
            self._on_update_download_succeeded
        )
        self.update_download_controller.failed.connect(self._on_update_download_failed)
        self.update_download_controller.cancelled.connect(
            self._on_update_download_cancelled
        )
        self.update_download_controller.finished.connect(
            self._on_update_download_finished
        )
        self.editor.dirty_changed.connect(self._on_dirty_changed)
        self.editor.stats_changed.connect(lambda _text: self._refresh_ai_actions())
        self.ai_controller.started.connect(self._on_ai_started)
        self.ai_controller.finished.connect(self._on_ai_finished)
        self.document_controller.auto_saved.connect(self._on_auto_saved)
        self.document_controller.save_conflict_detected.connect(
            self._on_save_conflict_detected
        )
        self.editor.exit_focus_button.clicked.connect(self._exit_focus_mode)

    def _open_dashboard_main_arc(self) -> None:
        project = self.project
        if project is None or not self._show_route("canon"):
            return
        self.left_panel.select_path(project.outline_dir / "main_arc.md")

    # ------------------------------------------------------------------
    # Routing and shared shell
    # ------------------------------------------------------------------
    def _show_route(self, route: str) -> bool:
        if route not in self.ROUTES:
            route = "dashboard"
        if route not in {"dashboard", "settings"} and not self._require_project():
            self.primary_nav.set_active(self.window_state_controller.current_route)
            return False
        if (
            route != self.window_state_controller.current_route
            and self.editor.is_dirty()
            and not self._save_if_dirty()
        ):
            self.primary_nav.set_active(self.window_state_controller.current_route)
            return False

        if route != "settings":
            self.view_refresh_controller.refresh_route(route)
        else:
            self.settings_page.set_config(self.config)
        self.window_state_controller.activate_route(route)
        if route == "canon":
            self.story_navigation_controller.select_default_canon()

        self._update_page_header(route)
        self._update_window_title()
        return True

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
        migration = self.project_session.last_migration_result
        if migration is not None and migration.migrated:
            backup = str(migration.backup_path or "")
            message = (
                f"项目数据已从格式 {migration.from_schema} 迁移到 "
                f"{migration.to_schema}；更新 {len(migration.changed_files)} 个文件。"
            )
            self.status_message.setText(message)
            if not quiet:
                QMessageBox.information(
                    self,
                    "项目数据迁移完成",
                    f"{message}\n\n迁移前备份：\n{backup}",
                )
        return True

    def _on_project_changed(self, project: NovelProject | None) -> None:
        """Update shell state after the view refresh controller switches projects."""
        self.primary_nav.set_project(project.name if project else None)
        if hasattr(self, "ai_task_view_controller"):
            self.ai_task_view_controller.clear_output()
        else:
            self.output_panel.clear()
        self.status_message.setText(
            f"已打开 · {project.name}" if project else "尚未打开项目"
        )
        self._refresh_delete_action()
        self._refresh_trash_access()
        self._refresh_ai_actions()
        self._update_page_header(self.window_state_controller.current_route)

    def _on_ai_started(self, _token) -> None:
        self.reports_page.set_check_running(True)
        self._refresh_delete_action()
        self.primary_nav.set_trash_enabled(False)
        self.actions["trash"].setEnabled(False)
        self._refresh_ai_actions()

    def _on_ai_finished(self, _token) -> None:
        self.reports_page.set_check_running(False)
        self._refresh_delete_action()
        self._refresh_trash_access()
        self._refresh_ai_actions()

    def _refresh_trash_access(self) -> None:
        enabled = self.project is not None and not self.ai_controller.is_running()
        self.primary_nav.set_trash_enabled(enabled)
        self.actions["trash"].setEnabled(enabled)

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
            self._show_route("canon")
            self._show_project_setup(project)

    def save_current_file(self, notify: bool = True) -> bool:
        if not self.editor.current_path():
            if notify:
                QMessageBox.information(self, "保存", "请先打开一份可编辑的故事资料。")
            return False
        if not self.document_controller.save():
            if self.document_controller.save_conflict_path is not None:
                return self._resolve_save_conflict()
            if notify:
                QMessageBox.warning(self, "保存失败", "文件未能保存，请检查写入权限。")
            return False
        if notify:
            self.status_message.setText("已保存")
        self._update_window_title()
        return True

    def _resolve_save_conflict(self) -> bool:
        path = self.document_controller.save_conflict_path
        if path is None:
            return False
        choice = QMessageBox(self)
        choice.setIcon(QMessageBox.Icon.Warning)
        choice.setWindowTitle("文件已被外部修改")
        choice.setText(f"当前文件在编辑期间发生了外部修改：\n{path.name}")
        choice.setInformativeText(
            "重新加载会放弃当前未保存内容；覆盖保存会用编辑器中的内容替换磁盘文件。"
        )
        reload_button = choice.addButton(
            "重新加载外部版本", QMessageBox.ButtonRole.DestructiveRole
        )
        overwrite_button = choice.addButton(
            "覆盖外部修改", QMessageBox.ButtonRole.AcceptRole
        )
        choice.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        choice.exec()
        if choice.clickedButton() is reload_button:
            return self.document_controller.reload_current_file()
        if choice.clickedButton() is overwrite_button:
            return self.document_controller.save(force=True)
        return False

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
        except ChapterIdConflictError as exc:
            answer = QMessageBox.question(
                self,
                "章节 ID 已存在",
                f"章节 ID“{exc.chapter_id}”已经存在。\n\n是否改用可用 ID“{exc.suggested_id}”？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                path = self.document_controller.create_chapter(title, exc.suggested_id)
            except (OSError, ValueError, FileExistsError) as retry_exc:
                QMessageBox.warning(self, "创建失败", str(retry_exc))
                return
        except FileExistsError:
            QMessageBox.warning(self, "章节已存在", f"请换一个文件标识：{chapter_id}")
            return
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "创建失败", str(exc))
            return
        self._show_route("writing")
        self.left_panel.select_path(path)
        self.status_message.setText(f"已创建 · {title.strip()}")

    def open_trash(self) -> None:
        project = self.project
        if project is None:
            QMessageBox.information(self, "尚未打开项目", "请先打开或新建一个小说项目。")
            return
        if self.ai_controller.is_running():
            QMessageBox.information(self, "AI 正在工作", "当前 AI 任务完成后才能管理回收站。")
            return
        dialog = TrashDialog(self.project_session.require_data_store(), self)
        dialog.changed.connect(self.project_session.notify_data_changed)
        dialog.exec()

    def delete_current_chapter(self) -> None:
        category = self.editor.current_category()
        if category == "角色":
            current = self.editor.current_path()
            self._delete_character(Path(current) if current else None)
            return
        if category in {"世界观", "体系设定", "时间线"}:
            current = self.editor.current_path()
            self._delete_canon(category, Path(current) if current else None)
            return
        chapter_id = self.editor.current_chapter_id()
        if chapter_id is None:
            QMessageBox.information(self, "需要章节", "请先打开要删除的章节。")
            return
        self._delete_chapter(chapter_id)

    def delete_chapter_by_path(self, path_str: str) -> None:
        project = self.project
        if project is None:
            return
        path = Path(path_str)
        try:
            if path.resolve().parent != project.chapters_dir.resolve():
                raise ValueError
        except (OSError, ValueError):
            QMessageBox.warning(self, "删除失败", "只能删除当前项目中的章节文件。")
            return
        self._delete_chapter(path.stem)

    def _delete_chapter(self, chapter_id: str) -> None:
        project = self.project
        if project is None:
            return
        if self.ai_controller.is_running():
            QMessageBox.information(self, "AI 正在工作", "当前 AI 任务完成后才能删除章节。")
            return

        target = project.chapters_dir / f"{chapter_id}.md"
        if not target.is_file():
            QMessageBox.warning(self, "删除失败", "目标章节不存在，项目资料可能已经发生变化。")
            self.project_session.notify_data_changed()
            return

        chapters = self.project_session.require_data_store().list_chapters()
        try:
            deleted_index = next(
                index
                for index, path in enumerate(chapters)
                if path.resolve() == target.resolve()
            )
        except StopIteration:
            QMessageBox.warning(self, "删除失败", "目标章节不在当前项目章节目录内。")
            return

        current_path = self.editor.current_path()
        is_current = bool(current_path and Path(current_path).resolve() == target.resolve())
        chapter_title = project.load_chapter(chapter_id).title
        answer = QMessageBox.question(
            self,
            "移入回收站",
            f"确定将《{chapter_title}》移入回收站吗？\n\n"
            "章节文件和对应摘要将移入回收站，之后仍可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        discard_current_changes = False
        if is_current and self.editor.is_dirty():
            choice = QMessageBox(self)
            choice.setIcon(QMessageBox.Icon.Warning)
            choice.setWindowTitle("章节尚未保存")
            choice.setText("当前章节有未保存修改，删除前如何处理？")
            save_button = choice.addButton("保存后删除", QMessageBox.ButtonRole.AcceptRole)
            discard_button = choice.addButton(
                "放弃修改并删除", QMessageBox.ButtonRole.DestructiveRole
            )
            choice.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            choice.exec()
            if choice.clickedButton() is save_button:
                if not self.document_controller.save():
                    QMessageBox.warning(self, "删除失败", "当前章节保存失败，已取消删除。")
                    return
            elif choice.clickedButton() is discard_button:
                discard_current_changes = True
            else:
                return

        fallback = fallback_chapter_after_delete(chapters, deleted_index, is_current)
        try:
            self.document_controller.delete_chapter(
                chapter_id,
                discard_current_changes=discard_current_changes,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return

        if fallback is not None:
            self._show_route("writing")
            self.left_panel.select_path(fallback)
        self.status_message.setText(f"已移入回收站 · {chapter_title}")
        self._refresh_delete_action()

    def delete_character_by_path(self, path_str: str) -> None:
        project = self.project
        if project is None:
            return
        path = Path(path_str)
        characters_dir = project.canon_dir / "characters"
        try:
            if path.resolve().parent != characters_dir.resolve():
                raise ValueError
        except (OSError, ValueError):
            QMessageBox.warning(self, "删除失败", "只能删除当前项目中的角色卡文件。")
            return
        self._delete_character(path)

    def _delete_character(self, path_or_id: Path | str | None) -> None:
        project = self.project
        if project is None or path_or_id is None:
            return
        target = (
            Path(path_or_id)
            if isinstance(path_or_id, Path)
            else project.canon_dir / "characters" / f"{str(path_or_id).strip()}.md"
        )
        characters_dir = project.canon_dir / "characters"
        try:
            if target.resolve().parent != characters_dir.resolve():
                raise ValueError
        except (OSError, ValueError):
            QMessageBox.warning(self, "删除失败", "只能删除当前项目中的角色卡文件。")
            return
        if not target.is_file():
            QMessageBox.warning(self, "删除失败", "目标角色卡不存在，项目资料可能已经发生变化。")
            self.project_session.notify_data_changed()
            return
        if self.ai_controller.is_running():
            QMessageBox.information(self, "AI 正在工作", "当前 AI 任务完成后才能删除角色卡。")
            return

        store = self.project_session.require_data_store()
        character_title = store.chapter_display_name(target)
        answer = QMessageBox.question(
            self,
            "移入回收站",
            f"确定将角色卡“{character_title}”移入回收站吗？\n\n"
            "角色卡文件之后仍可恢复，故事记忆中的角色追踪数据会保留。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        current_path = self.editor.current_path()
        is_current = bool(current_path and Path(current_path).resolve() == target.resolve())
        discard_current_changes = False
        if is_current and self.editor.is_dirty():
            choice = QMessageBox(self)
            choice.setIcon(QMessageBox.Icon.Warning)
            choice.setWindowTitle("角色卡尚未保存")
            choice.setText("当前角色卡有未保存修改，删除前如何处理？")
            save_button = choice.addButton("保存后删除", QMessageBox.ButtonRole.AcceptRole)
            discard_button = choice.addButton(
                "放弃修改并删除", QMessageBox.ButtonRole.DestructiveRole
            )
            choice.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            choice.exec()
            if choice.clickedButton() is save_button:
                if not self.document_controller.save():
                    QMessageBox.warning(self, "删除失败", "当前角色卡保存失败，已取消删除。")
                    return
            elif choice.clickedButton() is discard_button:
                discard_current_changes = True
            else:
                return

        try:
            self.document_controller.delete_character(
                target.stem,
                discard_current_changes=discard_current_changes,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        self.status_message.setText(f"已移入角色卡回收站 · {character_title}")
        self._show_route("canon")
        self._refresh_delete_action()

    def delete_canon_by_path(self, category: str, path_str: str) -> None:
        kind_map = {
            "世界观": "world",
            "world": "world",
            "体系设定": "power",
            "power": "power",
            "时间线": "timeline",
            "timeline": "timeline",
        }
        kind = kind_map.get(str(category or ""))
        if kind is None:
            return
        display = {"world": "世界观", "power": "体系设定", "timeline": "时间线"}[kind]
        self._delete_canon(display, Path(path_str))

    def _delete_canon(self, category: str, path: Path | None) -> None:
        project = self.project
        if project is None or path is None:
            return
        kind_map = {"世界观": "world", "体系设定": "power", "时间线": "timeline"}
        kind = kind_map.get(str(category or ""))
        if kind is None:
            return
        target = Path(path)
        if not target.is_absolute():
            target = project.root / target
        expected_parent = {
            "world": project.canon_dir / "world",
            "power": project.canon_dir / "power",
            "timeline": project.canon_dir,
        }[kind]
        try:
            resolved = target.resolve()
            if resolved.parent != expected_parent.resolve():
                raise ValueError
            if kind == "timeline" and resolved.name != "timeline.md":
                raise ValueError
            if target.suffix.casefold() != ".md":
                raise ValueError
        except (OSError, ValueError):
            QMessageBox.warning(self, "删除失败", "只能删除当前项目中的故事资料文件。")
            return
        store = self.project_session.require_data_store()
        if kind == "power" and store.is_core_power_path(target):
            QMessageBox.information(
                self,
                "核心规则不可删除",
                "核心规则是项目常驻资料，如需修改请直接编辑该文件。",
            )
            return
        if not target.is_file():
            QMessageBox.warning(self, "删除失败", "目标故事资料不存在，项目资料可能已经发生变化。")
            self.project_session.notify_data_changed()
            return
        if self.ai_controller.is_running():
            QMessageBox.information(self, "AI 正在工作", "当前 AI 任务完成后才能删除故事资料。")
            return

        title = store.chapter_display_name(target)
        if kind == "timeline":
            detail = "整份时间线文档之后仍可在回收站恢复；永久删除后可重新创建空白时间线。"
        elif kind == "power":
            detail = "体系的 AI 加载策略会一并保存，故事正文和故事记忆不会被修改。"
        else:
            detail = "该条目之后仍可在回收站恢复。"
        answer = QMessageBox.question(
            self,
            "移入回收站",
            f"确定将{category}“{title}”移入回收站吗？\n\n{detail}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        current_path = self.editor.current_path()
        is_current = bool(current_path and Path(current_path).resolve() == target.resolve())
        discard_current_changes = False
        if is_current and self.editor.is_dirty():
            choice = QMessageBox(self)
            choice.setIcon(QMessageBox.Icon.Warning)
            choice.setWindowTitle("故事资料尚未保存")
            choice.setText(f"当前{category}有未保存修改，删除前如何处理？")
            save_button = choice.addButton("保存后删除", QMessageBox.ButtonRole.AcceptRole)
            discard_button = choice.addButton(
                "放弃修改并删除", QMessageBox.ButtonRole.DestructiveRole
            )
            choice.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            choice.exec()
            if choice.clickedButton() is save_button:
                if not self.document_controller.save():
                    QMessageBox.warning(self, "删除失败", "当前故事资料保存失败，已取消删除。")
                    return
            elif choice.clickedButton() is discard_button:
                discard_current_changes = True
            else:
                return

        try:
            self.document_controller.delete_canon_entry(
                kind,
                target,
                discard_current_changes=discard_current_changes,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        self.status_message.setText(f"已移入回收站 · {title}")
        self._show_route("canon")
        self._refresh_delete_action()

    def new_character(self) -> None:
        self.new_canon_entry("character")

    def sync_character_card(self, card_path: str | None = None) -> None:
        """Preview and apply an evidence-bound sync to one character card."""
        project = self.project
        if project is None:
            self._require_project()
            return
        if self.ai_controller.is_running():
            QMessageBox.information(
                self,
                "AI 正在工作",
                "请等待当前 AI 任务完成后再同步角色档案。",
            )
            return
        if not self._save_if_dirty():
            return

        default_card: Path | str | None = card_path
        if default_card is None and self.editor.current_category() == "角色":
            default_card = self.editor.current_path()
        selection = CharacterCardSyncSelectionDialog(
            project,
            default_card=default_card,
            parent=self,
        )
        if selection.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = selection.selection()
        if chosen is None:
            return
        selected_card, chapter_ids = chosen
        try:
            proposal = build_character_card_sync_proposal(
                project,
                selected_card,
                chapter_ids,
            )
        except (CharacterCardSyncError, OSError, UnicodeError, ValueError) as exc:
            QMessageBox.warning(self, "无法生成同步预览", str(exc))
            return

        preview = CharacterCardSyncPreviewDialog(proposal, self)
        if preview.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            result = apply_character_card_sync(
                project,
                proposal,
                preview.selected_fields(),
            )
        except (CharacterCardSyncError, OSError, UnicodeError, ValueError) as exc:
            QMessageBox.warning(self, "角色档案同步失败", str(exc))
            return

        self.project_session.notify_data_changed([result.card_path], kind="canon")
        current = self.editor.current_path()
        if current and Path(current).resolve() == result.card_path.resolve():
            self.editor.reload_current_file()
        self.left_panel.select_path(result.card_path)
        labels = "、".join(
            patch.label for patch in proposal.patches if patch.field in result.applied_fields
        )
        self.status_message.setText(f"角色档案已同步 · {proposal.character_name}")
        QMessageBox.information(
            self,
            "角色档案已同步",
            f"已更新：{labels}\n本次同步截止章节：{proposal.as_of_chapter}\n\n"
            f"同步前版本已备份到：\n{result.backup_path}",
        )

    def new_world_entry(self) -> None:
        self.new_canon_entry("world")

    def new_power_entry(self) -> None:
        self.new_canon_entry("power")

    def new_timeline(self) -> None:
        if not self._require_project():
            return
        try:
            path = self.document_controller.create_timeline()
        except FileExistsError:
            QMessageBox.information(self, "时间线已存在", "当前项目已经有一份时间线。")
            return
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "创建失败", str(exc))
            return
        self._show_route("canon")
        self.left_panel.select_path(path)
        self.status_message.setText("已创建 · 时间线")

    def new_canon_entry(self, kind: str) -> None:
        if kind == "timeline":
            self.new_timeline()
            return
        if not self._require_project():
            return
        labels = {
            "character": ("角色", "角色姓名：", "新角色", "角色已存在", "同名角色卡已经存在。"),
            "world": ("世界观条目", "条目名称：", "新设定", "条目已存在", "同名世界观条目已经存在。"),
            "power": ("体系设定", "体系名称：", "新体系", "条目已存在", "同名体系设定已经存在。"),
        }
        title_data = labels.get(kind)
        if title_data is None:
            QMessageBox.warning(self, "创建失败", "不支持的故事资料类型。")
            return
        dialog_title, prompt, default, conflict_title, conflict_message = title_data
        title, ok = QInputDialog.getText(self, f"新建{dialog_title}", prompt, text=default)
        if not ok or not title.strip():
            return
        try:
            path = self.document_controller.create_canon_entry(kind, title)
        except FileExistsError:
            QMessageBox.warning(self, conflict_title, conflict_message)
            return
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "创建失败", str(exc))
            return
        self._show_route("canon")
        self.left_panel.select_path(path)
        if self.project_setup_dialog is not None:
            self.project_setup_dialog.refresh(self.project)

    def set_system_importance(self, path_str: str, importance: str) -> None:
        project = self.project
        if project is None:
            return
        path = Path(path_str)
        if not path.is_absolute():
            project_path = project.root / path
            path = project_path if project_path.exists() else path.resolve()
        path = path.resolve()
        try:
            store = ProjectDataStore(project)
            current = store.system_metadata(path).get("importance", "non_core")
            if current == importance:
                label = "核心 · 自动加载" if importance == "core" else "非核心加载"
                self.status_message.setText(f"“{path.stem}”当前已使用{label}")
                return
            store.set_system_importance(path, importance)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "设置体系重要性失败", str(exc))
            return
        self.project_session.notify_data_changed(
            [store.system_registry_path], kind="system_importance"
        )
        if importance == "core":
            message = f"已将“{path.stem}”设为核心 · 后续 AI 任务将自动加载"
        else:
            message = f"已将“{path.stem}”设为非核心 · 可在 AI 扩写时手动选择"
        core_count = len(store.list_core_systems())
        if core_count >= 8:
            message += f"（当前 {core_count} 项核心体系，可能占用较多上下文）"
        self.status_message.setText(message)

    def _show_project_setup(self, project: NovelProject) -> None:
        if self.project_setup_dialog is not None:
            self.project_setup_dialog.close()
        dialog = ProjectSetupDialog(project, self)
        dialog.new_entry_requested.connect(self.new_canon_entry)
        dialog.entry_open_requested.connect(self._open_setup_entry)
        dialog.done_requested.connect(lambda: self._finish_project_setup(dialog))
        dialog.finished.connect(lambda _result: self._clear_project_setup(dialog))
        self.project_setup_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _clear_project_setup(self, dialog: ProjectSetupDialog) -> None:
        if self.project_setup_dialog is dialog:
            self.project_setup_dialog = None

    def _finish_project_setup(self, dialog: ProjectSetupDialog) -> None:
        if self.project_setup_dialog is not dialog:
            return
        dialog.close()
        self._show_route("writing")
        self.status_message.setText("初始化资料完成，可以开始写作")

    def _open_setup_entry(self, path_str: str) -> None:
        project = self.project
        if project is None:
            return
        path = Path(path_str)
        if not path.is_file() or project.canon_dir.resolve() not in path.resolve().parents:
            return
        self._show_route("canon")
        self.left_panel.select_path(path)

    def _on_file_selected(self, category: str, path_str: str) -> None:
        current = self.editor.current_path()
        if current and Path(current) == Path(path_str):
            return
        opened = self.document_controller.open_file(category, path_str)
        if not opened and self.document_controller.save_conflict_path is not None:
            if self._resolve_save_conflict():
                opened = self.document_controller.open_file(category, path_str)
        if opened and self.project:
            self.view_refresh_controller.refresh_inspector(self.project)
            self._show_route(
                self.story_navigation_controller.route_for_category(category)
            )
        self._refresh_delete_action()
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
        self.update_controller.set_config(self.config)
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

    def check_for_updates(self) -> None:
        if (
            not self.update_controller.check(manual=True)
            and self.update_controller.is_running()
        ):
            self.status_message.setText("更新检查已在进行中")

    def show_about(self) -> None:
        try:
            version = load_current_version()
            version_text = f"v{version}"
        except ValueError:
            version_text = "版本未知"
        QMessageBox.information(
            self,
            "关于 Novalist",
            f"Novalist {version_text}\n\n"
            "本地优先的长篇小说创作工具。\n"
            "项目主页：https://github.com/xiauho/novalist",
        )

    def _on_update_check_started(self, manual: bool) -> None:
        self.actions["check_updates"].setEnabled(False)
        if manual:
            self.status_message.setText("正在检查 Novalist 更新…")

    def _on_update_check_result(
        self,
        result: UpdateCheckResult,
        manual: bool,
    ) -> None:
        if self.update_controller.should_present(result, manual=manual):
            if self.update_dialog is not None:
                self.update_dialog.show()
                self.update_dialog.raise_()
                self.update_dialog.activateWindow()
                return
            if result.latest is None:
                return
            dialog = UpdateDialog(result, self)
            self.update_dialog = dialog
            dialog.finished.connect(
                lambda choice,
                release=result.latest,
                current=result.current_version: self._handle_update_choice(
                    choice, release, current
                )
            )
            dialog.finished.connect(
                lambda _choice, target=dialog: self._clear_update_dialog(target)
            )
            dialog.open()
            self.status_message.setText(f"发现新版本 {result.latest.tag_name}")
            return
        if manual:
            notice_title, notice_message = no_update_notice(result)
            QMessageBox.information(
                self,
                notice_title,
                notice_message,
            )
            self.status_message.setText("更新检查完成")

    def _handle_update_choice(
        self,
        choice: int,
        release: UpdateInfo,
        current_version: AppVersion,
    ) -> None:
        if choice == UpdateDialog.OPEN_RELEASE:
            QDesktopServices.openUrl(QUrl(release.release_url))
        elif choice == UpdateDialog.SKIP_VERSION:
            self.update_controller.skip_version(release.tag_name)
            self.status_message.setText(f"已忽略 {release.tag_name}")
        elif choice == UpdateDialog.DOWNLOAD_UPDATE:
            if not self.update_download_controller.download(release, current_version):
                self.status_message.setText("更新下载已在进行中")

    def _on_update_download_started(self, release: UpdateInfo) -> None:
        dialog = QProgressDialog("正在准备安全下载…", "取消下载", 0, 1000, self)
        dialog.setWindowTitle(f"下载 {release.tag_name}")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(self.update_download_controller.cancel)
        dialog.show()
        self.update_download_progress = dialog
        self.actions["check_updates"].setEnabled(False)
        self.status_message.setText(f"正在安全下载 {release.tag_name}…")

    def _on_update_download_progress(self, downloaded: int, total: int) -> None:
        dialog = self.update_download_progress
        if dialog is None:
            return
        ratio = min(1000, int(downloaded * 1000 / total)) if total > 0 else 0
        dialog.setValue(ratio)
        dialog.setLabelText(
            f"正在下载并校验… {downloaded / 1048576:.1f} / "
            f"{total / 1048576:.1f} MiB"
        )

    def _on_update_download_succeeded(self, result: VerifiedUpdate) -> None:
        self._close_update_download_progress()
        unavailable = automatic_install_unavailable_reason()
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Information)
        message.setWindowTitle("更新包已安全下载")
        message.setText(
            f"{result.release.tag_name} 已通过大小、SHA-256 和 ZIP 安全检查。"
        )
        details = f"缓存位置：\n{result.archive_path}"
        if unavailable:
            details += f"\n\n{unavailable}"
        else:
            details += (
                "\n\n可立即退出 Novalist，由独立更新器备份并替换受管程序文件；"
                "新版启动自检失败时会自动恢复当前版本。"
            )
        message.setInformativeText(details)
        message.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
        open_folder = message.addButton(
            "打开缓存目录",
            QMessageBox.ButtonRole.ActionRole,
        )
        install_now = None
        if not unavailable:
            install_now = message.addButton(
                "立即重启并安装",
                QMessageBox.ButtonRole.AcceptRole,
            )
            message.setDefaultButton(install_now)
        message.exec()
        clicked = message.clickedButton()
        if clicked is open_folder:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(result.archive_path.parent))
            )
        elif install_now is not None and clicked is install_now:
            self._pending_update_install = result
            self.status_message.setText(
                f"{result.release.tag_name} 已验证，正在准备自动安装…"
            )
            return
        self.status_message.setText(f"{result.release.tag_name} 已下载并验证")

    def _on_update_download_failed(self, message: str) -> None:
        self._close_update_download_progress()
        QMessageBox.warning(self, "更新下载失败", str(message))
        self.status_message.setText("更新下载或安全校验失败")

    def _on_update_download_cancelled(self) -> None:
        self._close_update_download_progress()
        self.status_message.setText("更新下载已取消")

    def _on_update_download_finished(self) -> None:
        self.actions["check_updates"].setEnabled(True)
        if self._pending_update_install is not None:
            QTimer.singleShot(0, self._launch_pending_update_install)

    def _launch_pending_update_install(self) -> None:
        result = self._pending_update_install
        self._pending_update_install = None
        if result is None:
            return
        if self.ai_controller.is_running():
            QMessageBox.warning(
                self,
                "暂时无法安装更新",
                "AI 任务仍在进行，请等待任务完成后重新检查更新并安装。",
            )
            return
        if self.editor.is_dirty() and not self.save_current_file(notify=False):
            QMessageBox.warning(
                self,
                "暂时无法安装更新",
                "当前文档未能安全保存，已取消自动安装。更新包仍保留在缓存中。",
            )
            return
        try:
            launch = launch_verified_update_install(
                result,
                load_current_version(),
            )
        except (UpdateInstallLaunchError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "无法启动自动安装", str(exc))
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(result.archive_path.parent))
            )
            self.status_message.setText("自动安装未启动，可改用手动安装")
            return
        self.status_message.setText(f"正在退出并安装 v{launch.target_version}…")
        self.ai_engine_controller.cleanup()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _close_update_download_progress(self) -> None:
        dialog = self.update_download_progress
        self.update_download_progress = None
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()

    def _clear_update_dialog(self, dialog: UpdateDialog) -> None:
        if self.update_dialog is dialog:
            self.update_dialog = None
        dialog.deleteLater()

    def _on_update_check_failed(self, message: str, manual: bool) -> None:
        if manual:
            QMessageBox.warning(self, "检查更新失败", str(message))
            self.status_message.setText("更新检查失败")

    def _on_update_check_finished(self, _manual: bool) -> None:
        self.actions["check_updates"].setEnabled(True)

    def _on_update_config_changed(self, config: dict) -> None:
        self.config = dict(config)
        self.settings_controller.synchronize(self.config)
        self.settings_page.synchronize_update_metadata(self.config)

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

    def _on_save_conflict_detected(self, path: str) -> None:
        self.auto_save_status.setText("检测到外部修改，尚未覆盖")
        self.status_message.setText(f"文件已被外部修改：{Path(path).name}")

    def _on_dirty_changed(self, _dirty: bool) -> None:
        self._update_window_title()

    def _refresh_delete_action(self) -> None:
        action = self.actions.get("delete_chapter")
        if action is None:
            return
        enabled = (
            self.project is not None
            and self.editor.current_category()
            in {"章节", "角色", "世界观", "体系设定", "时间线"}
            and self.editor.current_path() is not None
            and not self.ai_controller.is_running()
        )
        if enabled and self.editor.current_category() == "体系设定":
            enabled = not ProjectDataStore(self.project).is_core_power_path(
                Path(self.editor.current_path())
            )
        action.setEnabled(enabled)

    def _save_if_dirty(self) -> bool:
        # Opening the first project normally has no current document yet. In
        # that state there is nothing to save, so project switching must be
        # allowed to continue.
        if not self.editor.is_dirty():
            return True
        return self.save_current_file(notify=False)

    def expand_chapter(self) -> None:
        self.ai_workflow_controller.expand()

    def continue_chapter(self) -> None:
        self.ai_workflow_controller.continue_chapter()

    def _refresh_ai_actions(self) -> None:
        if not hasattr(self, "actions") or not hasattr(self, "editor"):
            return
        running = self.ai_controller.is_running()
        chapter_open = self.project is not None and self.editor.current_chapter_id() is not None
        if "character_sync" in self.actions:
            self.actions["character_sync"].setEnabled(
                self.project is not None
                and not running
                and bool(self.project.list_characters())
                and bool(self.project.list_chapters())
            )
        self.actions["expand"].setEnabled(chapter_open and not running)
        continuation_enabled = False
        continuation_tip = "请先打开一个包含正文的章节。"
        if chapter_open and not running:
            body = chapter_body_text(self.editor.text_edit.toPlainText())
            current = count_content_chars(body)
            target = get_chapter_target_chars(self.config)
            remaining = target - current
            continuation_enabled = current > 0 and remaining >= MIN_CONTINUATION_CHARS
            if current <= 0:
                continuation_tip = "当前正文为空，请先使用 AI 扩写或手动写下开头。"
            elif remaining <= 0:
                continuation_tip = f"当前正文约 {current} 字，已达到目标章节字数 {target} 字。"
            elif remaining < MIN_CONTINUATION_CHARS:
                continuation_tip = f"距离目标章节字数只剩 {remaining} 字，不足最小续写长度。"
            else:
                continuation_tip = (
                    f"从正文末尾续写，本次最多生成 3000 字"
                    f"（当前约 {current}/{target} 字）。"
                )
        elif running:
            continuation_tip = "当前 AI 任务完成后才能继续创作。"
        self.actions["continuation"].setEnabled(continuation_enabled)
        self.actions["continuation"].setToolTip(continuation_tip)
        self.actions["continuation"].setStatusTip(continuation_tip)
        self._sync_ai_creation_button()

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
        if self.update_controller.is_running():
            self.status_message.setText("更新检查仍在进行，请稍候再退出")
            event.ignore()
            return
        if self.update_download_controller.is_running():
            self.status_message.setText("更新下载仍在进行，请先取消或等待完成")
            event.ignore()
            return
        self.ai_engine_controller.cleanup()
        event.accept()
