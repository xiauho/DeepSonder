"""Shared manuscript rendering for export previews and final files."""

from __future__ import annotations

from .project import NovelProject


def strip_markdown(text: str) -> str:
    """Create a reading copy while omitting chapter planning sections."""
    lines: list[str] = []
    in_outline = False
    for line in str(text or "").splitlines():
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


def render_manuscript(project: NovelProject, options: dict) -> str:
    """Render the selected chapters using the same rules for preview/export."""
    selected_ids = {str(item) for item in options.get("chapter_ids", [])}
    chapters = [
        path for path in project.list_chapters() if path.stem in selected_ids
    ]
    if not chapters:
        return ""

    plain_text = options.get("format") == "txt"
    remove_markdown = plain_text or bool(options.get("strip"))
    blocks: list[str] = []
    if options.get("include_title"):
        blocks.append(project.name if plain_text else f"# {project.name}")
    if options.get("include_toc"):
        toc_title = "目录" if plain_text else "## 目录"
        toc_lines = [
            f"{index}. {project.load_chapter(path.stem).title}"
            for index, path in enumerate(chapters, 1)
        ]
        blocks.append(toc_title + "\n\n" + "\n".join(toc_lines))

    for path in chapters:
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        blocks.append(strip_markdown(raw).strip() if remove_markdown else raw.strip())

    separator = "\n\n***\n\n" if options.get("separators") else "\n\n\n"
    return separator.join(blocks).strip()
