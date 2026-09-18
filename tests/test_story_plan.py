import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, SkipTest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication
from application.document_service import DocumentService, DocumentServiceError, DocumentRevisionConflict
from core.project import NovelProject
from core.story_plan import STORY_PLAN_PATH, empty_plan, parse_plan, serialize_plan
from core.prompt_builder import build_expansion_prompt, build_write_prompt, build_check_prompt
from core.task_context import AIContextSnapshot
from ui.editor import Editor
from ui.theme import apply_theme


class StoryPlanTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = NovelProject.create(Path(self.tmp.name) / "project", "规划测试")
        self.path = self.project.root / STORY_PLAN_PATH
        self.service = DocumentService()

    def test_new_project_only_creates_optional_blank_plan(self):
        self.assertEqual(parse_plan(self.path.read_text(encoding="utf-8")), empty_plan())
        for name in ("main_arc.md", "future_plan.md"):
            self.assertFalse((self.project.outline_dir / name).exists())
        self.assertEqual((self.project.canon_dir / "timeline.md").read_bytes(), b"")

    def test_ai_uses_only_enabled_plan_and_never_free_notes(self):
        data = empty_plan()
        data["direction"] = "独有的结局设想：守护海岛"
        (self.project.canon_dir / "timeline.md").write_text("自由笔记独有秘密", encoding="utf-8")
        for enabled in (False, True, False):
            data["enabled"] = enabled
            self.path.write_text(serialize_plan(data), encoding="utf-8")
            for builder in (build_expansion_prompt, build_write_prompt, build_check_prompt):
                with self.subTest(enabled=enabled, builder=builder.__name__):
                    bundle = builder(self.project, "chapter_01", 2000) if builder is build_expansion_prompt else builder(self.project, "chapter_01")
                    self.assertEqual(data["direction"] in bundle.user_prompt, enabled)
                    self.assertNotIn("自由笔记独有秘密", bundle.user_prompt)
                    self.assertNotIn("【后续剧情规划】", bundle.user_prompt)
                    self.assertIn("规划尚未实现不构成一致性错误", bundle.user_prompt)

    def test_budget_never_silently_truncates_enabled_direction(self):
        data = empty_plan()
        data.update(enabled=True, direction="后续方向" * 2000)
        self.path.write_text(serialize_plan(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "故事规划"):
            build_expansion_prompt(self.project, "chapter_01", 2000)

    def test_context_snapshot_tracks_plan_changes_but_not_notes(self):
        text = self.project.load_chapter("chapter_01").raw
        before = AIContextSnapshot.capture(self.project, "chapter_01", text)
        (self.project.canon_dir / "timeline.md").write_text("随手记录", encoding="utf-8")
        self.assertTrue(before.matches(self.project, "chapter_01", text))
        data = empty_plan()
        data.update(enabled=True, core="新的核心冲突")
        self.path.write_text(serialize_plan(data), encoding="utf-8")
        self.assertFalse(before.matches(self.project, "chapter_01", text))

    def test_invalid_or_externally_changed_plan_cannot_be_overwritten(self):
        opened = self.service.open_document(self.project, "故事规划", self.path)
        with self.assertRaises(DocumentServiceError):
            self.service.save_document(self.project, "故事规划", self.path, "{}", expected_revision=opened.revision)
        self.assertEqual(parse_plan(self.path.read_text(encoding="utf-8")), empty_plan())
        self.path.write_text("损坏的外部内容", encoding="utf-8")
        with self.assertRaises(DocumentRevisionConflict):
            self.service.save_document(self.project, "故事规划", self.path, opened.content, expected_revision=opened.revision)
        with self.assertRaises(DocumentServiceError):
            self.service.open_document(self.project, "故事规划", self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "损坏的外部内容")


class StoryPlanUITests(TestCase):
    @classmethod
    def setUpClass(cls):
        instance = QCoreApplication.instance()
        if instance and not isinstance(instance, QApplication):
            raise SkipTest("Requires QApplication")
        cls.app = instance or QApplication([])

    def test_form_save_reopen_switch_and_narrow_themes(self):
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "project", "测试")
            service = DocumentService()
            path = project.root / STORY_PLAN_PATH
            editor = Editor()
            self.addCleanup(editor.close)
            self.addCleanup(lambda: apply_theme(self.app, {"theme": "light"}))
            snapshot = service.open_document(project, "故事规划", path)
            editor.load_document(snapshot)
            self.assertFalse(editor.is_dirty())
            self.assertEqual(editor.title_label.text(), "故事规划")
            self.assertTrue(editor.source_button.isHidden())
            self.assertTrue(editor.preview_button.isHidden())
            editor.story_plan_form.fields["core"].setPlainText("家族秘密")
            editor.story_plan_form.enabled.setChecked(True)
            self.assertTrue(editor.is_dirty())
            saved = service.save_document(project, "故事规划", path, editor.document_text(), expected_revision=editor.loaded_revision())
            editor.mark_saved(saved.revision)
            self.assertFalse(editor.is_dirty())
            chapter = project.chapters_dir / "chapter_01.md"
            editor.load_document(service.open_document(project, "章节", chapter))
            self.assertFalse(editor.source_button.isHidden())
            editor.set_view_mode("preview")
            editor.load_document(service.open_document(project, "故事规划", path))
            self.assertIs(editor.editor_stack.currentWidget(), editor.story_plan_form)
            self.assertEqual(editor.story_plan_form.fields["core"].toPlainText(), "家族秘密")
            self.assertTrue(editor.story_plan_form.enabled.isChecked())
            editor.show_find()
            self.assertTrue(editor.find_bar.isHidden())
            for theme in ("light", "dark"):
                apply_theme(self.app, {"theme": theme})
                editor.resize(560, 680)
                editor.show()
                self.app.processEvents()
                self.assertEqual(editor.story_plan_form.horizontalScrollBar().maximum(), 0)
                for field in editor.story_plan_form.fields.values():
                    self.assertGreater(field.width(), 200)
                    self.assertTrue(field.accessibleName())
            editor.clear_document()
            self.assertIs(editor.editor_stack.currentWidget(), editor.text_edit)

    def test_window_navigation_saves_form_and_restores_last_document(self):
        from core.config import DEFAULT_CONFIG
        from ui.main_window import MainWindow
        with TemporaryDirectory() as tmp, patch.object(MainWindow, "_restore_last_project"):
            root = Path(tmp)
            project = NovelProject.create(root / "project", "导航测试")
            path = project.root / STORY_PLAN_PATH
            config = {**DEFAULT_CONFIG, "auto_save": False}
            window = MainWindow(config=config, ui_state_path=root / "ui.json")
            window.project_lifecycle_controller.persist_config = lambda _: None
            self.addCleanup(window.close)
            self.assertTrue(window._load_project(project.root, quiet=True))
            window.left_panel.select_path(path)
            self.app.processEvents()
            self.assertTrue(window.editor.is_story_plan())
            self.assertEqual(window.window_state_controller.current_route, "canon")
            self.assertFalse(window.actions["find"].isEnabled())
            self.assertFalse(window.actions["expand"].isEnabled())
            self.assertFalse(window.actions["delete_chapter"].isEnabled())
            window.editor.story_plan_form.fields["growth"].setPlainText("从孤立到合作")
            window.left_panel.select_path(project.chapters_dir / "chapter_01.md")
            self.assertEqual(parse_plan(path.read_text(encoding="utf-8"))["growth"], "从孤立到合作")
            self.assertTrue(window.actions["find"].isEnabled())
            window.left_panel.select_path(path)
            window.workspace_state.flush()
            restored = MainWindow(config=config, ui_state_path=root / "ui.json")
            restored.project_lifecycle_controller.persist_config = lambda _: None
            self.addCleanup(restored.close)
            self.assertTrue(restored._load_project(project.root, quiet=True))
            self.assertTrue(restored.editor.is_story_plan())
            self.assertEqual(restored.editor.story_plan_form.fields["growth"].toPlainText(), "从孤立到合作")
