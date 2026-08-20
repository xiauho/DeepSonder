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
        toggle_btn.setText("×")
        toggle_btn.setToolTip("收起故事雷达（Ctrl+Shift+I 可恢复）")
        toggle_btn.clicked.connect(self.toggle_requested)
        header_layout.addWidget(toggle_btn)
        layout.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("inspectorTabs")
        self.context_browser = self._browser()
        self.memory_browser = self._browser()
        self.report_browser = self._browser()
        self.tabs.addTab(self.context_browser, "上下文")
        self.tabs.addTab(self.memory_browser, "记忆")
        self.tabs.addTab(self.report_browser, "报告")
        layout.addWidget(self.tabs, 1)

    @staticmethod
    def _browser() -> QTextBrowser:
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setObjectName("inspectorBrowser")
        browser.document().setDefaultStyleSheet(
            "body{font-family:'Microsoft YaHei UI';line-height:1.65;}"
            "h2{font-size:18px;margin:5px 0 10px;}h3{font-size:14px;margin:16px 0 7px;}"
            ".eyebrow{font-size:11px;color:#58A6FF;letter-spacing:1px;}"
            ".muted{color:#8B949E;}.good{color:#3FB950;}"
            ".card{background:#151A21;border:1px solid #30363D;border-radius:7px;padding:9px;margin:5px 0;}"
            ".chip{background:#1C2A39;color:#58A6FF;border-radius:8px;padding:3px 7px;margin-right:4px;}"
            ".metric{display:inline-block;background:#151A21;border:1px solid #30363D;padding:7px;margin:3px;}"
            ".metric b{font-size:17px;color:#58A6FF;}.metric span{font-size:10px;color:#8B949E;margin-left:4px;}"
            ".report{line-height:1.75;}li{margin-bottom:5px;}"
        )
        return browser

    def show_project(self, project: NovelProject | None, chapter_id: str | None = None) -> None:
        if project is None:
            empty = self._empty("等待故事", "打开或新建项目后，这里会整理故事上下文。")
            self.context_browser.setHtml(empty)
            self.memory_browser.setHtml(empty)
            return
        if chapter_id:
            self.show_chapter(project, chapter_id)
        else:
            self.show_general(project)

    def show_general(self, project: NovelProject) -> None:
        chapters = project.list_chapters()
        chars = project.list_characters()
        total_chars = 0
        for path in chapters:
            try:
                text = path.read_text(encoding="utf-8")
                total_chars += len(re.findall(r"[\u3400-\u9fff]", text))
            except OSError:
                pass
        self.context_browser.setHtml(
            f"<div class='eyebrow'>当前项目</div>"
            f"<h2>{html.escape(project.name)}</h2>"
            f"<div class='metric'><b>{len(chapters)}</b><span>章节</span></div>"
            f"<div class='metric'><b>{total_chars:,}</b><span>汉字</span></div>"
            f"<div class='metric'><b>{len(chars)}</b><span>角色</span></div>"
            "<hr><p>从左侧选择章节后，这里会自动聚合相关角色和设定。</p>"
        )
        state = project.load_story_state()
        self.memory_browser.setHtml(self._state_html(state, None))

    def show_chapter(self, project: NovelProject, chapter_id: str) -> None:
        chapter = project.load_chapter(chapter_id)
        related = project.find_related_canon(chapter_id)
        state = project.load_story_state()
        summaries = project.load_chapter_summaries()

        character_names = re.findall(r"^###\s+(.+)$", related.characters, re.MULTILINE)
        characters_html = "".join(
            f"<span class='chip'>{html.escape(name)}</span>" for name in character_names
        ) or "<span class='muted'>正文中暂未识别到角色卡名称</span>"

        outline = html.escape(chapter.outline or "暂无大纲").replace("\n", "<br>")
        context = (
            f"<div class='eyebrow'>正在编辑</div><h2>{html.escape(chapter.title)}</h2>"
            f"<p class='muted'>{html.escape(chapter.id)}</p>"
            f"<h3>本章目标</h3><div class='card'>{outline}</div>"
            f"<h3>出场角色</h3><div>{characters_html}</div>"
        )
        if related.world:
            context += "<h3>世界观已接入</h3><p class='good'>✓ AI 续写会携带世界观设定</p>"
        if related.power:
            context += "<h3>战力体系已接入</h3><p class='good'>✓ 一致性检查会核对战力规则</p>"
        self.context_browser.setHtml(context)
        self.memory_browser.setHtml(self._state_html(state, summaries.get(chapter_id)))

    def show_text(self, title: str, text: str) -> None:
        rendered = html.escape(text).replace("\n", "<br>")
        self.report_browser.setHtml(
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
