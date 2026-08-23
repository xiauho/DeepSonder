import tempfile
from pathlib import Path
from unittest import TestCase

from core.export import render_manuscript, strip_markdown
from core.project import NovelProject


class ExportTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project = NovelProject.create(Path(self.temp_dir.name) / "novel", "测试作品")
        chapters = self.project.chapters_dir
        (chapters / "chapter_2.md").write_text(
            "# 第二章\n\n## 大纲\n规划\n\n## 正文\n第二章正文。\n",
            encoding="utf-8",
        )
        (chapters / "chapter_10.md").write_text(
            "# 第十章\n\n## 大纲\n规划\n\n## 正文\n第十章正文。\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_chapters_use_natural_order(self) -> None:
        self.assertEqual(
            [path.stem for path in self.project.list_chapters()],
            ["chapter_01", "chapter_2", "chapter_10"],
        )

    def test_plain_text_export_omits_planning_sections(self) -> None:
        raw = "# 第一章\n\n## 大纲\n规划\n\n## 剧情简写\n简写\n\n## 正文\n正文。\n"
        self.assertEqual(strip_markdown(raw), "第一章\n\n正文。")

    def test_rendered_markdown_and_text_follow_options(self) -> None:
        options = {
            "chapter_ids": ["chapter_10", "chapter_2", "chapter_01"],
            "include_title": True,
            "include_toc": True,
            "separators": False,
            "strip": False,
            "format": "md",
        }
        markdown = render_manuscript(self.project, options)
        self.assertTrue(markdown.startswith("# 测试作品"))
        self.assertLess(markdown.index("第二章正文"), markdown.index("第十章正文"))

        options["format"] = "txt"
        plain = render_manuscript(self.project, options)
        self.assertIn("测试作品", plain)
        self.assertNotIn("## 大纲", plain)
        self.assertNotIn("规划", plain)
