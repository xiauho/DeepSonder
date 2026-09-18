from tests.style_fixtures import set_style
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from core.project import NovelProject
from core.project_migrations import migrate_project
from core.project_schema import PROJECT_MANIFEST_RELATIVE_PATH
from core.project_data import (
    CanonEntryConflictError,
    CharacterIdConflictError,
    ChapterIdConflictError,
    ProjectDataStore,
    character_id_exists,
    chapter_id_exists,
    next_available_character_id,
    next_available_chapter_id,
    sanitize_filename,
)
from core.foreshadowing import ForeshadowingStore


class ProjectDataStoreTests(TestCase):
    def test_timeline_notes_start_empty_and_preserve_existing_content(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            store = ProjectDataStore(project)
            path = project.canon_dir / "timeline.md"
            self.assertEqual(path.read_bytes(), b"")

            path.unlink()
            self.assertEqual(store.create_timeline(), path)
            self.assertEqual(path.read_bytes(), b"")

            notes = "主角出发前，先与旧友告别。\n"
            path.write_text(notes, encoding="utf-8")
            with self.assertRaises(FileExistsError):
                store.create_timeline()
            self.assertEqual(path.read_text(encoding="utf-8"), notes)

    def test_project_style_guide_is_seeded_but_inactive_until_authored(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            store = ProjectDataStore(project)

            self.assertFalse(store.style_guide_path.is_file())
            self.assertEqual(store.load_style_guide(), "")

            set_style(project.root,
                "# 写作风格指南\n\n\n\n## 总体气质\n冷峻克制，避免总结式升华。\n")
            loaded = store.load_style_guide()
            self.assertIn("冷峻克制", loaded)
            self.assertNotIn("作者确认的文风画像", loaded)

    def test_next_chapter_id_uses_maximum_number_and_skips_conflicts(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            (project.chapters_dir / "chapter_03.md").write_text(
                "# 第三章\n", encoding="utf-8"
            )
            self.assertEqual(next_available_chapter_id(project), "chapter_04")
            self.assertEqual(
                next_available_chapter_id(project, "chapter_03"), "chapter_03_2"
            )

    def test_chapter_id_conflict_is_case_insensitive(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            (project.chapters_dir / "Chapter_02.md").write_text(
                "# 第二章\n", encoding="utf-8"
            )
            self.assertTrue(chapter_id_exists(project, "chapter_02"))
            # The helper's result is also stable on case-sensitive hosts.
            self.assertEqual(
                next_available_chapter_id(project, "chapter_02"), "chapter_02_2"
            )

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

    def test_canon_entry_factory_creates_all_supported_templates(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            store = ProjectDataStore(project)

            self.assertTrue(store.core_power_path.is_file())
            self.assertNotIn(store.core_power_path, store.list_power_entries())

            character = store.create_canon_entry("角色", "新角色")
            world = store.create_canon_entry("world", "新世界")
            power = store.create_canon_entry("能力体系", "新体系")

            self.assertEqual(character, project.canon_dir / "characters" / "新角色.md")
            self.assertEqual(world, project.canon_dir / "world" / "新世界.md")
            self.assertEqual(power, project.canon_dir / "power" / "新体系.md")
            character_text = character.read_text(encoding="utf-8")
            self.assertIn("## 秘密与人物弧线", character_text)
            self.assertIn("<!-- novalist:character-card:v2 -->", character_text)
            self.assertIn("## 能力档案", character_text)
            self.assertIn("novalist:auto-state:v1:start", character_text)
            self.assertIn("## 对剧情的约束", world.read_text(encoding="utf-8"))
            self.assertIn("## 代价与副作用", power.read_text(encoding="utf-8"))

            with self.assertRaises(ValueError):
                store.create_canon_entry("未知类型", "条目")

    def test_new_project_seeds_system_templates_and_registry(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            store = ProjectDataStore(project)
            ability = project.canon_dir / "power" / "能力体系设定.md"
            space = project.canon_dir / "power" / "空间体系设定.md"

            self.assertTrue(ability.is_file())
            self.assertTrue(space.is_file())
            registry_text = project.system_registry_path.read_text(encoding="utf-8")
            self.assertIn("canon/power/能力体系设定.md", registry_text)
            self.assertEqual(store.system_metadata(ability)["importance"], "non_core")
            self.assertIn(space, store.list_non_core_systems())

            store.set_system_importance(ability, "core")
            self.assertIn(ability, store.list_core_systems())
            self.assertNotIn(ability, store.list_non_core_systems())

            related = project.find_related_canon(
                "chapter_01",
                selected_power=store.list_core_systems(),
                core_power_paths=store.list_core_systems(),
            )
            self.assertIn("能力体系设定", related.core_systems)
            self.assertNotIn("能力体系设定", related.power)

    def test_related_canon_separates_core_selected_and_background_power(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            selected = project.canon_dir / "power" / "重点体系.md"
            background = project.canon_dir / "power" / "背景体系.md"
            selected.write_text("# 重点体系\n本章优先规则。\n", encoding="utf-8")
            background.write_text("# 背景体系\n仅作背景参考。\n", encoding="utf-8")

            related = project.find_related_canon(
                "chapter_01", selected_power=[selected]
            )

            self.assertIn("# 核心规则", related.core_power)
            self.assertIn("本章优先规则", related.selected_power)
            self.assertNotIn("本章优先规则", related.power)
            self.assertIn("仅作背景参考", related.power)

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

    def test_delete_character_moves_card_to_trash_and_keeps_story_memory(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            card = project.canon_dir / "characters" / "林夜.md"
            card.write_text("# 林夜\n\n- 身份：主角\n", encoding="utf-8")
            state = {
                "current_chapter": 2,
                "characters": {"林夜": {"state": "受伤"}},
                "foreshadowing": [],
            }
            store = ProjectDataStore(project)
            store.save_story_state(state)

            deleted = store.delete_character("林夜")

            self.assertEqual(deleted, card)
            self.assertFalse(card.exists())
            self.assertEqual(store.load_story_state(), state)
            entries = store.list_character_trash()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].character_id, "林夜")
            self.assertEqual(
                (entries[0].path / "character.md").read_text(encoding="utf-8"),
                "# 林夜\n\n- 身份：主角\n",
            )

            restored = store.restore_character_trash_item(entries[0].trash_id)

            self.assertEqual(restored, card)
            self.assertTrue(card.exists())
            self.assertEqual(store.list_character_trash(), [])
            self.assertTrue(character_id_exists(project, "林夜"))

    def test_character_restore_conflict_can_rename_and_trash_can_be_deleted_forever(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            original = project.canon_dir / "characters" / "林夜.md"
            original.write_text("# 林夜\n旧设定\n", encoding="utf-8")
            store = ProjectDataStore(project)
            store.delete_character("林夜")
            original.write_text("# 林夜\n新设定\n", encoding="utf-8")
            entry = store.list_character_trash()[0]

            with self.assertRaises(CharacterIdConflictError):
                store.restore_character_trash_item(entry.trash_id)
            self.assertEqual(
                next_available_character_id(project, "林夜"), "林夜_2"
            )
            restored = store.restore_character_trash_item(
                entry.trash_id, conflict_policy="rename"
            )
            self.assertEqual(restored.stem, "林夜_2")
            self.assertEqual(restored.read_text(encoding="utf-8"), "# 林夜\n旧设定\n")

            store.delete_character(restored.stem)
            trash_entry = store.list_character_trash()[0]
            store.delete_character_trash_item(trash_entry.trash_id)
            self.assertEqual(store.list_character_trash(), [])

    def test_delete_character_rejects_path_traversal(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            store = ProjectDataStore(project)
            with self.assertRaises(ValueError):
                store.delete_character("..\\project")

    def test_canon_trash_round_trip_covers_world_power_and_timeline(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            store = ProjectDataStore(project)
            world = store.create_canon_entry("world", "旧世界")
            power = store.create_canon_entry("power", "旧体系")
            store.set_system_importance(power, "core")
            timeline = project.canon_dir / "timeline.md"

            world_entry = store.move_canon_entry_to_trash("world", world)
            power_entry = store.move_canon_entry_to_trash("power", power)
            timeline_entry = store.move_canon_entry_to_trash("timeline", timeline)

            self.assertFalse(world.exists())
            self.assertFalse(power.exists())
            self.assertFalse(timeline.exists())
            self.assertNotIn(
                "canon/power/旧体系.md",
                store.load_system_registry()["entries"],
            )
            self.assertEqual(
                {entry.kind for entry in store.list_canon_trash()},
                {"world", "power", "timeline"},
            )

            self.assertEqual(store.restore_canon_trash_item(world_entry.trash_id), world)
            self.assertEqual(store.restore_canon_trash_item(power_entry.trash_id), power)
            self.assertEqual(
                store.system_metadata(power)["importance"], "core"
            )
            self.assertEqual(
                store.restore_canon_trash_item(timeline_entry.trash_id), timeline
            )
            self.assertEqual(store.list_canon_trash(), [])

    def test_canon_restore_rename_updates_power_registry_and_timeline_conflicts(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            store = ProjectDataStore(project)
            power = store.create_canon_entry("power", "秘术")
            store.set_system_importance(power, "core")
            deleted_power = store.move_canon_entry_to_trash("power", power)
            power.write_text("# 秘术\n新版本\n", encoding="utf-8")

            with self.assertRaises(CanonEntryConflictError):
                store.restore_canon_trash_item(deleted_power.trash_id)
            restored = store.restore_canon_trash_item(
                deleted_power.trash_id, conflict_policy="rename"
            )
            self.assertEqual(restored.stem, "秘术_2")
            self.assertEqual(store.system_metadata(restored)["importance"], "core")
            self.assertIn(
                "canon/power/秘术_2.md",
                store.load_system_registry()["entries"],
            )

            timeline = project.canon_dir / "timeline.md"
            deleted_timeline = store.move_canon_entry_to_trash("timeline", timeline)
            store.create_timeline()
            with self.assertRaises(CanonEntryConflictError):
                store.restore_canon_trash_item(deleted_timeline.trash_id)

    def test_canon_trash_permanent_delete_and_core_rule_protection(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            store = ProjectDataStore(project)
            with self.assertRaises(ValueError):
                store.move_canon_entry_to_trash("power", store.core_power_path)

            world = store.create_canon_entry("world", "待清理")
            entry = store.move_canon_entry_to_trash("world", world)
            store.delete_canon_trash_item(entry.trash_id)
            self.assertEqual(store.list_canon_trash(), [])
            self.assertFalse(world.exists())

    def test_deleted_canon_is_removed_from_related_context_and_restore_readds_it(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            store = ProjectDataStore(project)
            world = store.create_canon_entry("world", "上下文规则")
            power = store.create_canon_entry("power", "上下文体系")
            (project.chapters_dir / "chapter_01.md").write_text(
                "# 第一章\n\n正文\n", encoding="utf-8"
            )
            related = project.find_related_canon("chapter_01")
            self.assertIn("上下文规则", related.world)
            self.assertIn("上下文体系", related.power)

            world_entry = store.move_canon_entry_to_trash("world", world)
            power_entry = store.move_canon_entry_to_trash("power", power)
            related_after_delete = project.find_related_canon("chapter_01")
            self.assertNotIn("上下文规则", related_after_delete.world)
            self.assertNotIn("上下文体系", related_after_delete.power)

            store.restore_canon_trash_item(world_entry.trash_id)
            store.restore_canon_trash_item(power_entry.trash_id)
            related_after_restore = project.find_related_canon("chapter_01")
            self.assertIn("上下文规则", related_after_restore.world)
            self.assertIn("上下文体系", related_after_restore.power)

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

    def test_restore_can_rename_when_original_id_is_occupied(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            original = project.chapters_dir / "chapter_02.md"
            original.write_text("# 第二章\n", encoding="utf-8")
            store = ProjectDataStore(project)
            store.save_chapter_summaries({"chapter_02": "旧摘要"})
            store.delete_chapter("chapter_02")
            original.write_text("# 新的第二章\n", encoding="utf-8")

            entry = store.list_trash()[0]
            restored = store.restore_trash_item(entry.trash_id, conflict_policy="rename")

            self.assertEqual(restored.stem, "chapter_02_2")
            self.assertTrue(restored.exists())
            self.assertEqual(
                store.load_chapter_summaries(), {"chapter_02_2": "旧摘要"}
            )

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
            (project.root / PROJECT_MANIFEST_RELATIVE_PATH).unlink()

            result = migrate_project(project.root)
            store = ForeshadowingStore(project)
            notes = store.load_notes()

            self.assertEqual([note["title"] for note in notes], ["残缺古剑的来历", "神秘玉佩的来源"])
            self.assertTrue(all(note["id"].startswith("legacy-") for note in notes))
            self.assertTrue((project.memory_dir / "foreshadowing.json").exists())
            self.assertEqual(store.load_notes(), notes)
            self.assertNotIn("foreshadowing", project.load_story_state())
            self.assertTrue(result.backup_path.is_dir())

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

    def test_foreshadowing_resolution_is_batched_idempotent_and_reversible(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            store = ForeshadowingStore(project)
            first = store.create_note("古剑来历")
            second = store.create_note("玉佩来源")

            resolved = store.resolve_notes(
                [first["id"], first["id"], second["id"]],
                "chapter_08",
            )

            self.assertEqual(len(resolved), 2)
            self.assertTrue(all(note["status"] == "resolved" for note in resolved))
            self.assertTrue(
                all(note["resolved_chapter"] == "chapter_08" for note in resolved)
            )
            self.assertEqual(
                store.resolve_notes([first["id"]], "chapter_08"),
                [],
            )

            reopened = store.update_note(first["id"], status="open")
            self.assertEqual(reopened["status"], "open")
            self.assertEqual(reopened["resolved_chapter"], "")
