import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import SkipTest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from core.project_data import CANON_ENTRY_TYPES
from tests.test_canon_index import FakeProject
from tests.test_canon_schema import character_text
from ui.main_window import MainWindow


class StubCanonPage:
    """记录调用并可控返回的结构化表单页桩（MainWindow 不感知差异）。"""

    def __init__(self, current=None, kind=None):
        self.opened = []
        self.result = True
        self.raised_error = None
        self.modified = False
        self._current = current
        self._kind = kind

    def open_path(self, path, kind, project=None):
        if self.raised_error is not None:
            raise self.raised_error
        self.opened.append((Path(path), kind, project))
        return self.result

    def is_modified(self):
        return self.modified

    def current_path(self):
        return self._current

    def current_kind(self):
        return self._kind


class StubContentStack:
    def __init__(self):
        self.current = None

    def setCurrentWidget(self, widget):
        self.current = widget


def make_self(project, running=False, pending=False, page=None):
    page = page or StubCanonPage()
    window = SimpleNamespace(
        project=project,
        ai_controller=SimpleNamespace(is_running=lambda: running),
        ai_workflow_controller=SimpleNamespace(has_pending_result=pending),
        _save_if_dirty=lambda: True,
        structured_canon_page=page,
        content_stack=StubContentStack(),
        window_state_controller=SimpleNamespace(current_route="writing"),
        editor=SimpleNamespace(current_path=lambda: None),
    )
    window._open_canon_page = lambda path_str, fallback=False: MainWindow._open_canon_page(
        window, path_str, fallback=fallback)
    window._reveal_canon_page = lambda: MainWindow._reveal_canon_page(window)
    return window


class OpenCanonPageRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = FakeProject(Path(self.tmp.name))
        self.character = self.project.canon_dir / "characters" / "白鸥.md"
        self.character.write_text(character_text(), encoding="utf-8")

    def test_returns_false_without_project(self):
        window = make_self(None)
        self.assertFalse(MainWindow._open_canon_page(window, str(self.character), fallback=True))
        self.assertEqual(window.structured_canon_page.opened, [])
        self.assertIsNone(window.content_stack.current)

    def test_returns_false_for_non_canon_path(self):
        window = make_self(self.project)
        chapter = self.project.root / "chapter_01.md"
        self.assertFalse(MainWindow._open_canon_page(window, str(chapter), fallback=True))
        self.assertEqual(window.structured_canon_page.opened, [])

    def test_returns_false_for_canon_kind_outside_project(self):
        window = make_self(self.project)
        outside = Path(self.tmp.name) / "elsewhere" / "characters" / "白鸥.md"
        self.assertFalse(MainWindow._open_canon_page(window, str(outside), fallback=True))
        self.assertEqual(window.structured_canon_page.opened, [])

    def test_returns_false_while_ai_running_with_fallback(self):
        window = make_self(self.project, running=True)
        self.assertFalse(MainWindow._open_canon_page(window, str(self.character), fallback=True))
        self.assertEqual(window.structured_canon_page.opened, [])
        self.assertIsNone(window.content_stack.current)

    def test_blocks_with_message_while_ai_running_without_fallback(self):
        window = make_self(self.project, running=True)
        with patch("ui.main_window.QMessageBox") as message_box:
            handled = MainWindow._open_canon_page(window, str(self.character))
        self.assertTrue(handled)
        message_box.information.assert_called_once()
        self.assertEqual(window.structured_canon_page.opened, [])
        self.assertIsNone(window.content_stack.current)

    def test_returns_true_when_user_cancels_save(self):
        window = make_self(self.project)
        window._save_if_dirty = lambda: False
        handled = MainWindow._open_canon_page(window, str(self.character))
        self.assertTrue(handled)
        self.assertEqual(window.structured_canon_page.opened, [])
        self.assertIsNone(window.content_stack.current)

    def test_opens_embedded_page_for_canon_path(self):
        window = make_self(self.project)
        handled = MainWindow._open_canon_page(window, str(self.character), fallback=True)
        self.assertTrue(handled)
        path, kind, project = window.structured_canon_page.opened[0]
        self.assertEqual(path, self.character)
        self.assertEqual(kind, "character")
        self.assertIs(project, self.project)
        self.assertIs(window.content_stack.current, window.structured_canon_page)

    def test_same_path_reveal_does_not_reload_or_prompt(self):
        page = StubCanonPage(current=self.character, kind="character")
        page.modified = True
        window = make_self(self.project, page=page)
        with patch("ui.canon_form_widget.QMessageBox") as message_box:
            handled = MainWindow._open_canon_page(window, str(self.character), fallback=True)
        self.assertTrue(handled)
        message_box.assert_not_called()  # 同材料重复打开不触发未保存三选一
        self.assertEqual(page.opened, [])  # 也不重新载入
        self.assertIs(window.content_stack.current, page)  # 仅确保表单页成为当前视图

    def test_open_path_cancel_keeps_current_view(self):
        window = make_self(self.project)
        window.structured_canon_page.result = False
        handled = MainWindow._open_canon_page(window, str(self.character), fallback=True)
        self.assertTrue(handled)
        self.assertEqual(len(window.structured_canon_page.opened), 1)
        self.assertIsNone(window.content_stack.current)

    def test_load_failure_shows_warning_and_stays(self):
        window = make_self(self.project)
        window.structured_canon_page.raised_error = ValueError("解析失败")
        with patch("ui.main_window.QMessageBox") as message_box:
            handled = MainWindow._open_canon_page(window, str(self.character), fallback=True)
        self.assertTrue(handled)
        message_box.warning.assert_called_once()
        self.assertIsNone(window.content_stack.current)

    def test_edit_canon_structured_falls_back_to_first_character(self):
        window = make_self(self.project)
        MainWindow.edit_canon_structured(window)
        path, kind, project = window.structured_canon_page.opened[0]
        self.assertEqual(path, self.character)
        self.assertEqual(kind, "character")
        self.assertIs(project, self.project)

    def test_edit_canon_structured_warns_while_ai_busy(self):
        window = make_self(self.project, running=True)
        with patch("ui.main_window.QMessageBox") as message_box:
            MainWindow.edit_canon_structured(window)
        message_box.information.assert_called_once()
        self.assertEqual(window.structured_canon_page.opened, [])


class RealWindowCanonRoutingTests(unittest.TestCase):
    """端到端：真实 MainWindow 中点击设定材料在主编辑区内嵌打开表单页。"""

    @classmethod
    def setUpClass(cls):
        instance = QCoreApplication.instance()
        if instance and not isinstance(instance, QApplication):
            raise SkipTest("Requires QApplication")
        cls.app = instance or QApplication([])

    def _patch_page_confirm(self, choice):
        patcher = patch("ui.canon_form_widget.QMessageBox")
        message_box = patcher.start()
        self.addCleanup(patcher.stop)
        buttons = {}

        def fake_add(text, *args, **kwargs):
            button = MagicMock()
            buttons[text] = button
            return button

        box = message_box.return_value
        box.addButton.side_effect = fake_add
        box.clickedButton.side_effect = lambda: buttons[choice]
        return buttons

    def test_left_panel_canon_click_opens_embedded_page(self):
        from core.config import DEFAULT_CONFIG
        from core.project import NovelProject
        with TemporaryDirectory() as tmp, patch.object(MainWindow, "_restore_last_project"):
            root = Path(tmp)
            project = NovelProject.create(root / "project", "路由测试")
            character = project.canon_dir / "characters" / "白鸥.md"
            character.write_text(character_text(), encoding="utf-8")
            other = project.canon_dir / "world" / "北港.md"
            other.write_text(
                str(CANON_ENTRY_TYPES["world"]["template"]).format(title="北港"),
                encoding="utf-8")
            timeline = project.canon_dir / "timeline.md"
            if not timeline.exists():
                timeline.write_text("# 时间线\n\n- 序章。\n", encoding="utf-8")
            config = {**DEFAULT_CONFIG, "auto_save": False}
            window = MainWindow(config=config, ui_state_path=root / "ui.json")
            window.project_lifecycle_controller.persist_config = lambda _: None
            self.addCleanup(window.close)
            self.assertTrue(window._load_project(project.root, quiet=True))
            window.show()
            self.app.processEvents()

            # 点击角色：结构化页内嵌于主编辑区，替换 md 编辑器成为当前内容视图
            window.left_panel.select_path(character)
            self.app.processEvents()
            page = window.structured_canon_page
            self.assertIs(window.content_stack.currentWidget(), page)
            self.assertTrue(page.isVisible())
            self.assertFalse(window.editor.isVisible())
            self.assertEqual(page.current_path(), character)
            self.assertEqual(page.current_kind(), "character")
            # 结构化页接管本次打开：通用 md 编辑器不载入该材料
            self.assertNotEqual(window.editor.current_path(), str(character))

            # 连续点击另一份设定材料：复用同一页面切换条目
            window.left_panel.select_path(other)
            self.app.processEvents()
            self.assertIs(window.content_stack.currentWidget(), page)
            self.assertEqual(page.current_path(), other)
            self.assertEqual(page.current_kind(), "world")

            # 回到角色页修改并用全局保存动作写盘；数据变更经 kind=canon 通知
            window.left_panel.select_path(character)
            self.app.processEvents()
            self.assertEqual(page.current_path(), character)
            name_field = next(e for s, k, e in page.form._field_widgets if k == "姓名")
            name_field.setText("白羽")
            self.assertTrue(page.is_modified())
            notified = []
            window.project_session.data_change_detail.connect(
                lambda _project, change: notified.append(change))
            self.assertTrue(window.save_current_file())
            self.assertIn("- 姓名：白羽", character.read_text(encoding="utf-8"))
            self.assertFalse(page.is_modified())
            self.assertEqual(len(notified), 1)

            # 重复点击当前材料：仅重新展示表单页，不触发未保存三选一、不重载
            name_field.setText("白羽改")
            with patch("ui.canon_form_widget.QMessageBox") as message_box:
                window.left_panel.select_path(character)
                self.app.processEvents()
            message_box.assert_not_called()
            self.assertTrue(page.is_modified())
            self.assertIs(window.content_stack.currentWidget(), page)
            self.assertEqual(page.current_path(), character)

            # 离开守卫：未保存修改时点击非结构化文档（时间线）提供三选一
            before_editor_path = window.editor.current_path()
            name_field.setText("白羽改")
            self._patch_page_confirm("取消")
            window._on_file_selected("时间线", str(timeline))
            self.assertTrue(page.is_modified())
            self.assertIs(window.content_stack.currentWidget(), page)
            self.assertEqual(window.editor.current_path(), before_editor_path)
            self.assertIn("- 姓名：白羽", character.read_text(encoding="utf-8"))

            self._patch_page_confirm("放弃修改")
            window._on_file_selected("时间线", str(timeline))
            self.assertFalse(page.is_modified())
            self.assertEqual(Path(window.editor.current_path()), timeline)
            self.assertIs(window.content_stack.currentWidget(), window.editor)


if __name__ == "__main__":
    unittest.main()
