from __future__ import annotations

import re
import shlex
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.config import DEFAULT_CONFIG
from core.project import NovelProject
from ui.icons import IconTextButton
from ui.theme import DARK_COLORS, LIGHT_COLORS


def _words(text: str) -> int:
    chinese = len(re.findall(r"[\u3400-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", text))
    return chinese + latin


class DashboardPage(QWidget):
    new_project_requested = Signal()
    open_project_requested = Signal()
    import_requested = Signal()
    continue_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dashboardPage")
        self._project: NovelProject | None = None
        self._project_title = QLabel("尚未打开项目")
        self._project_meta = QLabel("打开或创建一个项目，开始管理章节、设定和故事记忆。")
        self._project_meta.setObjectName("mutedLabel")
        self._chapter_value = QLabel("0")
        self._word_value = QLabel("0")
        self._character_value = QLabel("0")
        self._status_value = QLabel("等待开始")

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 34, 30)
        root.setSpacing(18)

        header = QFrame()
        header.setObjectName("pageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        eyebrow = QLabel("PROJECTS / OVERVIEW")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("欢迎回来，创作者")
        title.setObjectName("pageTitle")
        subtitle = QLabel("继续上次的创作，或为下一段故事建立清晰的工作空间。")
        subtitle.setObjectName("mutedLabel")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box, 1)
        create = IconTextButton("add", "新建项目")
        create.setObjectName("accentButton")
        create.clicked.connect(self.new_project_requested)
        header_layout.addWidget(create, 0, Qt.AlignmentFlag.AlignTop)
        root.addWidget(header)

        project_card = QFrame()
        project_card.setObjectName("sectionCard")
        project_layout = QHBoxLayout(project_card)
        project_layout.setContentsMargins(20, 18, 20, 18)
        project_layout.setSpacing(18)
        project_text = QVBoxLayout()
        project_text.setSpacing(5)
        label = QLabel("最近项目")
        label.setObjectName("eyebrow")
        self._project_title.setObjectName("sectionTitle")
        project_text.addWidget(label)
        project_text.addWidget(self._project_title)
        project_text.addWidget(self._project_meta)
        project_layout.addLayout(project_text, 1)
        self.continue_button = QPushButton("进入写作台")
        self.continue_button.setObjectName("secondaryButton")
        self.continue_button.clicked.connect(self.continue_requested)
        project_layout.addWidget(self.continue_button, 0, Qt.AlignmentFlag.AlignCenter)
        root.addWidget(project_card)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(12)
        metrics.setVerticalSpacing(12)
        for index, (value, caption) in enumerate(
            (
                (self._chapter_value, "章节"),
                (self._word_value, "总字数"),
                (self._character_value, "角色卡"),
                (self._status_value, "工作状态"),
            )
        ):
            card = QFrame()
            card.setObjectName("statCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(15, 13, 15, 13)
            value.setObjectName("statValue")
            caption_label = QLabel(caption)
            caption_label.setObjectName("statCaption")
            card_layout.addWidget(value)
            card_layout.addWidget(caption_label)
            metrics.addWidget(card, 0, index)
        root.addLayout(metrics)

        lower = QHBoxLayout()
        lower.setSpacing(14)
        quick = self._build_section("快速入口")
        quick_layout = quick.layout()
        assert isinstance(quick_layout, QVBoxLayout)
        for text, slot in (
            ("打开本地项目", self.open_project_requested),
            ("导入 Markdown 章节", self.import_requested),
            ("从写作台继续", self.continue_requested),
        ):
            button = QPushButton(text)
            button.setObjectName("linkButton")
            button.clicked.connect(slot)
            quick_layout.addWidget(button)
        quick_layout.addStretch(1)
        lower.addWidget(quick, 1)

        privacy = self._build_section("本地优先")
        privacy_layout = privacy.layout()
        assert isinstance(privacy_layout, QVBoxLayout)
        privacy_text = QLabel(
            "作品与故事记忆保存在你选择的项目目录中。只有主动发起 AI 任务时，"
            "当前任务所需的章节和设定才会交给本机 dsh 处理。"
        )
        privacy_text.setObjectName("mutedLabel")
        privacy_text.setWordWrap(True)
        privacy_layout.addWidget(privacy_text)
        privacy_layout.addStretch(1)
        lower.addWidget(privacy, 1)
        root.addLayout(lower, 1)

        self.refresh(None)

    @staticmethod
    def _build_section(title: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sectionCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(17, 15, 17, 15)
        layout.setSpacing(7)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        return frame

    def refresh(self, project: NovelProject | None) -> None:
        self._project = project
        if project is None:
            self._project_title.setText("尚未打开项目")
            self._project_meta.setText("打开或创建一个项目，开始管理章节、设定和故事记忆。")
            self._chapter_value.setText("0")
            self._word_value.setText("0")
            self._character_value.setText("0")
            self._status_value.setText("等待开始")
            self.continue_button.setEnabled(False)
            return
        chapters = project.list_chapters()
        total_words = 0
        for path in chapters:
            try:
                total_words += _words(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                continue
        self._project_title.setText(project.name)
        self._project_meta.setText(f"{project.root} · 最近打开的本地项目")
        self._chapter_value.setText(f"{len(chapters)}")
        self._word_value.setText(f"{total_words:,}")
        self._character_value.setText(f"{len(project.list_characters())}")
        self._status_value.setText("可以继续写作")
        self.continue_button.setEnabled(bool(chapters))


class ReportsPage(QWidget):
    run_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("reportsPage")
        self._project: NovelProject | None = None
        self.browser = QTextBrowser()
        self.browser.setObjectName("pageBrowser")
        self.browser.setOpenExternalLinks(False)
        self._result = ""
        self._values = [QLabel("0"), QLabel("0"), QLabel("0")]

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 34, 30)
        root.setSpacing(18)
        header = QFrame()
        header.setObjectName("pageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        eyebrow = QLabel("REPORTS / CONSISTENCY")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("检查报告")
        title.setObjectName("pageTitle")
        subtitle = QLabel("集中查看最近一次设定一致性检查结果。")
        subtitle.setObjectName("mutedLabel")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box, 1)
        run = QPushButton("运行检查")
        run.setObjectName("accentButton")
        run.clicked.connect(self.run_requested)
        header_layout.addWidget(run, 0, Qt.AlignmentFlag.AlignTop)
        root.addWidget(header)

        summary = QGridLayout()
        summary.setHorizontalSpacing(12)
        for index, caption in enumerate(("严重问题", "警告", "最近检查")):
            card = QFrame()
            card.setObjectName("reportSummaryCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(15, 13, 15, 13)
            self._values[index].setObjectName("statValue")
            caption_label = QLabel(caption)
            caption_label.setObjectName("statCaption")
            card_layout.addWidget(self._values[index])
            card_layout.addWidget(caption_label)
            summary.addWidget(card, 0, index)
        root.addLayout(summary)
        root.addWidget(self.browser, 1)
        self.show_project(None)

    def show_project(self, project: NovelProject | None) -> None:
        self._project = project
        if project is None:
            self.browser.setHtml("<h2>尚无检查报告</h2><p>打开项目并选择章节后，可以运行一致性检查。</p>")
            for value in self._values:
                value.setText("0")
            return
        if self._result:
            self.show_result(self._result)
        else:
            self.browser.setHtml(
                "<h2>尚未检查当前章节</h2>"
                "<p>检查结果会显示角色状态、世界观、战力体系和时间线的潜在冲突。</p>"
            )

    def show_result(self, result: str) -> None:
        self._result = result or ""
        severe = len(re.findall(r"严重|critical|severe", self._result, re.IGNORECASE))
        warning = len(re.findall(r"警告|warning|冲突|inconsisten", self._result, re.IGNORECASE))
        self._values[0].setText(str(severe))
        self._values[1].setText(str(warning))
        self._values[2].setText("刚刚")
        escaped = (
            self._result.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        self.browser.setHtml(f"<h2>最新检查结果</h2><div>{escaped}</div>")


class ExportPage(QWidget):
    export_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("exportPage")
        self._project: NovelProject | None = None
        self._chapter_checks: list[tuple[str, QListWidgetItem]] = []
        self.format_combo = QComboBox()
        self.format_combo.addItem("Markdown 文档 (.md)", "md")
        self.format_combo.addItem("纯文本 (.txt)", "txt")
        self.include_title = QCheckBox("包含作品标题")
        self.include_toc = QCheckBox("包含目录")
        self.separators = QCheckBox("章节之间加入分隔线")
        self.strip = QCheckBox("移除 Markdown 格式")
        self.include_title.setChecked(True)
        self.include_toc.setChecked(True)
        self.preview = QTextBrowser()
        self.preview.setObjectName("pageBrowser")

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 34, 30)
        root.setSpacing(18)
        header = QFrame()
        header.setObjectName("pageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        eyebrow = QLabel("EXPORT / MANUSCRIPT")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("导出作品")
        title.setObjectName("pageTitle")
        subtitle = QLabel("确认章节范围和格式后，生成一份本地可读的整书文件。")
        subtitle.setObjectName("mutedLabel")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box, 1)
        export = QPushButton("导出当前选择")
        export.setObjectName("accentButton")
        export.clicked.connect(lambda: self.export_requested.emit(self.options()))
        header_layout.addWidget(export, 0, Qt.AlignmentFlag.AlignTop)
        root.addWidget(header)

        columns = QHBoxLayout()
        columns.setSpacing(14)
        options_card = QFrame()
        options_card.setObjectName("sectionCard")
        options_layout = QVBoxLayout(options_card)
        options_layout.setContentsMargins(16, 15, 16, 15)
        options_layout.setSpacing(10)
        heading = QLabel("导出设置")
        heading.setObjectName("sectionTitle")
        options_layout.addWidget(heading)
        form = QFormLayout()
        form.addRow("格式", self.format_combo)
        options_layout.addLayout(form)
        for checkbox in (self.include_title, self.include_toc, self.separators, self.strip):
            options_layout.addWidget(checkbox)
        chapter_label = QLabel("章节范围")
        chapter_label.setObjectName("sectionTitle")
        options_layout.addWidget(chapter_label)
        self.chapter_list = QListWidget()
        self.chapter_list.setObjectName("chapterList")
        self.chapter_list.setMinimumWidth(290)
        options_layout.addWidget(self.chapter_list, 1)
        columns.addWidget(options_card, 0)

        preview_card = QFrame()
        preview_card.setObjectName("sectionCard")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(16, 15, 16, 15)
        preview_layout.setSpacing(10)
        preview_title = QLabel("文档预览")
        preview_title.setObjectName("sectionTitle")
        preview_layout.addWidget(preview_title)
        preview_layout.addWidget(self.preview, 1)
        columns.addWidget(preview_card, 1)
        root.addLayout(columns, 1)

        self.format_combo.currentIndexChanged.connect(self.render_preview)
        for checkbox in (self.include_title, self.include_toc, self.separators, self.strip):
            checkbox.toggled.connect(self.render_preview)
        self.chapter_list.itemChanged.connect(lambda _item: self.render_preview())
        self.show_project(None)

    def show_project(self, project: NovelProject | None) -> None:
        self._project = project
        self.chapter_list.blockSignals(True)
        self.chapter_list.clear()
        self._chapter_checks.clear()
        if project:
            for path in project.list_chapters():
                chapter = project.load_chapter(path.stem)
                item = QListWidgetItem(self.chapter_list)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
                item.setText(f"{chapter.title}  ·  {path.stem}")
                self._chapter_checks.append((path.stem, item))
        self.chapter_list.blockSignals(False)
        self.render_preview()

    def options(self) -> dict:
        selected = [chapter_id for chapter_id, item in self._chapter_checks if item.checkState() == Qt.CheckState.Checked]
        return {
            "format": self.format_combo.currentData(),
            "chapter_ids": selected,
            "include_title": self.include_title.isChecked(),
            "include_toc": self.include_toc.isChecked(),
            "separators": self.separators.isChecked(),
            "strip": self.strip.isChecked(),
        }

    def render_preview(self) -> None:
        if self._project is None:
            self.preview.setPlainText("打开项目后，这里会显示导出预览。")
            return
        options = self.options()
        selected = set(options["chapter_ids"])
        chapters = [path for path in self._project.list_chapters() if path.stem in selected]
        blocks: list[str] = []
        if options["include_title"]:
            blocks.append(self._project.name)
        if options["include_toc"]:
            blocks.append("目录\n" + "\n".join(
                f"{index}. {self._project.load_chapter(path.stem).title}"
                for index, path in enumerate(chapters, 1)
            ))
        for path in chapters:
            try:
                raw = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            content = _strip_markdown(raw) if options["strip"] or options["format"] == "txt" else raw
            blocks.append(content.strip())
        separator = "\n\n***\n\n" if options["separators"] else "\n\n\n"
        text = separator.join(blocks).strip()
        self.preview.setPlainText(text or "请选择至少一个章节以生成预览。")


class SettingsPage(QWidget):
    """Application settings with one explicit DeepSeek Harness integration."""

    save_requested = Signal(object)
    test_requested = Signal(object)

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self._config = dict(config)

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 34, 30)
        root.setSpacing(18)
        header = QFrame()
        header.setObjectName("pageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        eyebrow = QLabel("SETTINGS / WORKSPACE")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("设置")
        title.setObjectName("pageTitle")
        subtitle = QLabel("只配置 Novalist 本身和本机 DeepSeek Harness，不保存任何 API 密钥。")
        subtitle.setObjectName("mutedLabel")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box, 1)
        save = QPushButton("保存设置")
        save.setObjectName("accentButton")
        save.clicked.connect(self._emit_save)
        header_layout.addWidget(save, 0, Qt.AlignmentFlag.AlignTop)
        root.addWidget(header)

        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("pageContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(2, 2, 10, 12)
        content_layout.setSpacing(14)

        writing = QFrame()
        writing.setObjectName("settingsSection")
        writing_layout = QVBoxLayout(writing)
        writing_layout.setContentsMargins(17, 15, 17, 17)
        writing_layout.setSpacing(9)
        writing_title = QLabel("写作体验")
        writing_title.setObjectName("sectionTitle")
        writing_layout.addWidget(writing_title)
        writing_form = QFormLayout()
        self.auto_save = QCheckBox("定时保存正在编辑的文件")
        self.auto_save_interval = QSpinBox()
        self.auto_save_interval.setRange(5, 600)
        self.auto_save_interval.setSuffix(" 秒")
        self.editor_font_size = QSpinBox()
        self.editor_font_size.setRange(12, 36)
        self.editor_font_size.setSuffix(" px")
        self.expand_target_chars = QSpinBox()
        self.expand_target_chars.setRange(300, 10000)
        self.expand_target_chars.setSingleStep(100)
        self.expand_target_chars.setSuffix(" 字")
        writing_form.addRow("自动保存", self.auto_save)
        writing_form.addRow("保存间隔", self.auto_save_interval)
        writing_form.addRow("正文字号", self.editor_font_size)
        writing_form.addRow("AI 扩写字数", self.expand_target_chars)
        writing_layout.addLayout(writing_form)
        content_layout.addWidget(writing)

        ai = QFrame()
        ai.setObjectName("settingsSection")
        ai_layout = QVBoxLayout(ai)
        ai_layout.setContentsMargins(17, 15, 17, 17)
        ai_layout.setSpacing(9)
        ai_title = QLabel("DeepSeek Harness")
        ai_title.setObjectName("sectionTitle")
        ai_hint = QLabel(
            "Novalist 只通过 dsh 的 headless 配置调用 DeepSeek Harness。"
            "凭据、模型和连接由 Harness 自己管理，不在这里重复配置。"
        )
        ai_hint.setObjectName("mutedLabel")
        ai_hint.setWordWrap(True)
        ai_layout.addWidget(ai_title)
        ai_layout.addWidget(ai_hint)
        ai_form = QFormLayout()
        self.command = QLineEdit()
        self.command.setPlaceholderText("dsh 或可执行文件路径")
        self.launcher_args = QLineEdit()
        self.launcher_args.setPlaceholderText("直接使用 dsh 时留空；npx 可填写 --yes @deepseek-ai/dsh")
        self.extra_args = QLineEdit()
        self.extra_args.setPlaceholderText("可选的 dsh 参数")
        self.profile = QLabel("headless（固定）")
        self.profile.setObjectName("mutedLabel")
        self.timeout = QSpinBox()
        self.timeout.setRange(30, 1800)
        self.timeout.setSuffix(" 秒")
        ai_form.addRow("命令", self.command)
        ai_form.addRow("启动参数", self.launcher_args)
        ai_form.addRow("运行配置", self.profile)
        ai_form.addRow("附加参数", self.extra_args)
        ai_form.addRow("最长等待", self.timeout)
        ai_layout.addLayout(ai_form)
        ai_actions = QHBoxLayout()
        self.test_button = QPushButton("测试 dsh")
        self.test_button.setObjectName("secondaryButton")
        self.test_button.clicked.connect(self._emit_test)
        self.test_status = QLabel("未测试")
        self.test_status.setObjectName("mutedLabel")
        ai_actions.addWidget(self.test_button)
        ai_actions.addWidget(self.test_status)
        ai_actions.addStretch(1)
        ai_layout.addLayout(ai_actions)
        content_layout.addWidget(ai)

        appearance = QFrame()
        appearance.setObjectName("settingsSection")
        appearance_layout = QVBoxLayout(appearance)
        appearance_layout.setContentsMargins(17, 15, 17, 17)
        appearance_layout.setSpacing(9)
        appearance_title = QLabel("外观")
        appearance_title.setObjectName("sectionTitle")
        appearance_hint = QLabel("浅色主题使用蓝白工作台；深色主题使用蓝黑工作台。保存后应用。")
        appearance_hint.setObjectName("mutedLabel")
        appearance_hint.setWordWrap(True)
        appearance_layout.addWidget(appearance_title)
        appearance_layout.addWidget(appearance_hint)
        appearance_form = QFormLayout()
        self.theme = QComboBox()
        self.theme.addItem("浅色 · 蓝白", "light")
        self.theme.addItem("深色 · 蓝黑", "dark")
        self.ui_font_size = QSpinBox()
        self.ui_font_size.setRange(10, 22)
        self.ui_font_size.setSuffix(" px")
        appearance_form.addRow("主题", self.theme)
        appearance_form.addRow("界面字号", self.ui_font_size)
        appearance_layout.addLayout(appearance_form)
        content_layout.addWidget(appearance)

        privacy = QFrame()
        privacy.setObjectName("settingsSection")
        privacy_layout = QVBoxLayout(privacy)
        privacy_layout.setContentsMargins(17, 15, 17, 17)
        privacy_layout.setSpacing(5)
        privacy_title = QLabel("数据与隐私")
        privacy_title.setObjectName("sectionTitle")
        privacy_text = QLabel(
            "作品、项目配置和故事记忆默认保存在本地。主动运行 AI 任务时，"
            "当前章节及任务需要的上下文可能通过本机 dsh 发送给其配置的服务。"
        )
        privacy_text.setObjectName("mutedLabel")
        privacy_text.setWordWrap(True)
        privacy_layout.addWidget(privacy_title)
        privacy_layout.addWidget(privacy_text)
        content_layout.addWidget(privacy)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        reset = QPushButton("恢复默认设置")
        reset.setObjectName("ghostButton")
        reset.clicked.connect(self._restore_defaults)
        root.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)
        self.auto_save.toggled.connect(self.auto_save_interval.setEnabled)
        self.set_config(config)

    def set_config(self, config: dict) -> None:
        self._config = dict(config)
        self.auto_save.setChecked(bool(config.get("auto_save", True)))
        self.auto_save_interval.setValue(int(config.get("auto_save_interval", 30)))
        self.editor_font_size.setValue(int(config.get("editor_font_size", 16)))
        self.expand_target_chars.setValue(
            int(config.get("expand_target_chars", config.get("continue_target_chars", 2000)))
        )
        self.command.setText(str(config.get("dsh_command", "dsh")))
        self.launcher_args.setText(shlex.join(config.get("dsh_launcher_args") or []))
        self.extra_args.setText(shlex.join(config.get("dsh_extra_args") or []))
        self.timeout.setValue(int(config.get("dsh_timeout", 600)))
        self.theme.setCurrentIndex(0 if config.get("theme", "light") == "light" else 1)
        self.ui_font_size.setValue(int(config.get("ui_font_size", 14)))
        self.auto_save_interval.setEnabled(self.auto_save.isChecked())

    def config(self) -> dict:
        try:
            launcher_args = shlex.split(self.launcher_args.text())
            extra_args = shlex.split(self.extra_args.text())
        except ValueError:
            raise ValueError("启动参数或附加参数的引号不完整。") from None
        result = dict(self._config)
        result.update(
            {
                "auto_save": self.auto_save.isChecked(),
                "auto_save_interval": self.auto_save_interval.value(),
                "editor_font_size": self.editor_font_size.value(),
                "expand_target_chars": self.expand_target_chars.value(),
                "dsh_command": self.command.text().strip() or "dsh",
                "dsh_launcher_args": launcher_args,
                "dsh_profile": "headless",
                "dsh_extra_args": extra_args,
                "dsh_timeout": self.timeout.value(),
                "theme": self.theme.currentData(),
                "ui_font_size": self.ui_font_size.value(),
            }
        )
        palette = LIGHT_COLORS if result["theme"] == "light" else DARK_COLORS
        result.update(palette)
        return result

    def _emit_save(self) -> None:
        try:
            config = self.config()
        except ValueError as exc:
            self.test_status.setText(str(exc))
            return
        self.save_requested.emit(config)

    def set_test_status(self, text: str) -> None:
        self.test_status.setText(text)

    def _emit_test(self) -> None:
        try:
            config = self.config()
        except ValueError as exc:
            self.test_status.setText(str(exc))
            return
        self.test_requested.emit(config)

    def _restore_defaults(self) -> None:
        self.set_config(DEFAULT_CONFIG)
