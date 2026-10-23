"""Stage two presentation and save feedback; fixtures never touch user projects."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QPlainTextEdit, QToolButton

from core.character_card_sync import MANAGED_STATE_END, MANAGED_STATE_START
from core.project import NovelProject
from core.project_data import CANON_ENTRY_TYPES
from tests.test_canon_schema import character_text
from ui.canon_sync_view import CanonSyncView, display_fields
from ui.structured_canon_page import StructuredCanonPage
from ui.theme import apply_theme


class CanonFeedbackTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        font = Path("C:/Windows/Fonts/msyh.ttc")
        cls.font_id = QFontDatabase.addApplicationFont(str(font)) if font.exists() else -1

    @classmethod
    def tearDownClass(cls):
        if cls.font_id >= 0:
            QFontDatabase.removeApplicationFont(cls.font_id)

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.project = NovelProject.create(Path(self.tmp.name) / "project", "资料反馈测试")
        self.project.save_chapter("chapter_02", title="雾港来信", outline="", content="来信。")
        self.path = self.project.canon_dir / "characters" / "白鸥.md"
        self.path.write_bytes(character_text().replace("\n", "\r\n").encode("utf-8"))
        apply_theme(self.app, {"theme": "light"})
        self.page = StructuredCanonPage()
        self.page.open_path(self.path, "character", self.project)
        self.page.resize(1100, 800)
        self.page.show()
        QTest.qWait(40)

    def tearDown(self):
        self.page.reset()
        self.page.close()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.tmp.cleanup()

    def view(self):
        return self.page.form.findChild(CanonSyncView)

    def name(self):
        return next(editor for _s, key, editor in self.page.form._field_widgets if key == "姓名")

    def test_sync_view_is_readonly_and_hides_control_markers(self):
        before = self.path.read_bytes()
        view = self.view()
        self.assertEqual(view.value_labels["as_of_chapter"].text(), "雾港来信")
        self.assertEqual(view.value_labels["location"].text(), "北港")
        self.assertEqual(view.value_labels["items"].text(), "钥匙")
        self.assertEqual(view.findChildren(QPlainTextEdit), [])
        self.assertEqual(view.findChildren(QLineEdit), [])
        self.assertIsNone(view.raw_button)
        self.assertFalse(self.page.is_modified())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.page.write_changes()[0])
        self.assertEqual(self.path.read_bytes(), before)

    def test_sync_and_chapter_requests_identify_the_active_document(self):
        sync, chapters = [], []
        self.page.sync_requested.connect(sync.append)
        self.page.chapter_requested.connect(chapters.append)
        self.view().sync_button.click()
        self.view().chapter_button.click()
        self.assertEqual(sync, [str(self.path)])
        self.assertEqual(chapters, ["chapter_02"])
        self.page._request_sync(str(self.path.with_name("过期资料.md")))
        self.page._request_chapter(str(self.path.with_name("过期资料.md")), "chapter_01")
        self.assertEqual(len(sync), 1)
        self.assertEqual(len(chapters), 1)

    def test_missing_chapter_is_explicit_and_not_clickable(self):
        (self.project.chapters_dir / "chapter_02.md").unlink()
        self.page.form.load_entry(self.path, "character", self.project)
        self.assertIn("章节不存在", self.view().value_labels["as_of_chapter"].text())
        self.assertIsNone(self.view().chapter_button)

    def test_chapter_reference_cannot_escape_project(self):
        text = self.path.read_text(encoding="utf-8").replace("chapter_02", "../secret")
        self.path.write_text(text, encoding="utf-8")
        with patch.object(self.project, "load_chapter", wraps=self.project.load_chapter) as load:
            self.page.form.load_entry(self.path, "character", self.project)
            load.assert_not_called()
        self.assertIsNone(self.view().chapter_button)

    def test_empty_default_state_is_not_treated_as_malformed(self):
        self.path.write_text(CANON_ENTRY_TYPES["character"]["template"].format(title="白鸥"), encoding="utf-8")
        self.page.form.load_entry(self.path, "character", self.project)
        self.assertIsNone(self.view().raw_button)
        self.assertEqual(self.view().value_labels["as_of_chapter"].text(), "未记录")
        self.assertIsNone(self.view().chapter_button)

    def test_ambiguous_content_is_never_silently_dropped(self):
        source = f"{MANAGED_STATE_START}\n- 当前状态：健康\n{MANAGED_STATE_END}"
        variants = (
            source.replace("健康", "健康\n- 未知字段：重要内容"),
            source.replace("健康", "健康\n- 当前状态：受伤"),
            source.replace(MANAGED_STATE_END, ""),
            source + "\n区块外手写内容。",
            source.replace("健康", "健康\n不是字段的手写内容。"),
            source + "\n" + source,
            source.replace("健康", "健康\n- 本次同步截止章节：chapter_01\n- 状态截止章节：chapter_02"),
        )
        for variant in variants:
            with self.subTest(source=variant):
                self.assertIsNone(display_fields(variant))
        self.assertEqual(display_fields(source)["state"], "健康")

    def test_fallback_raw_dialog_retains_every_character_and_is_readonly(self):
        text = self.path.read_text(encoding="utf-8").replace("- 当前状态：健康", "- 未知字段：原样保留")
        self.path.write_text(text, encoding="utf-8")
        self.page.form.load_entry(self.path, "character", self.project)
        view = self.view()
        self.assertIsNotNone(view.raw_button)
        observed = []

        class InspectDialog(QDialog):
            def exec(dialog):
                editor = dialog.findChild(QPlainTextEdit)
                observed.append((editor.toPlainText(), editor.isReadOnly()))
                return QDialog.DialogCode.Rejected

        with patch("ui.canon_sync_view.QDialog", InspectDialog):
            view.raw_button.click()
        self.assertIn("- 未知字段：原样保留", observed[0][0])
        self.assertIn(MANAGED_STATE_START, observed[0][0])
        self.assertTrue(observed[0][1])
        self.assertFalse(self.page.is_modified())
        self.assertEqual(self.path.read_text(encoding="utf-8"), text)

    def test_sync_disabled_state_survives_form_rebuild(self):
        self.page.set_sync_available(False, "请先处理待审结果")
        self.assertFalse(self.view().sync_button.isEnabled())
        self.page.form.load_entry(self.path, "character", self.project)
        self.assertFalse(self.view().sync_button.isEnabled())
        self.assertIn("待审", self.view().sync_button.toolTip())
        self.page.set_sync_available(True)
        self.assertTrue(self.view().sync_button.isEnabled())

    def test_saved_feedback_fades_and_new_edits_take_priority(self):
        self.page._saved_feedback_timer.setInterval(30)
        self.assertEqual(self.page.modified_badge.text(), "已保存")
        self.name().setText("白羽")
        self.assertEqual(self.page.modified_badge.text(), "待保存")
        self.assertIn("待保存", self.page.modified_badge.accessibleName())
        self.assertTrue(self.page.write_changes()[0])
        self.assertEqual(self.page.modified_badge.text(), "保存成功")
        QTest.qWait(60)
        self.assertEqual(self.page.modified_badge.text(), "已保存")
        self.name().setText("白鸥")
        self.page.configure_auto_save(True, 5)
        self.page._auto_save_flush()
        self.assertEqual(self.page.modified_badge.text(), "保存成功")
        self.name().setText("继续编辑")
        QTest.qWait(60)
        self.assertEqual(self.page.modified_badge.text(), "待保存")

    def test_save_error_survives_feedback_timeout_and_retains_draft(self):
        self.page._saved_feedback_timer.setInterval(30)
        self.name().setText("白羽")
        self.assertTrue(self.page.write_changes()[0])
        self.name().setText("保留草稿")
        before = self.path.read_bytes()
        with patch("ui.canon_form_widget.atomic_write_text", side_effect=OSError("只读磁盘")):
            self.assertFalse(self.page.write_changes()[0])
        QTest.qWait(60)
        self.assertEqual(self.page.modified_badge.text(), "保存失败")
        self.assertEqual(self.name().text(), "保留草稿")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.page.recovery_bar.isVisible())

    def test_external_conflict_remains_visible_after_success_timeout(self):
        self.page._saved_feedback_timer.setInterval(30)
        self.name().setText("白羽")
        self.page.write_changes()
        self.name().setText("本地草稿")
        external = self.path.read_bytes() + "\n外部修改".encode("utf-8")
        self.path.write_bytes(external)
        self.assertFalse(self.page.write_changes()[0])
        QTest.qWait(60)
        self.assertEqual(self.page.modified_badge.text(), "外部版本变化")
        self.assertEqual(self.name().text(), "本地草稿")
        self.assertEqual(self.path.read_bytes(), external)

    def test_switching_documents_does_not_inherit_success_feedback(self):
        self.name().setText("白羽")
        self.page.write_changes()
        another = self.path.with_name("另一份.md")
        another.write_text(character_text(), encoding="utf-8")
        self.page.open_path(another, "character", self.project)
        self.assertEqual(self.page.modified_badge.text(), "已保存")
        self.assertFalse(self.page._saved_feedback_timer.isActive())

    def test_collapsed_module_menu_remains_available_and_keeps_draft(self):
        form = self.page.form
        section = form.entry.section("外貌与辨识特征")
        editor = next(editor for s, editor in form._text_widgets if s is section)
        editor.setPlainText("未保存的外貌草稿")
        before = self.path.read_bytes()
        form._module_toggles[section.module_id].setChecked(False)
        button = next(b for b in form.findChildren(QToolButton)
                      if b.objectName() == "moduleMenuButton" and b.property("moduleId") == section.module_id)
        self.assertTrue(button.isVisible())
        action = next(a for a in button.menu().actions() if "重命名" in a.text())
        with patch("ui.canon_form_widget.QInputDialog.getText", return_value=("外观", True)):
            action.trigger()
        section = form.entry.section("外观")
        self.assertFalse(form._module_toggles[section.module_id].isChecked())
        self.assertIn("未保存的外貌草稿", form._effective_text())
        self.assertEqual(self.path.read_bytes(), before)

    def test_header_actions_stay_in_place_through_save_states(self):
        for theme in ("light", "dark"):
            apply_theme(self.app, {"theme": theme, "ui_font_size": 20})
            self.page.resize(520, 800)
            QTest.qWait(60)
            before = self.page.save_button.mapTo(self.page, self.page.save_button.rect().topLeft())
            self.name().setText("标题仍在")
            self.page.form._save_failed("保存异常")
            QTest.qWait(40)
            after = self.page.save_button.mapTo(self.page, self.page.save_button.rect().topLeft())
            self.assertEqual(before, after)
            self.assertEqual(self.page.width(), 520)
            self.assertEqual(self.page.form.form_scroll.horizontalScrollBar().maximum(), 0)
            self.page.form.save_error = ""
            self.page.write_changes()

    def test_wrapped_sync_heading_is_fully_visible_after_jump(self):
        apply_theme(self.app, {"theme": "dark", "ui_font_size": 20})
        self.page.resize(520, 800)
        QTest.qWait(60)
        form = self.page.form
        section = next(s for s in form.entry.sections if s.role == "managed")
        form.reveal_module(section.module_id)
        QTest.qWait(80)
        heading = form._module_toggles[section.module_id].parentWidget()
        top = heading.mapTo(form.form_scroll.viewport(), QPoint(0, 0)).y()
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(top + heading.height(), form.form_scroll.viewport().height())
