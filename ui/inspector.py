from __future__ import annotations

import html
import json
import re

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

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
        title = QLabel("故事雷达")
        title.setObjectName("panelTitle")
        subtitle = QLabel("上下文、记忆与检查结果")
        subtitle.setObjectName("mutedLabel")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box, 1)
        toggle_btn = QToolButton()
        toggle_btn.setObjectName("panelToggleButton")
        set_button_icon(toggle_btn, "close", size=17)
        toggle_btn.setToolTip("收起故事雷达（Ctrl+Shift+I 可恢复）")
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
        self.tabs.addTab(self.context_browser, "上下文")
        self.tabs.addTab(self.memory_browser, "记忆")
        self.tabs.addTab(self.report_browser, "报告")
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
