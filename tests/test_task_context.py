import tempfile
from pathlib import Path
from unittest import TestCase

from core.project import NovelProject
from core.task_context import AIContextSnapshot


class AIContextSnapshotTests(TestCase):
    def test_snapshot_matches_unchanged_project_and_editor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            text = project.load_chapter("chapter_01").raw
            snapshot = AIContextSnapshot.capture(project, "chapter_01", text)
            self.assertTrue(snapshot.matches(project, "chapter_01", text))

    def test_snapshot_rejects_editor_and_context_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            text = project.load_chapter("chapter_01").raw
            snapshot = AIContextSnapshot.capture(project, "chapter_01", text)

            self.assertFalse(snapshot.matches(project, "chapter_01", text + "\n新内容"))

            character = project.canon_dir / "characters" / "角色.md"
            character.write_text("# 角色\n\n状态已变化。\n", encoding="utf-8")
            self.assertFalse(snapshot.matches(project, "chapter_01", text))

    def test_snapshot_hash_covers_prompt_story_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            text = project.load_chapter("chapter_01").raw
            snapshot = AIContextSnapshot.capture(project, "chapter_01", text)
            state = project.load_story_state()
            state["current_location"] = "新地点"
            project.save_story_state(state)
            self.assertFalse(snapshot.matches(project, "chapter_01", text))
