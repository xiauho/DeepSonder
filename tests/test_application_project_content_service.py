import shutil
import tempfile
import unittest
from pathlib import Path

from application.project_content_service import ProjectContentService
from core.project import NovelProject
from core.project_data import ProjectDataStore


FIXTURE_PROJECT = (
    Path(__file__).parent / "fixtures" / "electron_migration" / "golden_project"
)


class ProjectContentServiceTests(unittest.TestCase):
    def _copy_project(self, root: Path) -> tuple[NovelProject, ProjectDataStore]:
        copied = root / "golden_project"
        shutil.copytree(FIXTURE_PROJECT, copied)
        project = NovelProject(copied)
        return project, ProjectDataStore(project)

    def test_snapshot_covers_all_local_story_documents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, store = self._copy_project(Path(tmp))

            snapshot = ProjectContentService().snapshot(project, store)

            self.assertEqual([item.item_id for item in snapshot.chapters], ["chapter_01", "chapter_02"])
            self.assertEqual([item.title for item in snapshot.characters], ["林砚", "苏乔"])
            self.assertEqual([item.title for item in snapshot.world], ["雾港"])
            self.assertEqual(len(snapshot.outlines), 3)
            self.assertEqual(len(snapshot.timeline), 1)
            self.assertEqual(snapshot.next_chapter_id, "chapter_03")
            core = next(item for item in snapshot.power if item.protected)
            self.assertEqual(core.item_id, "_核心规则")
            self.assertEqual(core.importance, "core")

    def test_typed_trash_restore_and_permanent_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, store = self._copy_project(Path(tmp))
            service = ProjectContentService()
            deleted_world = store.move_world_to_trash(project.canon_dir / "world" / "雾港.md")
            deleted_chapter = store.move_chapter_to_trash("chapter_02")

            trash = service.trash_snapshot(store)

            self.assertEqual({item.kind for item in trash.items}, {"world", "chapter"})
            restored = service.restore_trash_item(
                project, store, "world", deleted_world.trash_id
            )
            self.assertTrue(restored.is_file())
            service.delete_trash_item_forever(store, "chapter", deleted_chapter.trash_id)
            self.assertEqual(service.trash_snapshot(store).items, ())


if __name__ == "__main__":
    unittest.main()
