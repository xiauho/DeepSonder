"""Structured view of a chapter file. Markdown stays the stored text.

Chapters expose the title, outline, plot brief, prose, an optional author
note, and any other sections. Files that cannot be split safely stay on the
same writing page as one whole-chapter text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_H1 = re.compile(r"(?m)^[ \t]*#[ \t]+(.+?)\s*$")
_H2 = re.compile(r"(?m)^[ \t]*##[ \t]+(.+?)\s*$")
_FENCE = re.compile(r"(?m)^[ \t]*```")
_TABLE = re.compile(r"(?m)^[ \t]*\|.+\|[ \t]*$")
_OUTLINE_KEYS = ("本章目标", "核心冲突", "章节钩子")
_KNOWN = ("大纲", "剧情简写", "正文", "作者备注")
_OUTLINE_LABELS = {"本章目标": "goal", "核心冲突": "conflict", "章节钩子": "hook"}


@dataclass(frozen=True)
class ChapterDocument:
    raw: str
    title: str
    title_span: tuple[int, int] | None
    sections: dict[str, tuple[int, int]]
    outline_mode: str
    goal: str
    conflict: str
    hook: str
    outline_rest: str
    outline_text: str
    plot_brief: str
    body: str
    body_anchor: int
    author_note: str | None
    extras: tuple[tuple[str, str], ...] = ()

    def display_body(self) -> str:
        return self.body


@dataclass(frozen=True)
class ChapterEdits:
    title: str
    goal: str
    conflict: str
    hook: str
    outline_rest: str
    outline_text: str
    plot_brief: str
    body: str
    author_note: str | None
    extras: tuple[tuple[str, str], ...] = ()


def rejection_reason(raw: str) -> str:
    """Explain why a chapter is edited as one block. Empty means it can split."""
    text = str(raw or "")
    if _FENCE.search(text):
        return "这一章含有代码块，直接在下方修改整章文字。"
    if _TABLE.search(text):
        return "这一章含有表格，直接在下方修改整章文字。"
    h1s = list(_H1.finditer(text))
    h2s = list(_H2.finditer(text))
    if len(h1s) > 1:
        return "这一章的标题无法可靠拆开，直接在下方修改整章文字。"
    if h1s:
        prefix = text[:h1s[0].start()]
        if prefix.strip("\ufeff \t\r\n"):
            return "这一章的标题无法可靠拆开，直接在下方修改整章文字。"
        if h2s and h2s[0].start() < h1s[0].start():
            return "这一章的标题无法可靠拆开，直接在下方修改整章文字。"
        gap_end = h2s[0].start() if h2s else len(text)
        if h2s and text[_line_end(text, h1s[0].end()):gap_end].strip():
            return "这一章的标题无法可靠拆开，直接在下方修改整章文字。"
    names = [match.group(1).strip() for match in h2s]
    if len(names) != len(set(names)):
        return "这一章的标题无法可靠拆开，直接在下方修改整章文字。"
    return ""


def parse_chapter_document(raw: str) -> ChapterDocument:
    """Return the writing-page model. Unsafe files stay one whole-chapter text."""
    text = str(raw or "")
    if rejection_reason(text):
        return _verbatim_document(text)
    h1 = next(iter(_H1.finditer(text)), None)
    sections: dict[str, tuple[int, int]] = {}
    matches = list(_H2.finditer(text))
    for index, match in enumerate(matches):
        name = match.group(1).strip()
        start = _line_end(text, match.end())
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[name] = (start, end)
    title = h1.group(1).strip() if h1 else ""
    title_span = (h1.start(1), h1.end(1)) if h1 else None
    outline_mode, goal, conflict, hook, outline_rest, outline_text = _outline(text, sections.get("大纲"))
    plot = _display(text, sections.get("剧情简写"))
    if "正文" in sections:
        body_span = sections["正文"]
        body = _display(text, body_span)
        anchor = _anchor(text, body_span, body)
    elif not matches:
        loose_start = _line_end(text, h1.end()) if h1 else (1 if text.startswith("\ufeff") else 0)
        loose_end = len(text)
        body = text[loose_start:loose_end].strip("\r\n")
        anchor = loose_start + text[loose_start:loose_end].find(body) if body else loose_start
        sections["__loose__"] = (loose_start, loose_end)
    else:
        body = ""
        anchor = _insertion_point(text, sections, "正文")
    author = _display(text, sections["作者备注"]) if "作者备注" in sections else None
    extras = tuple(
        (name, _display(text, span))
        for name, span in sections.items()
        if name not in _KNOWN and name != "__loose__"
    )
    return ChapterDocument(
        raw=text,
        title=title,
        title_span=title_span,
        sections=sections,
        outline_mode=outline_mode,
        goal=goal,
        conflict=conflict,
        hook=hook,
        outline_rest=outline_rest,
        outline_text=outline_text,
        plot_brief=plot,
        body=body,
        body_anchor=anchor,
        author_note=author,
        extras=extras,
    )


def compose_chapter(document: ChapterDocument, edits: ChapterEdits) -> str:
    """Splice changed fields into the original text. Unchanged sections stay byte-for-byte."""
    raw = document.raw
    if document.outline_mode == "verbatim":
        return raw if edits.body == document.body else str(edits.body or "")
    newline = _newline(raw)
    replacements: list[tuple[int, int, str]] = []
    title = edits.title.replace("\n", " ").replace("\r", " ").strip()
    if title != document.title:
        if document.title_span is None:
            bom = 1 if raw.startswith("\ufeff") else 0
            block = f"# {title}{newline}{newline}"
            replacements.append((bom, bom, block))
        else:
            replacements.append((document.title_span[0], document.title_span[1], title))
    _replace_outline(document, edits, newline, replacements)
    _replace_named(document, "剧情简写", document.plot_brief, edits.plot_brief, newline, replacements, before="正文")
    _replace_body(document, edits.body, newline, replacements)
    if document.author_note is not None and edits.author_note is not None and edits.author_note != document.author_note:
        span = document.sections["作者备注"]
        replacements.append((span[0], span[1], _section_text(edits.author_note, newline, span[1] < len(raw))))
    edited_extras = dict(edits.extras)
    for name, current in document.extras:
        edited = edited_extras.get(name, current)
        if edited != current and name in document.sections:
            span = document.sections[name]
            replacements.append((span[0], span[1], _section_text(edited, newline, span[1] < len(raw))))
    if not replacements:
        return raw
    updated = raw
    for start, end, text in sorted(replacements, key=lambda item: item[0], reverse=True):
        updated = updated[:start] + text + updated[end:]
    return updated


def _verbatim_document(text: str) -> ChapterDocument:
    heading = next(iter(_H1.finditer(text)), None)
    title = ""
    if heading is not None and not text[:heading.start()].strip("\ufeff \t\r\n"):
        title = heading.group(1).strip()
    return ChapterDocument(
        raw=text,
        title=title,
        title_span=None,
        sections={},
        outline_mode="verbatim",
        goal="",
        conflict="",
        hook="",
        outline_rest="",
        outline_text="",
        plot_brief="",
        body=text,
        body_anchor=0,
        author_note=None,
        extras=(),
    )


def _outline(raw: str, span: tuple[int, int] | None):
    if span is None:
        return "absent", "", "", "", "", ""
    display = _display(raw, span)
    if not display:
        return "fields", "", "", "", "", ""
    lines = display.splitlines()
    values: dict[str, str] = {}
    index = 0
    for key in _OUTLINE_KEYS:
        if index >= len(lines):
            return "freeform", "", "", "", "", display
        match = re.fullmatch(rf"-\s*{key}\s*[:：]\s*(.*)", lines[index].strip())
        if match is None:
            return "freeform", "", "", "", "", display
        values[_OUTLINE_LABELS[key]] = match.group(1)
        index += 1
    rest = "\n".join(lines[index:]).strip("\r\n")
    return "fields", values["goal"], values["conflict"], values["hook"], rest, display


def _replace_outline(document, edits, newline, replacements) -> None:
    if document.outline_mode == "freeform":
        if edits.outline_text == document.outline_text:
            return
        span = document.sections["大纲"]
        replacements.append((span[0], span[1], _section_text(edits.outline_text, newline, span[1] < len(document.raw))))
        return
    same = (
        edits.goal == document.goal
        and edits.conflict == document.conflict
        and edits.hook == document.hook
        and edits.outline_rest == document.outline_rest
    )
    if same:
        return
    rendered = _render_outline(edits)
    if "大纲" in document.sections:
        span = document.sections["大纲"]
        replacements.append((span[0], span[1], _section_text(rendered, newline, span[1] < len(document.raw))))
        return
    if not rendered:
        return
    point = _insertion_point(document.raw, document.sections, "大纲")
    replacements.append((point, point, f"## 大纲{newline}{_section_text(rendered, newline, True)}"))


def _replace_named(document, name, current, edited, newline, replacements, *, before: str) -> None:
    if edited == current:
        return
    if name in document.sections:
        span = document.sections[name]
        replacements.append((span[0], span[1], _section_text(edited, newline, span[1] < len(document.raw))))
        return
    if not edited:
        return
    point = _insertion_point(document.raw, document.sections, name if name != "剧情简写" else before)
    if name == "剧情简写":
        point = _insertion_point(document.raw, document.sections, "正文")
    replacements.append((point, point, f"## {name}{newline}{_section_text(edited, newline, True)}"))


def _replace_body(document, edited, newline, replacements) -> None:
    if edited == document.body:
        return
    if "正文" in document.sections:
        span = document.sections["正文"]
        replacements.append((span[0], span[1], _section_text(edited, newline, span[1] < len(document.raw))))
        return
    loose = document.sections.get("__loose__")
    if loose is not None and not any(name in document.sections for name in _KNOWN):
        text = _section_text(edited, newline, False) if edited else ""
        replacements.append((loose[0], loose[1], text))
        return
    if not edited:
        return
    point = _insertion_point(document.raw, document.sections, "作者备注")
    replacements.append((point, point, f"## 正文{newline}{_section_text(edited, newline, point < len(document.raw))}"))


def _render_outline(edits: ChapterEdits) -> str:
    lines = [
        f"- 本章目标：{edits.goal}",
        f"- 核心冲突：{edits.conflict}",
        f"- 章节钩子：{edits.hook}",
    ]
    if edits.outline_rest.strip():
        lines.extend(edits.outline_rest.replace("\r\n", "\n").split("\n"))
    return "\n".join(lines)


def _section_text(text: str, newline: str, before_heading: bool) -> str:
    body = str(text or "").replace("\r\n", "\n").strip("\n")
    if not body:
        return newline if before_heading else ""
    return body.replace("\n", newline) + newline + (newline if before_heading else "")


def _insertion_point(raw: str, sections: dict[str, tuple[int, int]], name: str) -> int:
    order = ("大纲", "剧情简写", "正文", "作者备注")
    if name not in order:
        return len(raw)
    for later in order[order.index(name):]:
        if later in sections:
            heading = _heading_start(raw, sections[later][0])
            return heading
    loose = sections.get("__loose__")
    if loose is not None:
        return loose[0]
    return len(raw)


def _heading_start(raw: str, content_start: int) -> int:
    line_start = raw.rfind("\n", 0, content_start) + 1
    return line_start


def _display(raw: str, span: tuple[int, int] | None) -> str:
    if span is None:
        return ""
    return raw[span[0]:span[1]].strip("\r\n")


def _anchor(raw: str, span: tuple[int, int], display: str) -> int:
    if not display:
        return span[0]
    return span[0] + raw[span[0]:span[1]].find(display)


def _line_end(raw: str, index: int) -> int:
    if raw[index:index + 2] == "\r\n":
        return index + 2
    if index < len(raw) and raw[index] == "\n":
        return index + 1
    return index


def _newline(raw: str) -> str:
    return "\r\n" if "\r\n" in raw and raw.count("\n") == raw.count("\r\n") else "\n"
