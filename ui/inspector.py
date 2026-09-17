from __future__ import annotations

import html
import json
import re

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.context_report import PromptContextReport
from core.project import NovelProject
from core.project_data import ProjectDataStore
from ui.icons import set_button_icon
from ui.theme import document_css


def rank_character_names(character_names: list[str], chapter_text: str) -> list[str]:
    """Order related characters by their frequency in the current chapter.

    Python's sort is stable, so characters with the same frequency retain the
    canonical filename order returned by the project lookup.
    """
    names = [name.strip() for name in character_names if name.strip()]
    return sorted(names, key=lambda name: -chapter_text.count(name))


SECTION_LABELS = {
    "outline": "本章大纲",
    "plot_brief": "剧情简写",
    "style": "写作风格",
    "content": "章节正文",
    "generated_content": "生成正文复核",
    "selected_foreshadowing": "重点伏笔",
    "core_power": "常驻核心规则",
    "core_systems": "核心体系",
    "selected_power": "重点体系",
    "state": "故事状态",
    "summaries": "历史摘要",
    "characters": "相关角色",
    "future_plan": "后续规划",
    "main_arc": "主线大纲",
    "timeline": "时间线",
    "world": "世界观",
    "power": "其他体系",
}

TASK_LABELS = {
    "expand": "章节扩写",
    "continuation": "章节续写",
    "check": "一致性检查",
    "repair": "一致性修复",
    "memory": "同步上下文",
    "chapter_expansion": "章节扩写",
    "chapter_expansion_retry": "扩写纠偏重试",
    "continuation_current_chapter": "章节续写",
    "continuation_retry": "续写纠偏重试",
    "foreshadowing_review": "伏笔复核",
    "chapter_summary": "章节摘要",
    "story_state_update": "故事状态更新",
    "chapter_digest_shard": "章节事实归并",
    "chapter_memory_proposal": "章节记忆提案",
    "consistency_check": "一致性检查",
    "consistency_repair": "一致性修复",
}

STATUS_LABELS = {
    "full": "完整",
    "capped": "达到单项上限",
    "trimmed": "受总预算截断",
    "dropped": "已省略",
}

SELECTION_REASON_LABELS = {
    "global_core": "全局核心",
    "author_core": "作者标记为核心",
    "manual_selection": "作者手动选择",
    "title_match": "标题命中",
    "heading_match": "小标题命中",
    "metadata_match": "别名或标签命中",
    "name_match": "名称命中",
    "task_profile": "任务固定需要",
    "background": "低优先级背景",
}

OUTCOME_LABELS = {
    "pending": "等待调用",
    "success": "调用成功",
    "failed": "调用失败",
    "timeout": "调用超时",
    "cancelled": "已取消",
}


def render_context_reports(reports: list[PromptContextReport]) -> str:
    """Render redacted metrics only; prompt text is never accepted here."""
    if not reports:
        return "<h2>正在准备上下文</h2><p class='muted'>完成提示词组装后将在这里显示统计。</p>"
    blocks = [
        "<div class='eyebrow'>AI 上下文报告</div>",
        "<p><a href='novalist://copy-context-report'>复制脱敏诊断信息</a></p>",
    ]
    for index, report in enumerate(reports, start=1):
        health_label = {
            "complete": "上下文充足",
            "partial": "部分内容已裁剪",
            "critical": "关键内容空间不足",
        }[report.health]
        transport_label = {
            "argv": "命令行传输",
            "file": "临时文件传输",
            "file_unavailable": "文件传输不可用",
            "pending": "等待传输",
        }.get(report.transport, report.transport)
        task_label = TASK_LABELS.get(report.task_kind, report.task_kind)
        outcome_label = OUTCOME_LABELS.get(report.outcome, report.outcome)
        blocks.append(
            f"<h2>{index}. {html.escape(task_label)}</h2>"
            f"<p><b>{health_label}</b> · {html.escape(transport_label)}"
            f" · {html.escape(outcome_label)}</p>"
            f"<div class='metric'><b>{report.total_prompt_chars:,}</b>"
            f"<span>/ {report.prompt_budget:,} 字符</span></div>"
            f"<p class='muted'>实际提交 {report.submitted_prompt_chars:,} 字符"
            f" · 启动命令 {report.command_chars:,} 字符</p>"
        )
        if report.estimated_input_tokens:
            budget_text = (
                f" / {report.input_token_budget:,}"
                if report.input_token_budget
                else ""
            )
            blocks.append(
                f"<p class='muted'>估算输入 {report.estimated_input_tokens:,}"
                f"{budget_text} token · 运行预留 "
                f"{report.runtime_reserve_tokens:,} token"
                f" · {html.escape(report.token_estimator)}</p>"
            )
        if report.model_context_window_tokens:
            strategy_label = {
                "compatible": "兼容",
                "balanced": "均衡",
                "deep": "深度",
            }.get(report.context_strategy, report.context_strategy or "未指定")
            used_total = report.estimated_input_tokens + report.runtime_reserve_tokens
            utilization = min(
                100.0,
                used_total * 100 / report.model_context_window_tokens,
            )
            blocks.append(
                f"<p class='muted'>模型窗口 {report.model_context_window_tokens:,} token"
                f" · {html.escape(strategy_label)}策略"
                f" · 输入与预留占用约 {utilization:.1f}%</p>"
            )
        if report.transport == "file":
            verification = "回执已验证" if report.file_ack_verified else "回执未验证"
            retry = (
                f" · 自动重试 {report.file_ack_retry_count} 次"
                if report.file_ack_retry_count
                else ""
            )
            receipt_issue = {
                "file_read_failed": "文件读取失败",
                "missing_ack": "未找到回执",
                "wrong_nonce": "nonce 不匹配",
                "duplicate_ack": "回执重复",
                "late_ack": "回执位置过晚",
            }.get(report.file_ack_error, "")
            issue = f" · 首次异常：{receipt_issue}" if receipt_issue else ""
            blocks.append(
                f"<p class='muted'>任务文件 {report.task_file_bytes:,} 字节"
                f" · {verification}{retry}{issue}</p>"
            )
        if report.history_requested is not None:
            blocks.append(
                "<p>历史摘要：请求 "
                f"{report.history_requested} 章 · 可用 {report.history_available or 0} 章"
                f" · 纳入 {report.history_included or 0} 章"
                f" · 缺少摘要 {report.history_missing} 章"
                f" · 预算排除 {report.history_excluded_budget} 章</p>"
            )
            if report.history_token_budget:
                blocks.append(
                    f"<p class='muted'>历史摘要估算 {report.history_estimated_tokens:,}"
                    f" / {report.history_token_budget:,} token（仍受总预算限制）</p>"
                )
            if report.history_missing:
                blocks.append("<p class='muted'>部分前文章节尚无摘要，可先更新这些章节的故事记忆。</p>")
            blocks.append(
                f"<p>远期记忆：候选 {report.history_remote_candidates} 章 · 命中 "
                f"{report.history_remote_matched} 章 · 纳入 {report.history_remote_included} 章"
                f" · 过期或无效排除 {report.history_stale} 章"
                f" · 近期旧摘要版本未确认 {report.history_unverified} 章</p>"
            )
            if report.history_provenance_error:
                blocks.append("<p>记忆来源记录损坏或版本不兼容，本次已停止引用相关历史；请检查已采用记忆文件。</p>")
            for source in report.history_sources:
                label = "远期关联" if source["kind"] == "remote" else "近期参考"
                reason = "、".join({"entity_match": "人物/地点及别名命中", "keyword_match": "情节关键词命中"}.get(r, r) for r in source["reasons"])
                version = source["source_hash"][:12] or "未确认"
                blocks.append(
                    f"<p class='muted'>{html.escape(source['chapter_id'])} · {label}"
                    f" · 正文版本 {html.escape(version)}"
                    f" · {html.escape(reason or '近期章节')}"
                    f" · 事实引用 {html.escape(', '.join(source['fact_ids'])) or '摘要'}</p>"
                )
        if report.state_scope:
            from core.context_budget import state_scope_label
            blocks.append(f"<p class='muted'>{html.escape(state_scope_label(report.state_scope))}</p>")
        if report.selections:
            selection_rows = []
            for item in report.selections:
                if item.candidates <= 0:
                    continue
                label = SECTION_LABELS.get(item.category, item.category)
                final_count = (
                    str(item.prompt_included)
                    if item.prompt_included is not None
                    else "背景资料"
                )
                reasons = "、".join(
                    f"{SELECTION_REASON_LABELS.get(reason, reason)} {count}"
                    for reason, count in item.reasons
                    if count
                )
                required = f" · 必需 {item.required}" if item.required else ""
                budget_excluded = (
                    max(0, item.included - item.prompt_included)
                    if item.prompt_included is not None
                    else None
                )
                budget_text = (
                    f" · 总预算排除 {budget_excluded}"
                    if budget_excluded is not None
                    else ""
                )
                selection_rows.append(
                    "<li>"
                    f"<b>{html.escape(label)}</b>：候选 {item.candidates}"
                    f" · 相关 {item.matched} · 预选 {item.included}"
                    f" · 最终纳入 {final_count}"
                    f" · 未匹配排除 {item.excluded_unmatched}"
                    f" · 分类容量排除 {item.excluded_capacity}"
                    f"{budget_text}"
                    f"{required}"
                    + (f"<br><span class='muted'>{html.escape(reasons)}</span>" if reasons else "")
                    + "</li>"
                )
            if selection_rows:
                blocks.append("<h3>相关资料筛选</h3><ul>" + "".join(selection_rows) + "</ul>")
        rows = []
        for item in report.sections:
            label = SECTION_LABELS.get(item.key, item.key)
            status = STATUS_LABELS.get(item.status, item.status)
            keep = "保留结尾" if item.keep == "tail" else "保留开头"
            rows.append(
                "<li>"
                f"<b>{html.escape(label)}</b>：{item.sent_chars:,} / {item.source_chars:,} 字符"
                f" · 优先级 {item.priority + 1} · {html.escape(status)}"
                + (f" · {keep}" if item.status in {"capped", "trimmed"} else "")
                + "</li>"
            )
        blocks.append("<ul>" + "".join(rows) + "</ul>")
        if not report.task_file_cleaned:
            blocks.append("<p><b>警告：</b>临时任务文件未能确认清理。</p>")
    return "".join(blocks)


class Inspector(QWidget):
    """Right-hand story intelligence panel."""

    toggle_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("inspectorPanel")
        self.setMinimumWidth(270)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 16, 14, 12)
        layout.setSpacing(10)
        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("写作助手")
        title.setObjectName("panelTitle")
        subtitle = QLabel("上下文、记忆与检查结果")
        subtitle.setObjectName("mutedLabel")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box, 1)
        toggle_btn = QToolButton()
        toggle_btn.setObjectName("panelToggleButton")
        set_button_icon(toggle_btn, "close", size=17)
        toggle_btn.setToolTip("收起写作助手（Ctrl+Shift+I 可恢复）")
        toggle_btn.clicked.connect(self.toggle_requested)
        header_layout.addWidget(toggle_btn)
        layout.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("inspectorTabs")
        self.context_browser = self._browser()
        self.memory_browser = self._browser()
        self.report_browser = self._browser()
        self._browser_html: dict[QTextBrowser, str] = {
            self.context_browser: "",
            self.memory_browser: "",
            self.report_browser: "",
        }
        self._context_reports: list[PromptContextReport] = []
        self.context_browser.anchorClicked.connect(self._on_context_link)
        self.tabs.addTab(self.context_browser, "本章资料")
        self.tabs.addTab(self.memory_browser, "记忆")
        self.tabs.addTab(self.report_browser, "检查结果")
        layout.addWidget(self.tabs, 1)
        self.set_theme({"theme": "light"})

    @staticmethod
    def _browser() -> QTextBrowser:
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setObjectName("inspectorBrowser")
        return browser

    def set_theme(self, config: dict | None = None) -> None:
        style = document_css(config)
        for browser in (self.context_browser, self.memory_browser, self.report_browser):
            browser.document().setDefaultStyleSheet(style)
            html_content = self._browser_html.get(browser)
            if html_content:
                browser.setHtml(html_content)

    def _set_browser_html(self, browser: QTextBrowser, content: str) -> None:
        self._browser_html[browser] = content
        browser.setHtml(content)

    def show_project(self, project: NovelProject | None, chapter_id: str | None = None) -> None:
        if project is None:
            empty = self._empty("等待故事", "打开或新建项目后，这里会整理故事上下文。")
            self._set_browser_html(self.context_browser, empty)
            self._set_browser_html(self.memory_browser, empty)
            return
        if chapter_id:
            self.show_chapter(project, chapter_id)
        else:
            self.show_general(project)

    def begin_context_report(self, task_kind: str, chapter_id: str) -> None:
        """Start a fresh in-memory report group for one user-visible AI task."""
        self._context_reports = []
        title = TASK_LABELS.get(task_kind, task_kind)
        self._set_browser_html(
            self.context_browser,
            f"<div class='eyebrow'>AI 上下文报告</div>"
            f"<h2>{html.escape(title)}</h2>"
            f"<p class='muted'>{html.escape(chapter_id)} · 正在准备上下文…</p>",
        )
        self.tabs.setCurrentWidget(self.context_browser)

    def show_context_report(self, report: PromptContextReport) -> None:
        if not isinstance(report, PromptContextReport):
            return
        self._context_reports.append(report)
        self._set_browser_html(
            self.context_browser,
            render_context_reports(self._context_reports),
        )
        self.tabs.setCurrentWidget(self.context_browser)

    def clear_task_context(self) -> None:
        self._context_reports = []

    def show_task_context(self) -> None:
        """Restore the task report even after normal chapter navigation refreshed the tab."""
        content = (render_context_reports(self._context_reports) if self._context_reports else
                   "<h2>本次上下文</h2><p>本次任务尚未提供上下文用量报告。</p>")
        self._set_browser_html(self.context_browser, content)
        self.tabs.setCurrentWidget(self.context_browser)

    def context_report_payload(self) -> str:
        return json.dumps(
            [report.to_dict() for report in self._context_reports],
            ensure_ascii=False,
            indent=2,
        )

    def _on_context_link(self, url: QUrl) -> None:
        if url.toString() == "novalist://copy-context-report":
            QApplication.clipboard().setText(self.context_report_payload())

    def show_general(self, project: NovelProject) -> None:
        store = ProjectDataStore(project)
        chapters = store.list_chapters()
        chars = store.list_characters()
        total_chars = store.total_chinese_character_count()
        self._set_browser_html(self.context_browser,
            f"<div class='eyebrow'>当前项目</div>"
            f"<h2>{html.escape(project.name)}</h2>"
            f"<div class='metric'><b>{len(chapters)}</b><span>章节</span></div>"
            f"<div class='metric'><b>{total_chars:,}</b><span>汉字</span></div>"
            f"<div class='metric'><b>{len(chars)}</b><span>角色</span></div>"
            "<hr><p>从左侧选择章节后，这里会自动聚合相关角色和设定。</p>"
        )
        state = store.load_story_state()
        self._set_browser_html(self.memory_browser, self._state_html(state, None))

    def show_chapter(self, project: NovelProject, chapter_id: str) -> None:
        store = ProjectDataStore(project)
        chapter = store.load_chapter(chapter_id)
        related = store.find_related_canon(chapter_id)
        state = store.load_story_state()
        summaries = store.load_chapter_summaries()

        character_names = re.findall(r"^###\s+(.+)$", related.characters, re.MULTILINE)
        character_names = rank_character_names(character_names, chapter.content)
        characters_html = (
            "&nbsp;".join(
                f"<span class='chip'>{html.escape(name)}</span>" for name in character_names
            )
            or "<span class='muted'>正文中暂未识别到角色卡名称</span>"
        )

        outline = html.escape(chapter.outline or "暂无大纲").replace("\n", "<br>")
        context = (
            f"<div class='eyebrow'>正在编辑</div><h2>{html.escape(chapter.title)}</h2>"
            f"<p class='muted'>{html.escape(chapter.id)}</p>"
            f"<h3>本章目标</h3><div class='card'>{outline}</div>"
            f"<h3>出场角色</h3><div>{characters_html}</div>"
        )
        if related.world:
            context += "<h3>世界观已接入</h3><p class='good'>✓ AI 扩写会携带世界观设定</p>"
        if related.power or related.core_power or related.core_systems or related.selected_power:
            context += "<h3>体系设定已接入</h3><p class='good'>✓ 一致性检查会核对相关体系规则</p>"
        self._set_browser_html(self.context_browser, context)
        self._set_browser_html(
            self.memory_browser,
            self._state_html(state, summaries.get(chapter_id)),
        )

    def show_text(self, title: str, text: str) -> None:
        rendered = html.escape(text).replace("\n", "<br>")
        self._set_browser_html(
            self.report_browser,
            f"<div class='eyebrow'>AI 分析</div><h2>{html.escape(title)}</h2>"
            f"<div class='report'>{rendered}</div>"
        )
        self.tabs.setCurrentWidget(self.report_browser)

    @staticmethod
    def _state_html(state: dict, summary: str | None) -> str:
        location = html.escape(str(state.get("current_location") or "尚未记录"))
        characters = state.get("characters") or {}
        foreshadowing = state.get("foreshadowing") or []
        summary_html = html.escape(summary or "尚未生成章节摘要").replace("\n", "<br>")
        items = "".join(f"<li>{html.escape(str(item))}</li>" for item in foreshadowing)
        if not items:
            items = "<li class='muted'>暂无未回收伏笔</li>"
        return (
            "<div class='eyebrow'>长期记忆</div><h2>故事状态</h2>"
            f"<div class='card'><b>当前位置</b><br>{location}</div>"
            f"<div class='card'><b>已追踪角色</b><br>{len(characters)} 位</div>"
            f"<h3>本章摘要</h3><div class='card'>{summary_html}</div>"
            f"<h3>未回收伏笔</h3><ul>{items}</ul>"
        )

    @staticmethod
    def _empty(title: str, body: str) -> str:
        return f"<h2>{html.escape(title)}</h2><p>{html.escape(body)}</p>"
