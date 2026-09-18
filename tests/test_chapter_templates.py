from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from application.document_service import DocumentService
from application.project_service import ProjectService
from core.project import NovelProject


class ChapterTemplateTests(TestCase):
    def test_initial_and_added_chapters_share_default_content(self) -> None:
        with TemporaryDirectory() as tmp:
            opened = ProjectService().create_project(tmp, "测试")
            project = opened.project
            first_path = project.chapters_dir / "chapter_01.md"
            first = first_path.read_text(encoding="utf-8")
            DocumentService().create_chapter(
                project, opened.data_store, "第二章", "chapter_02"
            )
            second = (project.chapters_dir / "chapter_02.md").read_text(encoding="utf-8")

            self.assertEqual(first.split("\n", 1)[0], "# 第一章 初始")
            self.assertEqual(second.split("\n", 1)[0], "# 第二章")
            self.assertEqual(first.split("\n", 1)[1], second.split("\n", 1)[1])
            self.assertEqual(
                first.split("\n", 1)[1],
                "\n## 大纲\n- 本章目标：\n- 核心冲突：\n- 章节钩子：\n\n"
                "## 剧情简写\n\n\n## 正文\n\n",
            )
            chapter = project.load_chapter("chapter_01")
            self.assertEqual(chapter.plot_brief, "")
            self.assertEqual(chapter.content, "")

    def test_opening_project_preserves_existing_chapter(self) -> None:
        with TemporaryDirectory() as tmp:
            opened = ProjectService().create_project(tmp, "测试")
            path = opened.project.chapters_dir / "chapter_01.md"
            original = "# 第一章 初始\n\n## 大纲\n旧大纲\n\n## 正文\n已有正文。\n"
            path.write_text(original, encoding="utf-8")
            before = path.read_bytes()

            ProjectService().open_project(opened.project.root)

            self.assertEqual(path.read_bytes(), before)

    def test_initialization_preserves_existing_first_chapter(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            path = root / "outline" / "chapters" / "chapter_01.md"
            path.parent.mkdir(parents=True)
            original = "# 自定义首章\n\n已有正文。\n".encode("utf-8")
            path.write_bytes(original)

            NovelProject.create(root, "测试")

            self.assertEqual(path.read_bytes(), original)
