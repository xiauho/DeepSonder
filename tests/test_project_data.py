from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from core.project import NovelProject
from core.project_data import ProjectDataStore


class ProjectDataStoreTests(TestCase):
    def test_common_reads_and_new_file_writes_use_one_facade(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            store = ProjectDataStore(project)

            chapter = store.load_chapter("chapter_01")
            self.assertEqual(store.chapter_display_name(chapter.path), "第一章 初始")
            self.assertGreater(store.total_chinese_character_count(), 0)

            target = project.canon_dir / "world" / "新设定.md"
            store.write_new_file(target, "# 新设定\n")
            self.assertEqual(store.read_text(target), "# 新设定\n")
            with self.assertRaises(FileExistsError):
                store.write_new_file(target, "覆盖\n")

    def test_memory_commit_rolls_back_when_second_file_write_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            store = ProjectDataStore(project)
            old_state = store.load_story_state()
            old_summaries = store.load_chapter_summaries()
            original_save_state = store.save_story_state
            failed = False

            def fail_once(state: dict) -> None:
                nonlocal failed
                original_save_state(state)
                if not failed:
                    failed = True
                    raise OSError("模拟故事状态写入失败")

            store.save_story_state = fail_once  # type: ignore[method-assign]
            with self.assertRaisesRegex(OSError, "模拟故事状态写入失败"):
                store.commit_memory_update(
                    "chapter_01",
                    "不应留下的摘要",
                    {"current_chapter": 1, "characters": {}, "foreshadowing": []},
                )

            self.assertEqual(store.load_story_state(), old_state)
            self.assertEqual(store.load_chapter_summaries(), old_summaries)
