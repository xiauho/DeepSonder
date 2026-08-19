from __future__ import annotations

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
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core import consistency, memory, prompt_builder
from core.config import load_config, save_config
from core.dsh_client import DSHClient
from core.project import NovelProject
from ui.editor import Editor
from ui.inspector import Inspector
from ui.left_panel import LeftPanel
from ui.settings_dialog import SettingsDialog
from ui.theme import apply_theme


class DSHTask(QThread):
    """Run a blocking dsh call without freezing the writing interface."""

    success = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        else:
            self.success.emit(result)


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: NovelProject | None = None
        self._task: DSHTask | None = None
        self._task_chapter_id: str | None = None
        self._focus_mode = False
        self.config = load_config()
        self._configure_dsh()

        self.setWindowTitle("Novalist")
        self.setMinimumSize(1024, 680)
        self._build_actions()
        self._build_ui()
        self._build_menus()
        self._build_statusbar()

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

    def _configure_dsh(self) -> None:
        self.dsh = DSHClient(
            dsh_command=self.config.get("dsh_command", "dsh"),
            launcher_args=self.config.get("dsh_launcher_args") or [],
            profile=self.config.get("dsh_profile", "headless"),
            timeout=self.config.get("dsh_timeout", 180),
            extra_args=self.config.get("dsh_extra_args") or [],
        )

    # ------------------------------------------------------------------
    # UI construction
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
        action("export", "导出全书…", self.export_manuscript, "Ctrl+Shift+E")
        action("settings", "偏好设置…", self.open_settings, "Ctrl+,")
        action("quit", "退出", self.close, "Ctrl+Q")
        action("new_chapter", "新建章节", self.new_chapter, "Ctrl+N")
        action("new_character", "新建角色", self.new_character, "Ctrl+Alt+C")
        action("new_world", "新建世界观条目", self.new_world_entry)
        action("undo", "撤销", lambda: self.editor.text_edit.undo(), "Ctrl+Z")
        action("redo", "重做", lambda: self.editor.text_edit.redo(), "Ctrl+Y")
        action("find", "查找与替换", lambda: self.editor.show_find(), "Ctrl+F")
        action("focus", "专注模式", self.toggle_focus_mode, "Ctrl+K")
        action("inspector", "显示/隐藏故事雷达", self.toggle_inspector, "Ctrl+Shift+I")
        action("output", "显示/隐藏 AI 记录", self.toggle_output, "Ctrl+J")
        action("continue", "AI 续写", self.continue_writing, "Ctrl+Enter")
        action("check", "一致性检查", self.check_consistency, "Ctrl+Shift+C")
        action("memory", "更新故事记忆", self.update_memory, "Ctrl+Shift+M")

    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("appRoot")
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.app_header = QFrame()
        self.app_header.setObjectName("appHeader")
        header_layout = QHBoxLayout(self.app_header)
        header_layout.setContentsMargins(18, 10, 18, 10)
        header_layout.setSpacing(11)
        brand = QLabel()
        brand.setObjectName("brandMark")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        app = QApplication.instance()
        if app is not None and not app.windowIcon().isNull():
            brand.setPixmap(app.windowIcon().pixmap(30, 30))
        else:
            brand.setText("N")
        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        brand_title = QLabel("Novalist")
        brand_title.setObjectName("brandTitle")
        self.project_subtitle = QLabel("AI 小说创作工作台")
        self.project_subtitle.setObjectName("mutedLabel")
        brand_box.addWidget(brand_title)
        brand_box.addWidget(self.project_subtitle)
        header_layout.addWidget(brand)
        header_layout.addLayout(brand_box)
        header_layout.addStretch(1)
        self.ai_indicator = QLabel("●  AI 引擎就绪")
        self.ai_indicator.setObjectName("aiStatus")
        header_layout.addWidget(self.ai_indicator)
        preferences_btn = self._action_button("settings", "偏好")
        preferences_btn.setObjectName("ghostButton")
        header_layout.addWidget(preferences_btn)
        root_layout.addWidget(self.app_header)

        self.action_bar = QFrame()
        self.action_bar.setObjectName("actionBar")
        action_layout = QHBoxLayout(self.action_bar)
        action_layout.setContentsMargins(14, 8, 14, 8)
        action_layout.setSpacing(7)
        action_layout.addWidget(self._action_button("new_chapter", "＋ 新章节"))
        action_layout.addWidget(self._action_button("open_project", "打开项目"))
        action_layout.addWidget(self._action_button("save", "保存"))
        action_layout.addWidget(self._action_button("export", "导出"))
        action_layout.addStretch(1)
        focus_btn = self._action_button("focus", "专注模式")
        focus_btn.setObjectName("ghostButton")
        action_layout.addWidget(focus_btn)
        check_btn = self._action_button("check", "检查设定")
        check_btn.setObjectName("secondaryButton")
        action_layout.addWidget(check_btn)
        memory_btn = self._action_button("memory", "更新记忆")
        memory_btn.setObjectName("secondaryButton")
        action_layout.addWidget(memory_btn)
        continue_btn = self._action_button("continue", "✦ AI 续写")
        continue_btn.setObjectName("accentButton")
        action_layout.addWidget(continue_btn)
        root_layout.addWidget(self.action_bar)

        self.outer_splitter = QSplitter(Qt.Orientation.Vertical)
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setObjectName("mainSplitter")
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
        self.main_splitter.setCollapsible(1, False)

        self.output_container = QFrame()
        self.output_container.setObjectName("outputContainer")
        output_layout = QVBoxLayout(self.output_container)
        output_layout.setContentsMargins(14, 8, 14, 10)
        output_layout.setSpacing(6)
        output_header = QHBoxLayout()
        output_title = QLabel("AI 工作记录")
        output_title.setObjectName("panelTitle")
        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("ghostButton")
        clear_btn.clicked.connect(lambda: self.output_panel.clear())
        close_btn = QPushButton("收起")
        close_btn.setObjectName("ghostButton")
        close_btn.clicked.connect(self.toggle_output)
        output_header.addWidget(output_title)
        output_header.addStretch(1)
        output_header.addWidget(clear_btn)
        output_header.addWidget(close_btn)
        output_layout.addLayout(output_header)
        self.output_panel = QPlainTextEdit()
        self.output_panel.setObjectName("outputPanel")
        self.output_panel.setReadOnly(True)
        self.output_panel.setPlaceholderText("AI 续写、设定检查和记忆更新的过程会记录在这里。")
        output_layout.addWidget(self.output_panel, 1)

        self.outer_splitter.addWidget(self.main_splitter)
        self.outer_splitter.addWidget(self.output_container)
        self.outer_splitter.setStretchFactor(0, 1)
        self.outer_splitter.setStretchFactor(1, 0)
        self.outer_splitter.setSizes([720, 170])
        root_layout.addWidget(self.outer_splitter, 1)

        self.left_panel.file_selected.connect(self._on_file_selected)
        self.left_panel.new_chapter_requested.connect(self.new_chapter)
        self.editor.dirty_changed.connect(self._on_dirty_changed)
        self.editor.file_saved.connect(lambda _path: self._refresh_current_context())
        self.editor.exit_focus_button.clicked.connect(self._exit_focus_mode)

    def _action_button(self, key: str, text: str) -> QToolButton:
        button = QToolButton()
        button.setDefaultAction(self.actions[key])
        button.setText(text)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        return button

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
        view_menu.addAction(self.actions["inspector"])
        view_menu.addAction(self.actions["output"])

    def _build_statusbar(self) -> None:
        self.status_message = QLabel("打开一个项目，开始今天的写作")
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

    # ------------------------------------------------------------------
    # Projects and files
    # ------------------------------------------------------------------
    def open_project(self) -> None:
        start = self.project.root if self.project else Path.cwd() / "projects"
        path = QFileDialog.getExistingDirectory(self, "选择小说项目目录", str(start))
        if path:
            self._load_project(Path(path))

    def _load_project(self, path: Path, quiet: bool = False) -> None:
        if not NovelProject.is_project(path):
            if not quiet:
                QMessageBox.warning(self, "无法打开", f"该目录不是有效的 Novalist 创作项目：\n{path}")
            return
        self._save_if_dirty()
        try:
            self.project = NovelProject(path)
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                QMessageBox.critical(self, "加载失败", str(exc))
            return

        self.editor.clear_document("选择一份故事资料")
        self.left_panel.set_project(self.project)
        self.inspector.show_project(self.project)
        self.output_panel.clear()
        self.project_subtitle.setText(self.project.name)
        self.status_message.setText(f"已打开 · {self.project.name}")
        self._remember_project(self.project.root)
        chapters = self.project.list_chapters()
        if chapters:
            self.left_panel.select_path(chapters[0])
        self._update_window_title()

    def new_project(self) -> None:
        parent_dir = QFileDialog.getExistingDirectory(self, "选择新项目存放目录")
        if not parent_dir:
            return
        name, ok = QInputDialog.getText(self, "创建新的故事", "作品名称：", text="我的小说")
        if not ok or not name.strip():
            return
        root = Path(parent_dir) / self._safe_name(name.strip())
        try:
            project = NovelProject.create(root, name=name.strip())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "创建失败", str(exc))
            return
        self._load_project(project.root)

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

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if not dialog.exec():
            return
        self.config = dialog.config()
        save_config(self.config)
        self._configure_dsh()
        self._refresh_auto_save_timer()
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self.config)
        self.status_message.setText("偏好设置已保存")

    def new_chapter(self) -> None:
        if not self._require_project():
            return
        assert self.project is not None
        default_id = f"chapter_{len(self.project.list_chapters()) + 1:02d}"
        title, ok = QInputDialog.getText(self, "新建章节", "章节标题：", text="新章节")
        if not ok or not title.strip():
            return
        chapter_id, ok = QInputDialog.getText(
            self, "新建章节", "文件标识（建议保留默认值）：", text=default_id
        )
        chapter_id = self._safe_name(chapter_id.strip())
        if not ok or not chapter_id:
            return
        path = self.project.chapters_dir / f"{chapter_id}.md"
        if path.exists():
            QMessageBox.warning(self, "章节已存在", f"请换一个文件标识：{chapter_id}")
            return
        path.write_text(
            f"# {title.strip()}\n\n## 大纲\n- 本章目标：\n- 核心冲突：\n- 章节钩子：\n\n## 正文\n\n",
            encoding="utf-8",
        )
        self.left_panel.set_project(self.project)
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
        path.write_text(
            f"# {title.strip()}\n\n## 核心规则\n\n## 历史与现状\n\n## 对剧情的约束\n",
            encoding="utf-8",
        )
        self.left_panel.set_project(self.project)
        self.left_panel.select_path(path)

    def export_manuscript(self) -> None:
        if not self._require_project():
            return
        assert self.project is not None
        self._save_if_dirty()
        suggested = self.project.root / f"{self._safe_name(self.project.name)}-全书.md"
        output, selected = QFileDialog.getSaveFileName(
            self, "导出全书", str(suggested), "Markdown 文档 (*.md);;纯文本 (*.txt)"
        )
        if not output:
            return
        chapters = self.project.list_chapters()
        blocks = []
        for path in chapters:
            text = path.read_text(encoding="utf-8")
            if output.lower().endswith(".txt") or "纯文本" in selected:
                text = re_strip_markdown(text)
            blocks.append(text.strip())
        try:
            Path(output).write_text("\n\n\n".join(blocks) + "\n", encoding="utf-8-sig")
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.status_message.setText(f"已导出 {len(chapters)} 章 · {Path(output).name}")
        QMessageBox.information(self, "导出完成", f"全书已导出到：\n{output}")

    def _on_file_selected(self, category: str, path_str: str) -> None:
        current = self.editor.current_path()
        if current and Path(current) == Path(path_str):
            return
        self._save_if_dirty()
        if self.editor.open_file(category, path_str) and self.project:
            if category == "章节":
                self.inspector.show_project(self.project, Path(path_str).stem)
            else:
                self.inspector.show_project(self.project)
        self._update_window_title()

    # ------------------------------------------------------------------
    # Writing experience
    # ------------------------------------------------------------------
    def toggle_focus_mode(self) -> None:
        self._focus_mode = not self._focus_mode
        visible = not self._focus_mode
        self.app_header.setVisible(visible)
        self.action_bar.setVisible(visible)
        self.menuBar().setVisible(visible)
        self.statusBar().setVisible(visible)
        self.left_panel.setVisible(visible)
        self.inspector.setVisible(visible)
        self.output_container.setVisible(False if self._focus_mode else visible)
        self.editor.exit_focus_button.setVisible(self._focus_mode)
        self.editor.layout().setContentsMargins(80 if self._focus_mode else 18, 30 if self._focus_mode else 14, 80 if self._focus_mode else 18, 20 if self._focus_mode else 12)
        self.actions["focus"].setText("退出专注模式" if self._focus_mode else "专注模式")
        if self._focus_mode:
            self.editor.text_edit.setFocus()

    def _exit_focus_mode(self) -> None:
        if self._focus_mode:
            self.toggle_focus_mode()

    def toggle_inspector(self) -> None:
        self.inspector.setVisible(not self.inspector.isVisible())

    def toggle_output(self) -> None:
        self.output_container.setVisible(not self.output_container.isVisible())
        if self.output_container.isVisible():
            self.outer_splitter.setSizes([700, 180])

    def _auto_save(self) -> None:
        if self.editor.is_dirty() and self.editor.current_path():
            if self.save_current_file(notify=False):
                self.auto_save_status.setText("刚刚自动保存")

    def _refresh_auto_save_timer(self) -> None:
        enabled = bool(self.config.get("auto_save", True))
        seconds = max(5, int(self.config.get("auto_save_interval", 30) or 30))
        if enabled:
            self._auto_save_timer.start(seconds * 1000) if hasattr(self, "_auto_save_timer") else None
        elif hasattr(self, "_auto_save_timer"):
            self._auto_save_timer.stop()
        if hasattr(self, "auto_save_status"):
            self.auto_save_status.setText("自动保存已开启" if enabled else "自动保存已关闭")

    def _on_dirty_changed(self, _dirty: bool) -> None:
        self._update_window_title()

    def _save_if_dirty(self) -> None:
        if self.editor.is_dirty():
            self.save_current_file(notify=False)

    # ------------------------------------------------------------------
    # DSH actions
    # ------------------------------------------------------------------
    def _require_project_and_chapter(self) -> str | None:
        if not self._require_project():
            return None
        chapter_id = self.editor.current_chapter_id()
        if chapter_id is None:
            QMessageBox.information(self, "需要章节", "请先从左侧打开一个章节。")
            return None
        return chapter_id

    def _ensure_ai_notice(self) -> bool:
        """Explain external processing before the first AI-backed action."""
        if self.config.get("ai_notice_acknowledged", False):
            return True

        notice = QMessageBox(self)
        notice.setIcon(QMessageBox.Icon.Information)
        notice.setWindowTitle("使用 AI 功能前请确认")
        notice.setText("AI 功能会将创作内容发送给您配置的 dsh/AI 服务处理。")
        notice.setInformativeText(
            "发送内容可能包括当前章节、故事大纲、角色与世界观设定、章节摘要和故事状态。\n\n"
            "请勿提交无权处理的作品或敏感个人信息。AI 生成内容可能包含错误、遗漏或不当内容，"
            "使用或公开前请自行审阅。Novalist 不保存您的 API 密钥，凭据由 dsh 管理。"
        )
        notice.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        notice.button(QMessageBox.StandardButton.Ok).setText("了解并继续")
        notice.button(QMessageBox.StandardButton.Cancel).setText("暂不使用")
        notice.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if notice.exec() != QMessageBox.StandardButton.Ok:
            return False

        self.config["ai_notice_acknowledged"] = True
        save_config(self.config)
        return True

    def continue_writing(self) -> None:
        chapter_id = self._require_project_and_chapter()
        if (
            chapter_id is None
            or not self._ensure_ai_notice()
            or not self.save_current_file(notify=False)
        ):
            return
        assert self.project is not None
        system_prompt, user_prompt = prompt_builder.build_write_prompt(self.project, chapter_id)
        self._append_output(f"正在续写 · {chapter_id}")
        self._task_chapter_id = chapter_id
        self._start_task(lambda: self.dsh.generate(system_prompt, user_prompt), self._on_continue_done)

    def check_consistency(self) -> None:
        chapter_id = self._require_project_and_chapter()
        if (
            chapter_id is None
            or not self._ensure_ai_notice()
            or not self.save_current_file(notify=False)
        ):
            return
        assert self.project is not None
        project = self.project
        self._append_output(f"正在检查设定 · {chapter_id}")
        self._task_chapter_id = chapter_id
        self._start_task(
            lambda: consistency.run_consistency_check(project, chapter_id, self.dsh),
            self._on_check_done,
        )

    def update_memory(self) -> None:
        chapter_id = self._require_project_and_chapter()
        if (
            chapter_id is None
            or not self._ensure_ai_notice()
            or not self.save_current_file(notify=False)
        ):
            return
        assert self.project is not None
        project, dsh = self.project, self.dsh

        def task():
            summary_system, summary_user = prompt_builder.build_summary_prompt(project, chapter_id)
            summary = dsh.generate(summary_system, summary_user)
            state_system, state_user = prompt_builder.build_state_update_prompt(project, chapter_id)
            return summary, dsh.generate_json(state_system, state_user)

        self._append_output(f"正在提炼章节摘要与故事状态 · {chapter_id}")
        self._task_chapter_id = chapter_id
        self._start_task(task, self._on_memory_done)

    def _start_task(self, fn, on_success) -> None:
        if self._task is not None and self._task.isRunning():
            QMessageBox.information(self, "AI 正在工作", "当前任务完成后再试一次。")
            return
        for key in ("continue", "check", "memory"):
            self.actions[key].setEnabled(False)
        self.task_progress.show()
        self.ai_indicator.setText("●  AI 引擎思考中")
        self.ai_indicator.setObjectName("aiStatusBusy")
        self.ai_indicator.style().unpolish(self.ai_indicator)
        self.ai_indicator.style().polish(self.ai_indicator)
        self.status_message.setText("AI 正在整理故事上下文…")
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
        self.ai_indicator.setText("●  AI 引擎就绪")
        self.ai_indicator.setObjectName("aiStatus")
        self.ai_indicator.style().unpolish(self.ai_indicator)
        self.ai_indicator.style().polish(self.ai_indicator)

    def _on_task_failed(self, message: str) -> None:
        self._append_output(f"任务失败\n{message}")
        self.status_message.setText("AI 任务失败")
        QMessageBox.critical(self, "AI 引擎调用失败", message)

    def _on_continue_done(self, result: str) -> None:
        if self.editor.current_chapter_id() == self._task_chapter_id:
            self.editor.append_text(result)
            self.status_message.setText("续写已加入正文，请审阅后保存")
        else:
            self.status_message.setText("续写已完成；因已切换章节，结果保留在 AI 记录中")
        self._append_output(f"续写完成\n{result}")

    def _on_check_done(self, result: str) -> None:
        self._append_output(f"一致性检查完成\n{result}")
        self.inspector.show_text("一致性检查", result)
        self.status_message.setText("一致性检查完成")

    def _on_memory_done(self, result: tuple[str, dict]) -> None:
        summary, new_state = result
        if not isinstance(new_state, dict):
            QMessageBox.warning(self, "更新失败", "AI 返回的故事状态不是有效对象。")
            return
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
        assert self.project is not None
        chapter_id = self._task_chapter_id
        if not chapter_id:
            return
        memory.save_chapter_summary(self.project, chapter_id, summary)
        memory.apply_state_update(self.project, new_state)
        self._append_output(f"长期记忆已更新\n{summary}")
        self.inspector.show_project(self.project, chapter_id)
        self.status_message.setText("长期记忆已更新")

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
        project = self.project.name if self.project else "Novalist"
        document = self.editor.title_label.text() if self.editor.current_path() else ""
        dirty = " •" if self.editor.is_dirty() else ""
        self.setWindowTitle(
            f"{document + ' — ' if document else ''}{project}{dirty} · Novalist"
        )

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
            QMessageBox.information(
                self,
                "AI 任务仍在进行",
                "请等待当前 AI 任务完成后再退出，以免丢失生成结果。",
            )
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
