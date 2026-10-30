"""Measure synchronous typing through the complete writing window on temporary data."""

import argparse
import cProfile
import json
import os
from pathlib import Path
import platform
import statistics
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import QApplication

from core.config import DEFAULT_CONFIG
from core.project import NovelProject
from ui.main_window import MainWindow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", type=Path)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    font = Path("C:/Windows/Fonts/msyh.ttc")
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    profiler = cProfile.Profile() if args.profile else None
    rows = []
    with TemporaryDirectory(prefix="deepsonder-benchmark-") as temporary:
        root = Path(temporary)
        with patch("core.config.get_config_path", return_value=root / "config.json"), patch.object(MainWindow, "_restore_last_project"):
            project = NovelProject.create(root / "book", "性能验证")
            window = MainWindow(config={**DEFAULT_CONFIG, "auto_save": False}, ui_state_path=root / "ui.json")
            try:
                window.resize(1440, 900)
                window.show()
                window.project_session.set_project(project)
                window._show_route("writing")
                for count in (3000, 30000, 200000):
                    for shape in ("paragraphs", "single_paragraph"):
                        unit = "旧站的灯还亮着。林舟推开门，潮湿的风跟着他走进大厅。"
                        if shape == "paragraphs":
                            unit = unit * 5 + "\n\n"
                        text = (unit * (count // len(unit) + 1))[:count]
                        project.save_chapter("chapter_01", "性能验证", text, title="旧站")
                        path = project.chapters_dir / "chapter_01.md"
                        original = path.read_bytes()
                        window._on_file_selected("章节", str(path))
                        window.document_controller.reload_current_file()
                        edit = window.editor.writing_page.body_edit
                        cursor = edit.textCursor()
                        cursor.setPosition(count // 2)
                        edit.setTextCursor(cursor)
                        edit.ensureCursorVisible()
                        app.processEvents()
                        revision = window.editor.loaded_revision()
                        source_before = window.editor.document_text()
                        timings = []
                        for sample in range(10):
                            if profiler and count == 200000 and shape == "single_paragraph":
                                profiler.enable()
                            start = perf_counter()
                            edit.insertPlainText("灯")
                            app.processEvents()
                            elapsed = (perf_counter() - start) * 1000
                            if profiler:
                                profiler.disable()
                            if sample:  # Exclude one warm-up edit.
                                timings.append(elapsed)
                            assert window.editor.document_text() != source_before
                            edit.undo()
                            app.processEvents()
                            assert window.editor.document_text() == source_before
                        assert edit.toPlainText() == text
                        assert path.read_bytes() == original
                        assert window.editor.loaded_revision() == revision
                        rows.append({"characters": count, "shape": shape, "samples_ms": timings,
                                     "median_ms": statistics.median(timings), "max_ms": max(timings)})
                        window.editor._set_dirty(False)
            finally:
                window.editor._set_dirty(False)
                window.settings_page.set_config(window.config)
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
    report = {"python": platform.python_version(), "pyside": pyside_version,
              "platform": platform.platform(), "qt_platform": os.environ["QT_QPA_PLATFORM"],
              "window": [1440, 900], "font_px": 18, "spacing": 1.8,
              "scope": "insertPlainText + synchronous application signals + processEvents; no physical IME or display latency",
              "cases": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if profiler:
        profiler.dump_stats(str(args.profile))
    print(json.dumps(rows, ensure_ascii=False))


if __name__ == "__main__":
    main()
