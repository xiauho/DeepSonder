"""Opt-in UI runtime smoke check; all profiles and projects are temporary."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from PySide6.QtCore import QCoreApplication, QEvent

from core import config as config_module
from core.expansion import ExpansionRunResult
from core.project import NovelProject
from ui.main_window import MainWindow


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run_ui_self_test(app, report_path: Path):
    """Exercise real UI controllers without model calls or the normal profile."""
    checks = []
    with TemporaryDirectory(prefix="deepsonder-ui-self-test-") as temporary:
        root = Path(temporary)
        profile = root / "config.json"
        original_config_path = config_module.get_config_path
        config_module.get_config_path = lambda: profile
        window = None
        try:
            fresh = config_module.load_config()
            _require((fresh["editor_font_size"], fresh["editor_line_spacing"]) == (18, 1.8), "fresh typography")
            _require(not profile.exists(), "fresh load must not write a profile")
            checks.append("fresh_profile")
            profile.write_text(json.dumps({"config_schema_version": 6, "editor_font_size": 16, "auto_save": False}), encoding="utf-8")
            legacy = config_module.load_config()
            _require((legacy["editor_font_size"], legacy["editor_line_spacing"]) == (16, 1.0), "legacy typography")
            _require(config_module.load_config() == legacy, "idempotent migration")
            checks.append("legacy_profile")
            project = NovelProject.create(root / "book", "运行自检")
            project.save_chapter("chapter_01", "运行自检", "旧站的灯还亮着。\n\n" * 150, title="旧站")
            path = project.chapters_dir / "chapter_01.md"
            original = path.read_bytes()
            window = MainWindow(config=legacy, ui_state_path=root / "ui.json")
            window.resize(1100, 900)
            window.show()
            window.project_session.set_project(project)
            window._show_route("writing")
            window._on_file_selected("章节", str(path))
            app.processEvents()
            editor = window.editor
            edit = editor.writing_page.body_edit
            edit.restore_reading_position({"cursor": 80, "anchor": 20, "top_position": 300})
            state = edit.reading_position()
            window.document_controller.reload_current_file()
            app.processEvents()
            edit.restore_reading_position(state)
            _require(edit.reading_position() == state, "visible prose state restoration")
            checks.append("prose_position")
            candidate = "灯" * 300
            result = ExpansionRunResult(raw_output=f"<NOVEL_TEXT>{candidate}</NOVEL_TEXT>", first_raw_output=None,
                plain_text_fallback_count=0, target_chars=300, min_chars=240, max_chars=360,
                initial_char_count=300, final_char_count=300)
            token = window.ai_controller.start("expand", project, "chapter_01", editor.document_text(), lambda _cancel: result)
            deadline = monotonic() + 10
            while monotonic() < deadline:
                app.processEvents()
                if window.ai_workflow_controller.has_pending_result and not window.ai_controller.is_running():
                    break
                sleep(.005)
            _require(token is not None and window.ai_workflow_controller.has_pending_result, "AI review arrival")
            settings = window.settings_page
            settings.editor_font_size.setValue(24)
            settings.editor_line_spacing.setValue(2.0)
            settings.typography_preview_toggle.click()
            settings._emit_save()
            app.processEvents()
            _require((edit.font().pixelSize(), edit.line_spacing()) == (24, 2.0), "saved typography")
            _require(window.ai_workflow_controller._task_context_matches(token), "display settings preserve AI context")
            _require(candidate not in editor.document_text() and path.read_bytes() == original, "review must not auto-write")
            checks.extend(("settings_apply", "ai_explicit_review"))
            window.ai_workflow_controller.discard_pending_result()
            edit.insertPlainText("作者草稿。")
            draft = editor.document_text()
            external = original + "\n外部修改。".encode("utf-8")
            path.write_bytes(external)
            _require(not window.document_controller.auto_save(), "external change must reject save")
            _require(path.read_bytes() == external and editor.document_text() == draft and editor.is_dirty(), "conflict must preserve both versions")
            checks.append("save_conflict")
        finally:
            if window is not None:
                window.ai_workflow_controller.discard_pending_result()
                window.review_panel.finish()
                window.editor._set_dirty(False)
                window.settings_page.set_config(window.config)
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
            config_module.get_config_path = original_config_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"successful": True, "checks": checks,
        "temporary_data_only": True, "native_ime_tested": False}, indent=2), encoding="utf-8")
