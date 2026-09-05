import os
from unittest import SkipTest, TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.editor import Editor  # noqa: E402


class EditorContinuationTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        instance = QCoreApplication.instance()
        if instance is not None and not isinstance(instance, QApplication):
            raise SkipTest("已有 QCoreApplication，无法在同一进程升级为 QApplication")
        cls.app = instance or QApplication([])

    def test_continuation_is_inserted_before_custom_section_and_is_undoable(self) -> None:
        editor = Editor()
        original = (
            "# 第一章\n\n## 大纲\n规划\n\n## 正文\n原正文。\n\n"
            "## 作者备注\n保留备注。\n"
        )
        editor.text_edit.setPlainText(original)

        editor.append_chapter_body("续写第一段。\n\n续写第二段。")

        updated = editor.text_edit.toPlainText()
        self.assertLess(updated.index("续写第一段"), updated.index("## 作者备注"))
        self.assertIn("原正文。\n\n续写第一段。", updated)
        self.assertIn("## 作者备注\n保留备注。", updated)
        editor.text_edit.undo()
        self.assertEqual(editor.text_edit.toPlainText(), original)
