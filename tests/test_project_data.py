from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from core.project import NovelProject
from core.project_data import ProjectDataStore, sanitize_filename
from core.foreshadowing import ForeshadowingStore


class ProjectDataStoreTests(TestCase):
    def test_sanitize_filename_is_shared_and_stable(self) -> None:
        self.assertEqual(sanitize_filename('  a:b?.md  '), "a_b_.md")
        self.assertEqual(sanitize_filename("..."), "untitled")

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

    def test_delete_chapter_moves_file_and_summary_to_trash(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            chapter = project.chapters_dir / "chapter_02.md"
            chapter.write_text("# 第二章\n\n## 正文\n正文\n", encoding="utf-8")
            store = ProjectDataStore(project)
            store.save_chapter_summaries(
                {"chapter_01": "保留摘要", "chapter_02": "删除摘要"}
            )

            deleted = store.delete_chapter("chapter_02")

            self.assertEqual(deleted, chapter)
            self.assertFalse(chapter.exists())
            self.assertEqual(store.load_chapter_summaries(), {"chapter_01": "保留摘要"})
            self.assertTrue((project.chapters_dir / "chapter_01.md").exists())
            entries = store.list_trash()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].chapter_id, "chapter_02")
            self.assertEqual(
                (entries[0].path / "chapter.md").read_text(encoding="utf-8"),
                "# 第二章\n\n## 正文\n正文\n",
            )

            restored = store.restore_trash_item(entries[0].trash_id)

            self.assertEqual(restored, chapter)
            self.assertTrue(chapter.exists())
            self.assertEqual(
                store.load_chapter_summaries(),
                {"chapter_01": "保留摘要", "chapter_02": "删除摘要"},
            )
            self.assertEqual(store.list_trash(), [])

    def test_restore_rejects_existing_original_path_and_permanent_delete_removes_entry(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            chapter = project.chapters_dir / "chapter_02.md"
            chapter.write_text("# 第二章\n\n## 正文\n正文\n", encoding="utf-8")
            store = ProjectDataStore(project)
            store.delete_chapter("chapter_02")
            entry = store.list_trash()[0]
            chapter.write_text("# 新的第二章\n", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                store.restore_trash_item(entry.trash_id)

            store.delete_trash_item(entry.trash_id)
            self.assertEqual(store.list_trash(), [])
            self.assertEqual(chapter.read_text(encoding="utf-8"), "# 新的第二章\n")

    def test_delete_chapter_rejects_path_traversal_and_rolls_back_metadata_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            store = ProjectDataStore(project)
            with self.assertRaises(ValueError):
                store.delete_chapter("..\\project")

            chapter = project.chapters_dir / "chapter_02.md"
            chapter.write_text("# 第二章\n\n## 正文\n正文\n", encoding="utf-8")
            store.save_chapter_summaries({"chapter_02": "保留摘要"})
            original_save = store.save_chapter_summaries

            def fail_save(_summaries: dict) -> None:
                raise OSError("模拟摘要写入失败")

            store.save_chapter_summaries = fail_save  # type: ignore[method-assign]
            with self.assertRaisesRegex(OSError, "模拟摘要写入失败"):
                store.delete_chapter("chapter_02")

            self.assertTrue(chapter.exists())
            store.save_chapter_summaries = original_save  # type: ignore[method-assign]
            self.assertEqual(store.load_chapter_summaries(), {"chapter_02": "保留摘要"})

    def test_replace_chapter_body_preserves_custom_sections_after正文(self) -> None:
        raw = (
            "# 第一章\n\n"
            "## 大纲\n目标\n\n"
            "## 剧情简写\n简写\n\n"
            "## 正文\n旧正文\n\n"
            "## 作者备注\n保留这段\n"
        )

        replaced = NovelProject.replace_chapter_body(raw, "新正文")

        self.assertIn("## 正文\n新正文", replaced)
        self.assertNotIn("旧正文", replaced)
        self.assertIn("## 作者备注\n保留这段", replaced)

    def test_related_canon_lookup_reuses_cache_until_a_source_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            with patch.object(project, "read_file", wraps=project.read_file) as read_file:
                first = project.find_related_canon("chapter_01")
                first_reads = read_file.call_count
                second = project.find_related_canon("chapter_01")

                self.assertEqual(first.world, second.world)
                self.assertEqual(read_file.call_count, first_reads)

                world = project.canon_dir / "world" / "新设定.md"
                world.write_text("# 新设定\n\n规则已更新。\n", encoding="utf-8")
                project.find_related_canon("chapter_01")
                self.assertGreater(read_file.call_count, first_reads)

    def test_legacy_foreshadowing_is_migrated_to_stable_notes(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            state = project.load_story_state()
            state["foreshadowing"] = ["残缺古剑的来历", "神秘玉佩的来源"]
            project.save_story_state(state)
            # Simulate a project created before the independent note file
            # existed. New projects intentionally start with an empty file.
            (project.memory_dir / "foreshadowing.json").unlink()

            store = ForeshadowingStore(project)
            notes = store.load_notes()

            self.assertEqual([note["title"] for note in notes], ["残缺古剑的来历", "神秘玉佩的来源"])
            self.assertTrue(all(note["id"].startswith("legacy-") for note in notes))
            self.assertTrue((project.memory_dir / "foreshadowing.json").exists())
            self.assertEqual(store.load_notes(), notes)

    def test_foreshadowing_can_be_updated_and_recycled_independently(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            store = ForeshadowingStore(project)
            created = store.create_note(
                "残缺古剑的来历",
                note="后续揭示来源",
                first_seen_chapter="chapter_01",
                planned_resolution_chapter="chapter_20",
                priority="high",
                related_characters=["林夜"],
            )
            updated = store.update_note(
                created["id"],
                recent_seen_chapter="chapter_05",
                appearances=[
                    {"chapter_id": "chapter_01", "note": "首次发现"},
                    {"chapter_id": "chapter_05", "note": "禁制反应"},
                ],
            )
            self.assertEqual(updated["recent_seen_chapter"], "chapter_05")
            self.assertEqual(len(store.load_notes()), 1)

            deleted = store.delete_note(created["id"])
            self.assertEqual(store.load_notes(), [])
            self.assertEqual(store.list_trash()[0].foreshadowing_id, created["id"])

            restored = store.restore_trash_item(deleted.trash_id)
            self.assertEqual(restored["id"], created["id"])
            self.assertEqual(store.load_notes()[0]["title"], "残缺古剑的来历")

            deleted_again = store.delete_note(created["id"])
            store.delete_trash_item(deleted_again.trash_id)
            self.assertEqual(store.list_trash(), [])
