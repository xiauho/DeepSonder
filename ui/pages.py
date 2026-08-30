from __future__ import annotations

import re
import shlex
from typing import Any

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
from core.ai_protocol import consistency_issue_counts, format_consistency_report
from core.export import render_manuscript
from core.project import NovelProject, chapter_number_from_id
from core.project_data import ProjectDataStore
from ui.export_controller import ExportController
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
    recent_chapter_requested = Signal(str)
    new_chapter_requested = Signal()
    outline_requested = Signal()
    memory_requested = Signal()
    canon_requested = Signal()

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
        self._foreshadowing_value = QLabel("0")
        self._recent_chapter_id: str | None = None
        self._recent_title = QLabel("尚未选择章节")
        self._recent_title.setObjectName("dashboardChapterTitle")
        self._recent_meta = QLabel("打开项目后，可从最近编辑的章节继续。")
        self._recent_meta.setObjectName("mutedLabel")
        self._recent_preview = QLabel("暂无章节正文。")
        self._recent_preview.setObjectName("dashboardPreview")
        self._recent_preview.setWordWrap(True)
        self._main_arc_status = QLabel("待建立")
        self._memory_status = QLabel("尚未同步")
        self._foreshadowing_status = QLabel("0 条")
        self._canon_status = QLabel("0 项")
        self._next_step_action = "new_chapter"

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
        self.open_project_button = QPushButton("切换项目")
        self.open_project_button.setObjectName("secondaryButton")
        self.open_project_button.clicked.connect(self.open_project_requested)
        project_layout.addWidget(
            self.open_project_button, 0, Qt.AlignmentFlag.AlignCenter
        )
        root.addWidget(project_card)

        local_info = QFrame()
        local_info.setObjectName("localInfoBar")
        local_layout = QHBoxLayout(local_info)
        local_layout.setContentsMargins(11, 7, 11, 7)
        local_layout.setSpacing(7)
        local_icon = QLabel("✓")
        local_icon.setObjectName("localInfoIcon")
        local_text = QLabel(
            "本地保存 · 仅在主动运行 AI 任务时发送必要的章节与设定上下文"
        )
        local_text.setObjectName("mutedLabel")
        local_text.setWordWrap(True)
        local_layout.addWidget(local_icon)
        local_layout.addWidget(local_text, 1)
        root.addWidget(local_info)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(12)
        metrics.setVerticalSpacing(12)
        for index, (value, caption) in enumerate(
            (
                (self._chapter_value, "章节"),
                (self._word_value, "总字数"),
                (self._character_value, "角色卡"),
                (self._foreshadowing_value, "未回收伏笔"),
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

        continue_section = self._build_section("继续创作")
        continue_layout = continue_section.layout()
        assert isinstance(continue_layout, QVBoxLayout)
        recent_label = QLabel("最近编辑章节")
        recent_label.setObjectName("eyebrow")
        continue_layout.addWidget(recent_label)
        continue_layout.addWidget(self._recent_title)
        continue_layout.addWidget(self._recent_meta)
        continue_layout.addSpacing(6)
        continue_layout.addWidget(self._recent_preview)
        continue_layout.addSpacing(6)
        continue_actions = QHBoxLayout()
        continue_actions.setSpacing(9)
        self.continue_button = IconTextButton("edit", "继续写作")
        self.continue_button.setObjectName("accentButton")
        self.continue_button.clicked.connect(self._continue_recent_chapter)
        self.new_chapter_button = IconTextButton("note_add", "新建章节")
        self.new_chapter_button.setObjectName("secondaryButton")
        self.new_chapter_button.clicked.connect(self.new_chapter_requested)
        self.import_button = IconTextButton("file_open", "导入章节")
        self.import_button.setObjectName("secondaryButton")
        self.import_button.clicked.connect(self.import_requested)
        continue_actions.addWidget(self.continue_button)
        continue_actions.addWidget(self.new_chapter_button)
        continue_actions.addWidget(self.import_button)
        continue_actions.addStretch(1)
        continue_layout.addLayout(continue_actions)
        lower.addWidget(continue_section, 7)

        readiness = self._build_section("项目准备情况")
        readiness_layout = readiness.layout()
        assert isinstance(readiness_layout, QVBoxLayout)
        readiness_intro = QLabel("从项目状态直接进入需要补充的内容。")
        readiness_intro.setObjectName("mutedLabel")
        readiness_layout.addWidget(readiness_intro)
        self._readiness_buttons: list[QPushButton] = []
        for caption, value, slot in (
            ("主线大纲", self._main_arc_status, self.outline_requested),
            ("故事记忆", self._memory_status, self.memory_requested),
            ("未回收伏笔", self._foreshadowing_status, self.memory_requested),
            ("世界观与体系", self._canon_status, self.canon_requested),
        ):
            row_frame = QFrame()
            row_frame.setObjectName("dashboardReadinessRow")
            row = QHBoxLayout(row_frame)
            row.setContentsMargins(11, 7, 11, 7)
            row.setSpacing(12)
            label = QLabel(caption)
            value.setObjectName("dashboardReadinessValue")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            open_button = QPushButton("查看")
            open_button.setObjectName("readinessLinkButton")
            open_button.clicked.connect(slot)
            self._readiness_buttons.append(open_button)
            row.addWidget(label)
            row.addStretch(1)
            row.addWidget(value)
            row.addWidget(open_button)
            readiness_layout.addWidget(row_frame)

        readiness_layout.addSpacing(4)
        next_step = QFrame()
        next_step.setObjectName("dashboardNextStep")
        next_step_layout = QVBoxLayout(next_step)
        next_step_layout.setContentsMargins(12, 10, 12, 10)
        next_step_layout.setSpacing(5)
        next_step_label = QLabel("建议下一步")
        next_step_label.setObjectName("eyebrow")
        self._next_step_text = QLabel("打开或创建项目后，这里会给出建议。")
        self._next_step_text.setObjectName("dashboardNextStepText")
        self._next_step_text.setWordWrap(True)
        self._next_step_button = QPushButton("开始")
        self._next_step_button.setObjectName("smallAccentButton")
        self._next_step_button.clicked.connect(self._run_next_step)
        next_step_action = QHBoxLayout()
        next_step_action.addWidget(self._next_step_text, 1)
        next_step_action.addWidget(self._next_step_button, 0, Qt.AlignmentFlag.AlignVCenter)
        next_step_layout.addWidget(next_step_label)
        next_step_layout.addLayout(next_step_action)
        readiness_layout.addWidget(next_step)
        readiness_layout.addStretch(1)
        lower.addWidget(readiness, 5)
        root.addLayout(lower)
        root.addStretch(1)

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
            self._foreshadowing_value.setText("0")
            self._recent_chapter_id = None
            self._recent_title.setText("尚未选择章节")
            self._recent_meta.setText("打开项目后，可从最近编辑的章节继续。")
            self._recent_preview.setText("暂无章节正文。")
            self._main_arc_status.setText("待建立")
            self._memory_status.setText("尚未同步")
            self._foreshadowing_status.setText("0 条")
            self._canon_status.setText("0 项")
            self.continue_button.setEnabled(False)
            self.new_chapter_button.setEnabled(False)
            self.import_button.setEnabled(False)
            for button in self._readiness_buttons:
                button.setEnabled(False)
            self._next_step_action = "new_chapter"
            self._next_step_text.setText("打开或创建项目后，这里会给出建议。")
            self._next_step_button.setText("开始")
            self._next_step_button.setEnabled(False)
            return
        store = ProjectDataStore(project)
        chapters = store.list_chapters()
        total_words = store.total_word_count(_words)
        try:
            open_foreshadowing = store.load_foreshadowing(status="open")
        except (OSError, ValueError):
            open_foreshadowing = []
        self._project_title.setText(project.name)
        self._project_meta.setText(str(project.root))
        self._chapter_value.setText(f"{len(chapters)}")
        self._word_value.setText(f"{total_words:,}")
        self._character_value.setText(f"{len(project.list_characters())}")
        self._foreshadowing_value.setText(str(len(open_foreshadowing)))
        self.continue_button.setEnabled(bool(chapters))
        self.new_chapter_button.setEnabled(True)
        self.import_button.setEnabled(True)
        for button in self._readiness_buttons:
            button.setEnabled(True)
        self._next_step_button.setEnabled(True)

        self._refresh_recent_chapter(project, chapters)
        self._refresh_readiness(store, len(open_foreshadowing), chapters)

    def _continue_recent_chapter(self) -> None:
        if self._recent_chapter_id:
            self.recent_chapter_requested.emit(self._recent_chapter_id)
        else:
            self.continue_requested.emit()

    def _run_next_step(self) -> None:
        actions = {
            "outline": self.outline_requested,
            "memory": self.memory_requested,
            "canon": self.canon_requested,
            "new_chapter": self.new_chapter_requested,
        }
        if self._next_step_action == "continue":
            self._continue_recent_chapter()
            return
        signal = actions.get(self._next_step_action)
        if signal is not None:
            signal.emit()

    def _refresh_recent_chapter(
        self,
        project: NovelProject,
        chapters: list,
    ) -> None:
        if not chapters:
            self._recent_chapter_id = None
            self._recent_title.setText("尚未创建章节")
            self._recent_meta.setText("新建章节后即可开始写作。")
            self._recent_preview.setText("当前项目暂无章节正文。")
            return

        def modified_at(path) -> int:
            try:
                return path.stat().st_mtime_ns
            except OSError:
                return 0

        path = max(chapters, key=modified_at)
        try:
            chapter = project.load_chapter(path.stem)
        except (OSError, UnicodeError, ValueError):
            self._recent_chapter_id = path.stem
            self._recent_title.setText(path.stem)
            self._recent_meta.setText("最近编辑章节")
            self._recent_preview.setText("章节内容暂时无法预览。")
            return
        self._recent_chapter_id = chapter.id
        self._recent_title.setText(chapter.title or chapter.id)
        chapter_words = _words(chapter.content)
        self._recent_meta.setText(f"{chapter_words:,} 字 · {chapter.id}")
        self._recent_preview.setText(self._chapter_preview(chapter.content))

    def _refresh_readiness(
        self,
        store: ProjectDataStore,
        open_foreshadowing_count: int,
        chapters: list,
    ) -> None:
        try:
            main_arc = store.load_main_arc()
        except (OSError, UnicodeError):
            main_arc = ""
        has_main_arc = self._has_meaningful_markdown(main_arc)
        self._main_arc_status.setText("已建立" if has_main_arc else "待完善")
        try:
            state = store.load_story_state()
        except (OSError, ValueError):
            state = {}
        current_chapter = state.get("current_chapter") if isinstance(state, dict) else None
        if isinstance(current_chapter, int) and current_chapter > 0:
            self._memory_status.setText(f"已同步至第 {current_chapter} 章")
        else:
            self._memory_status.setText("尚未同步")
        self._foreshadowing_status.setText(f"{open_foreshadowing_count} 条")
        canon_count = len(store.list_world()) + len(store.list_power())
        self._canon_status.setText(f"{canon_count} 项")

        chapter_numbers = [
            number
            for path in chapters
            if (number := chapter_number_from_id(path.stem)) is not None
        ]
        latest_chapter = max(chapter_numbers, default=len(chapters))
        memory_is_behind = bool(chapters) and (
            not isinstance(current_chapter, int) or current_chapter < latest_chapter
        )
        if not has_main_arc:
            self._set_next_step(
                "outline", "先完善主线大纲，让后续章节和一致性检查有明确基准。", "完善主线"
            )
        elif memory_is_behind:
            self._set_next_step(
                "memory", "故事记忆尚未覆盖最新章节，建议先同步关键状态。", "同步记忆"
            )
        elif open_foreshadowing_count:
            self._set_next_step(
                "memory", "当前仍有未回收伏笔，可进入故事记忆查看和规划。", "查看伏笔"
            )
        elif canon_count == 0:
            self._set_next_step(
                "canon", "补充世界观或力量体系，可提升后续创作的一致性。", "补充设定"
            )
        elif chapters:
            self._set_next_step(
                "continue", "项目准备情况良好，可以继续最近编辑的章节。", "继续写作"
            )
        else:
            self._set_next_step(
                "new_chapter", "基础资料已经就绪，可以创建第一章。", "新建章节"
            )

    def _set_next_step(self, action: str, text: str, button_text: str) -> None:
        self._next_step_action = action
        self._next_step_text.setText(text)
        self._next_step_button.setText(button_text)

    @staticmethod
    def _chapter_preview(content: str, limit: int = 120) -> str:
        normalized = re.sub(r"\s+", " ", str(content or "")).strip()
        if not normalized:
            return "正文尚未开始，打开章节继续创作。"
        return f"“{normalized[:limit].rstrip()}…”" if len(normalized) > limit else f"“{normalized}”"

    @staticmethod
    def _has_meaningful_markdown(text: str) -> bool:
        for line in str(text or "").splitlines():
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            value = value.lstrip("-* ").strip()
            if value and not value.endswith("："):
                return True
        return False


class ReportsPage(QWidget):
    run_requested = Signal()
    jump_requested = Signal(object)
    repair_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("reportsPage")
        self._project: NovelProject | None = None
        self.browser = QTextBrowser()
        self.browser.setObjectName("pageBrowser")
        self.browser.setOpenExternalLinks(False)
        self._result = ""
        self._report: dict[str, Any] | None = None
        self._report_project_root = ""
        self._values = [QLabel("0"), QLabel("0"), QLabel("0")]
        self._run_button = QPushButton("运行检查")
        self._issues_scroll = QScrollArea()
        self._issues_scroll.setWidgetResizable(True)
        self._issues_content = QWidget()
        self._issues_layout = QVBoxLayout(self._issues_content)
        self._issues_layout.setContentsMargins(2, 2, 2, 2)
        self._issues_layout.setSpacing(10)
        self._issues_scroll.setWidget(self._issues_content)

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
        run = self._run_button
        run.setObjectName("accentButton")
        run.clicked.connect(self.run_requested)
        header_layout.addWidget(run, 0, Qt.AlignmentFlag.AlignTop)
        root.addWidget(header)

        summary = QGridLayout()
        summary.setHorizontalSpacing(12)
        for index, caption in enumerate(("严重问题", "警告", "提示")):
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
        root.addWidget(self._issues_scroll, 1)
        self._issues_scroll.hide()
        self.show_project(None)

    def set_check_running(self, running: bool) -> None:
        """Disable the report-page entry while any AI task is active."""
        running = bool(running)
        self._run_button.setEnabled(not running)
        self._run_button.setText("检查中…" if running else "运行检查")

    def last_report_chapter_id(self) -> str | None:
        """Return the chapter represented by the currently displayed report."""
        if not isinstance(self._report, dict):
            return None
        chapter_id = str(self._report.get("chapter_id") or "").strip()
        return chapter_id or None

    def show_project(self, project: NovelProject | None) -> None:
        previous_root = self._report_project_root
        self._project = project
        if project is None:
            self._clear_result()
            self.browser.setHtml("<h2>尚无检查报告</h2><p>打开项目并选择章节后，可以运行一致性检查。</p>")
            return
        project_root = str(project.root.resolve()).casefold()
        if previous_root and previous_root != project_root:
            self._clear_result()
        if self._report is not None:
            self._render_report()
        elif self._result:
            self._render_report()
        else:
            self.browser.setHtml(
                "<h2>尚未检查当前章节</h2>"
                "<p>检查结果会显示角色状态、世界观、体系设定和时间线的潜在冲突。</p>"
            )

    def show_result(
        self,
        report: dict[str, Any] | str,
        rendered: str | None = None,
        *,
        project: NovelProject | None = None,
    ) -> None:
        """Display a structured report and derive summary counts from it.

        A plain-string fallback remains for older callers, but all current
        consistency results pass the validated report object.
        """
        if isinstance(report, dict):
            self._report = dict(report)
            self._result = rendered or format_consistency_report(self._report)
            bound_project = project or self._project
            self._report_project_root = (
                str(bound_project.root.resolve()).casefold()
                if bound_project is not None
                else ""
            )
            counts = consistency_issue_counts(self._report)
            self._values[0].setText(str(counts["high"]))
            self._values[1].setText(str(counts["medium"]))
            self._values[2].setText(str(counts["low"]))
        else:
            self._report = None
            self._result = str(report or "")
            self._report_project_root = ""
            # Compatibility for legacy string-only callers. New callers use
            # structured counts above and do not rely on text heuristics.
            severe = len(re.findall(r"严重|critical|severe", self._result, re.IGNORECASE))
            warning = len(re.findall(r"警告|warning", self._result, re.IGNORECASE))
            self._values[0].setText(str(severe))
            self._values[1].setText(str(warning))
            self._values[2].setText("0")
        self._render_report()

    def _render_report(self) -> None:
        if self._report is not None:
            self._render_issue_cards()
            self.browser.hide()
            self._issues_scroll.show()
            return
        escaped = (
            self._result.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        self.browser.setHtml(f"<h2>最新检查结果</h2><div>{escaped}</div>")
        self.browser.show()
        self._issues_scroll.hide()

    def _render_issue_cards(self) -> None:
        while self._issues_layout.count():
            item = self._issues_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        report = self._report or {}
        issues = report.get("issues", [])
        chapter_label = str(
            report.get("chapter_title") or report.get("chapter_id") or "当前章节"
        )
        heading = QLabel(
            f"{chapter_label} · {len(issues)} 条问题"
            if issues
            else f"{chapter_label} · 未发现明显风险"
        )
        heading.setObjectName("sectionTitle")
        self._issues_layout.addWidget(heading)
        if not issues:
            empty = QLabel("当前章节没有需要处理的一致性问题。")
            empty.setObjectName("mutedLabel")
            self._issues_layout.addWidget(empty)
        chapter_id = str(report.get("chapter_id") or "")
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            payload = dict(issue)
            payload["chapter_id"] = chapter_id
            card = QFrame()
            card.setObjectName("reportIssueCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            severity = str(issue.get("severity") or "").casefold()
            category = str(issue.get("category") or "").casefold()
            kind = str(issue.get("kind") or "").casefold()
            from core.ai_protocol import (
                CONSISTENCY_CATEGORY_LABELS,
                CONSISTENCY_KIND_LABELS,
                CONSISTENCY_SEVERITY_LABELS,
            )
            title = QLabel(
                f"[{CONSISTENCY_SEVERITY_LABELS.get(severity, severity)}] "
                f"{CONSISTENCY_CATEGORY_LABELS.get(category, category)} · "
                f"{CONSISTENCY_KIND_LABELS.get(kind, kind)}"
            )
            title.setObjectName("sectionTitle")
            card_layout.addWidget(title)
            description = QLabel(str(issue.get("description") or ""))
            description.setWordWrap(True)
            card_layout.addWidget(description)
            evidence = QLabel(f"证据：{issue.get('evidence') or '未提供'}")
            evidence.setWordWrap(True)
            evidence.setObjectName("mutedLabel")
            card_layout.addWidget(evidence)
            if issue.get("location_hint"):
                location = QLabel(f"位置：{issue['location_hint']}")
                location.setWordWrap(True)
                location.setObjectName("mutedLabel")
                card_layout.addWidget(location)
            if issue.get("source_hint"):
                source = QLabel(f"来源：{issue['source_hint']}")
                source.setWordWrap(True)
                source.setObjectName("mutedLabel")
                card_layout.addWidget(source)
            if issue.get("suggestion"):
                suggestion = QLabel(f"建议：{issue['suggestion']}")
                suggestion.setWordWrap(True)
                suggestion.setObjectName("mutedLabel")
                card_layout.addWidget(suggestion)

            actions = QHBoxLayout()
            anchor = issue.get("chapter_anchor") or {}
            quote = str(issue.get("chapter_quote") or "").strip()
            can_jump = bool(quote) and anchor.get("status", "") not in {"missing", "unresolved", "ambiguous"}
            jump = QPushButton("跳转正文")
            jump.setAutoDefault(False)
            jump.setEnabled(can_jump)
            if not can_jump:
                jump.setToolTip("本问题没有唯一可定位的正文原句，请重新运行检查。")
            else:
                jump.clicked.connect(lambda _checked=False, item=payload: self.jump_requested.emit(item))
            actions.addWidget(jump)
            repairable = (
                can_jump
                and kind in {"hard_conflict", "continuity_risk"}
                and issue.get("recommended_target") == "chapter"
                and issue.get("repairability") == "automatic"
            )
            repair = QPushButton("AI 修复")
            repair.setObjectName("accentButton")
            repair.setAutoDefault(False)
            repair.setEnabled(repairable)
            if not repairable:
                repair.setToolTip("该问题需要人工判断，或缺少可安全替换的正文锚点。")
            else:
                repair.clicked.connect(lambda _checked=False, item=payload: self.repair_requested.emit(item))
            actions.addWidget(repair)
            actions.addStretch(1)
            card_layout.addLayout(actions)
            self._issues_layout.addWidget(card)
        self._issues_layout.addStretch(1)

    def _clear_result(self) -> None:
        self._result = ""
        self._report = None
        self._report_project_root = ""
        for value in self._values:
            value.setText("0")
        self.browser.show()
        self._issues_scroll.hide()


class ExportPage(QWidget):
    export_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("exportPage")
        self._project: NovelProject | None = None
        self._export_controller: ExportController | None = None
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

    def set_export_controller(self, controller: ExportController) -> None:
        self._export_controller = controller
        self.render_preview()

    def show_project(self, project: NovelProject | None) -> None:
        self._project = project
        self.chapter_list.blockSignals(True)
        self.chapter_list.clear()
        self._chapter_checks.clear()
        if project:
            store = ProjectDataStore(project)
            for path in store.list_chapters():
                chapter = store.load_chapter(path.stem)
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
        if self._export_controller is not None:
            text = self._export_controller.render(options)
        else:
            text = render_manuscript(self._project, options)
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
        self.ai_context_history_chapters = QSpinBox()
        self.ai_context_history_chapters.setRange(0, 10)
        self.ai_context_history_chapters.setSingleStep(1)
        self.ai_context_history_chapters.setSuffix(" 章")
        self.ai_context_history_chapters.setToolTip(
            "仅控制携带的前文章节摘要数量；当前章节内容和本章规划按任务规则单独处理。"
        )
        writing_form.addRow("自动保存", self.auto_save)
        writing_form.addRow("保存间隔", self.auto_save_interval)
        writing_form.addRow("正文字号", self.editor_font_size)
        writing_form.addRow("AI 扩写字数", self.expand_target_chars)
        writing_form.addRow("AI 前文参考章节数", self.ai_context_history_chapters)
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

        updates = QFrame()
        updates.setObjectName("settingsSection")
        updates_layout = QVBoxLayout(updates)
        updates_layout.setContentsMargins(17, 15, 17, 17)
        updates_layout.setSpacing(9)
        updates_title = QLabel("软件更新")
        updates_title.setObjectName("sectionTitle")
        updates_hint = QLabel(
            "自动检查最多每 24 小时连接一次 GitHub，只读取 Novalist 发布信息，"
            "不会发送小说内容、AI 凭据或个人配置。"
        )
        updates_hint.setObjectName("mutedLabel")
        updates_hint.setWordWrap(True)
        updates_layout.addWidget(updates_title)
        updates_layout.addWidget(updates_hint)
        updates_form = QFormLayout()
        self.auto_check_updates = QCheckBox("启动后自动检查更新")
        self.update_channel = QComboBox()
        self.update_channel.addItem("测试版 · Beta", "beta")
        self.update_channel.addItem("稳定版 · Stable", "stable")
        updates_form.addRow("自动检查", self.auto_check_updates)
        updates_form.addRow("更新通道", self.update_channel)
        updates_layout.addLayout(updates_form)
        content_layout.addWidget(updates)

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
        self.ai_context_history_chapters.setValue(
            int(config.get("ai_context_history_chapters", 5))
        )
        self.command.setText(str(config.get("dsh_command", "dsh")))
        self.launcher_args.setText(shlex.join(config.get("dsh_launcher_args") or []))
        self.extra_args.setText(shlex.join(config.get("dsh_extra_args") or []))
        self.timeout.setValue(int(config.get("dsh_timeout", 600)))
        self.theme.setCurrentIndex(0 if config.get("theme", "light") == "light" else 1)
        self.ui_font_size.setValue(int(config.get("ui_font_size", 14)))
        self.auto_check_updates.setChecked(
            bool(config.get("auto_check_updates", False))
        )
        channel_index = self.update_channel.findData(
            str(config.get("update_channel") or "beta")
        )
        self.update_channel.setCurrentIndex(max(0, channel_index))
        self.auto_save_interval.setEnabled(self.auto_save.isChecked())

    def synchronize_update_metadata(self, config: dict) -> None:
        """Keep background-check metadata without resetting edited controls."""
        for key in ("last_update_check_at", "skipped_update_version"):
            self._config[key] = str(config.get(key) or "")

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
                "ai_context_history_chapters": self.ai_context_history_chapters.value(),
                "dsh_command": self.command.text().strip() or "dsh",
                "dsh_launcher_args": launcher_args,
                "dsh_profile": "headless",
                "dsh_extra_args": extra_args,
                "dsh_timeout": self.timeout.value(),
                "theme": self.theme.currentData(),
                "ui_font_size": self.ui_font_size.value(),
                "auto_check_updates": self.auto_check_updates.isChecked(),
                "update_channel": self.update_channel.currentData(),
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
