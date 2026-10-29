"""Round-trip rules for the chapter writing page."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import SkipTest, TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from application.document_service import DocumentService  # noqa: E402
from core.chapter_document import (  # noqa: E402
    ChapterEdits,
    compose_chapter,
    parse_chapter_document,
    rejection_reason,
)
from core.project import NovelProject, default_chapter_markdown  # noqa: E402
from ui.editor import Editor  # noqa: E402


def _edits(document, **changes) -> ChapterEdits:
    values = {
        "title": document.title,
        "goal": document.goal,
        "conflict": document.conflict,
        "hook": document.hook,
        "outline_rest": document.outline_rest,
        "outline_text": document.outline_text,
        "plot_brief": document.plot_brief,
        "body": document.body,
        "author_note": document.author_note,
    }
    values.update(changes)
    return ChapterEdits(**values)


class ChapterDocumentTests(TestCase):
    def test_unchanged_fields_keep_the_original_bytes(self) -> None:
        raw = default_chapter_markdown("第一章 初始")
        document = parse_chapter_document(raw)
        self.assertIsNotNone(document)
        self.assertEqual(compose_chapter(document, _edits(document)), raw)
        self.assertEqual(rejection_reason(raw), "")

    def test_body_edit_preserves_outline_and_author_note_bytes(self) -> None:
        raw = (
            "# 第一章\n\n## 大纲\n- 本章目标：赶到山门\n- 核心冲突：断剑\n- 章节钩子：巨响\n\n"
            "## 剧情简写\n林夜醒来。\n\n## 正文\n旧正文。\n\n## 作者备注\n保留备注。\n"
        )
        document = parse_chapter_document(raw)
        updated = compose_chapter(document, _edits(document, body="新正文。"))
        self.assertIn("## 大纲\n- 本章目标：赶到山门\n- 核心冲突：断剑\n- 章节钩子：巨响\n\n", updated)
        self.assertIn("## 剧情简写\n林夜醒来。\n\n", updated)
        self.assertIn("## 作者备注\n保留备注。\n", updated)
        self.assertIn("新正文。", updated)
        self.assertNotIn("旧正文。", updated)

    def test_one_outline_field_preserves_body_bytes(self) -> None:
        raw = "# 第一章\r\n\r\n## 大纲\r\n- 本章目标：\r\n- 核心冲突：\r\n- 章节钩子：\r\n\r\n## 正文\r\n旧正文。\r\n"
        document = parse_chapter_document(raw)
        updated = compose_chapter(document, _edits(document, goal="离开后山"))
        self.assertIn("## 正文\r\n旧正文。\r\n", updated)
        self.assertIn("- 本章目标：离开后山", updated)
        self.assertNotIn("\n", updated.replace("\r\n", ""))

    def test_code_fence_and_table_stay_one_block_on_the_writing_page(self) -> None:
        fenced = "# 章\n\n## 正文\n```\n代码\n```\n"
        table = "# 章\n\n## 正文\n| 甲 | 乙 |\n"
        self.assertIn("代码块", rejection_reason(fenced))
        self.assertIn("表格", rejection_reason(table))
        self.assertEqual(parse_chapter_document(fenced).outline_mode, "verbatim")
        self.assertEqual(parse_chapter_document(fenced).body, fenced)

    def test_other_section_stays_on_the_writing_page(self) -> None:
        raw = "# 章\n\n## 伏笔\n以后再说。\n\n## 正文\n他跑了。\n"
        document = parse_chapter_document(raw)
        self.assertEqual(document.extras, (("伏笔", "以后再说。"),))
        updated = compose_chapter(document, _edits(document, body="他停了。"))
        self.assertIn("## 伏笔\n以后再说。\n\n", updated)
        self.assertIn("他停了。", updated)

    def test_freeform_outline_is_kept_as_one_block(self) -> None:
        raw = "# 章\n\n## 大纲\n先写追逐，再写停下来。\n\n## 正文\n他跑了。\n"
        document = parse_chapter_document(raw)
        self.assertEqual(document.outline_mode, "freeform")
        updated = compose_chapter(document, _edits(document, body="他停了。"))
        self.assertIn("## 大纲\n先写追逐，再写停下来。\n\n", updated)


class ChapterWritingPageTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        instance = QCoreApplication.instance()
        if instance is not None and not isinstance(instance, QApplication):
            raise SkipTest("已有 QCoreApplication，无法在同一进程升级为 QApplication")
        cls.app = instance or QApplication([])

    def test_empty_chapter_expands_preparation_and_hides_markdown_controls(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "book", "测试")
            editor = Editor()
            self.addCleanup(editor.close)
            editor.load_document(DocumentService().open_document(
                project, "章节", project.chapters_dir / "chapter_01.md"))
            self.assertTrue(editor.is_writing_page_visible())
            self.assertFalse(editor.writing_page.prep.isHidden())
            self.assertIn("也可以直接写正文", editor.writing_page.guide.text())
            self.assertTrue(editor.source_button.isHidden())
            self.assertTrue(editor.format_label.isHidden())
            self.assertEqual(editor.title_edit.text(), "第一章 初始")
            self.assertFalse(editor.is_dirty())
            self.assertEqual(editor.document_text(), (project.chapters_dir / "chapter_01.md").read_text(encoding="utf-8"))

    def test_existing_prose_collapses_preparation_until_opened(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "book", "测试")
            chapter = project.chapters_dir / "chapter_01.md"
            original = chapter.read_text(encoding="utf-8").replace("## 正文\n\n", "## 正文\n林夜是被冷醒的。\n\n")
            chapter.write_text(original, encoding="utf-8")
            editor = Editor()
            self.addCleanup(editor.close)
            editor.load_document(DocumentService().open_document(project, "章节", chapter))
            self.assertTrue(editor.writing_page.prep.isHidden())
            self.assertIn("林夜是被冷醒的", editor.writing_page.body_edit.toPlainText())
            self.assertNotIn("##", editor.writing_page.body_edit.toPlainText())
            editor.writing_page.prep_toggle.click()
            self.assertFalse(editor.writing_page.prep.isHidden())
            editor.writing_page.body_edit.setPlainText("林夜是被冷醒的。\n\n他坐了起来。")
            saved = editor.document_text()
            self.assertIn("他坐了起来。", saved)
            self.assertIn("- 本章目标：", saved)
            self.assertNotIn("## 正文", editor.writing_page.body_edit.toPlainText())
            self.assertFalse(hasattr(editor, "view_source_action"))
            self.assertTrue(editor.source_button.isHidden())

    def test_unrecognized_chapter_explains_why_it_stays_in_source(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "book", "测试")
            chapter = project.chapters_dir / "chapter_01.md"
            chapter.write_text("# 章\n\n## 正文\n```\n不要拆开\n```\n", encoding="utf-8")
            editor = Editor()
            self.addCleanup(editor.close)
            editor.load_document(DocumentService().open_document(project, "章节", chapter))
            self.assertTrue(editor.is_writing_page_visible())
            self.assertIn("代码块", editor.writing_page.notice.text())
            self.assertIn("```", editor.writing_page.body_edit.toPlainText())
            self.assertTrue(editor.writing_page.prep_bar.isHidden())
            self.assertTrue(editor.source_button.isHidden())
            self.assertTrue(editor.preview_button.isHidden())

    def test_continuation_updates_the_visible_prose(self) -> None:
        editor = Editor()
        self.addCleanup(editor.close)
        editor._current_category = "章节"
        editor.text_edit.setPlainText("# 章\n\n## 正文\n原正文。\n\n## 作者备注\n保留。\n")
        editor._refresh_open_chapter()
        editor._enter_writing_page(editor._chapter_doc, reset_prep=True)
        editor.append_chapter_body("续写。")
        visible = editor.writing_page.body_edit.toPlainText()
        self.assertIn("原正文。", visible)
        self.assertIn("续写。", visible)
        self.assertNotIn("作者备注", visible)
        self.assertLess(editor.text_edit.toPlainText().index("续写。"), editor.text_edit.toPlainText().index("## 作者备注"))

    def test_selecting_prose_uses_the_body_and_not_the_outline(self) -> None:
        from PySide6.QtGui import QTextCursor

        editor = Editor()
        self.addCleanup(editor.close)
        editor._current_category = "章节"
        editor.text_edit.setPlainText(
            "# 章\n\n## 大纲\n- 本章目标：\n- 核心冲突：\n- 章节钩子：\n\n## 正文\n林夜醒来。\n"
        )
        editor._refresh_open_chapter()
        editor._enter_writing_page(editor._chapter_doc, reset_prep=False)
        body = editor.writing_page.body_edit
        cursor = body.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(2, QTextCursor.MoveMode.KeepAnchor)
        body.setTextCursor(cursor)
        source, start, end = editor.source_selection()
        self.assertEqual(source[start:end], "林夜")
        self.assertNotIn("大纲", source[start:end])
