"""Render the production spacing editor on a temporary chapter, never a user project.

Example (set QT_SCALE_FACTOR before launching for each scale):
    python scripts/validate_prose_spacing.py --output output/ui-implementation-spacing
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import QApplication

from application.document_service import DocumentService
from core.config import DEFAULT_CONFIG
from core.project import NovelProject
from core.task_context import AIContextSnapshot
from core.theme_tokens import DARK_COLORS, LIGHT_COLORS
from ui.prose_edit import ProseTextEdit
from ui.editor import Editor
from ui.theme import apply_theme


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    font = Path("C:/Windows/Fonts/msyh.ttc")
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    scale = os.environ.get("QT_SCALE_FACTOR", "1")
    cases = []
    timings = []
    with TemporaryDirectory(prefix="deepsonder-spacing-") as temporary:
        project = NovelProject.create(Path(temporary) / "book", "排版验证")
        prose = (
            "旧站的灯还亮着。林舟推开门，潮湿的风跟着他走进大厅。\n\n"
            "他把那封信放在窗边。远处传来列车的鸣笛，像是有人终于回答了多年前的问题。"
            "钟表停在七点一刻，桌上的茶却仍然温热。\n\n"
            + "沿着铁轨向北走，就能看见山后的灯塔。" * 16 + "\n\n"
        ) * 30
        project.save_chapter("chapter_01", "找到信中的旧站", prose, title="第一章 旧站的灯")
        path = project.chapters_dir / "chapter_01.md"
        original = path.read_bytes()
        snapshot = DocumentService().open_document(project, "章节", path)
        editor = Editor()
        try:
            # Match the real shell's opaque appRoot surface when capturing an
            # Editor alone; otherwise its transparent top level grabs black.
            editor.setObjectName("appRoot")
            editor.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            editor.show()
            editor.load_document(snapshot)
            body = editor.writing_page.body_edit
            context = AIContextSnapshot.capture(project, "chapter_01", editor.document_text(), task_kind="expand")
            for theme, colors in (("light", LIGHT_COLORS), ("dark", DARK_COLORS)):
                for width in (1100, 1440, 1600):
                    for pixels in (18, 24):
                        apply_theme(app, {**DEFAULT_CONFIG, **colors, "theme": theme, "editor_font_size": pixels})
                        editor.resize(width, 860)
                        body.moveCursor(QTextCursor.MoveOperation.Start)
                        body.set_line_spacing(1.8)
                        app.processEvents()
                        assert editor.document_text() == snapshot.content
                        assert not editor.is_dirty()
                        assert body.document().availableUndoSteps() == 0
                        assert editor.loaded_revision() == snapshot.revision
                        assert context.matches(project, "chapter_01", editor.document_text())
                        assert body.horizontalScrollBar().maximum() == 0
                        block = body.document().firstBlock()
                        body.blockBoundingRect(block)
                        cursor = QTextCursor(block)
                        cursor.setPosition(4)
                        assert body.cursorForPosition(body.cursorRect(cursor).center()).position() == 4
                        name = f"spacing-{theme}-{width}-font{pixels}-scale{scale}.png"
                        capture = editor.grab()
                        background = capture.toImage().pixelColor(2, 2).name()
                        assert background == colors["background_color"].lower(), (theme, background)
                        assert capture.save(str(output / name))
                        cases.append({"theme": theme, "width": width, "font_px": pixels,
                                      "factor": 1.8, "dpr": editor.devicePixelRatioF(), "image": name,
                                      "rendered_background": background})
            # Comparison captures: same font, source, viewport and theme.
            apply_theme(app, {**DEFAULT_CONFIG, **LIGHT_COLORS, "theme": "light", "editor_font_size": 18})
            editor.resize(1100, 860)
            for factor in (1.0, 1.8):
                body.set_line_spacing(factor)
                body.moveCursor(QTextCursor.MoveOperation.Start)
                app.processEvents()
                assert editor.grab().save(str(output / f"compare-{factor}-scale{scale}.png"))
            assert path.read_bytes() == original
        finally:
            editor.close()
            editor.deleteLater()
        # Record actual local costs; no timing threshold tied to a machine.
        probe = ProseTextEdit()
        probe.resize(760, 600)
        probe.show()
        try:
            for shape, source in (("long_paragraph", "长段落文字 mixed text。" * 12000),
                                  ("many_paragraphs", ("短段落文字。\n\n") * 12000)):
                probe.setPlainText(source)
                app.processEvents()
                started = perf_counter()
                probe.set_line_spacing(1.8)
                app.processEvents()
                elapsed = (perf_counter() - started) * 1000
                assert probe.toPlainText() == source
                assert not probe.document().isModified()
                timings.append({"shape": shape, "chars": len(source), "change_ms": round(elapsed, 2)})
                probe.set_line_spacing(1.0)
        finally:
            probe.close()
            probe.deleteLater()
    report = {"pyside": pyside_version, "scale": scale, "cases": cases, "timings": timings,
              "source_unchanged": True, "production_integration": True,
              "ime_scope": "synthetic QInputMethodEvent tests; native candidate window not verified"}
    (output / f"layout-scale{scale}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"cases": len(cases), "scale": scale, "timings": timings}, ensure_ascii=False))


if __name__ == "__main__":
    main()
