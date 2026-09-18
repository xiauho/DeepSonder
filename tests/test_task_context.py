from tests.style_fixtures import set_style
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

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

    def test_snapshot_hash_covers_structured_foreshadowing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            text = project.load_chapter("chapter_01").raw
            snapshot = AIContextSnapshot.capture(project, "chapter_01", text)
            path = project.memory_dir / "foreshadowing.json"
            path.write_text('{"version":1,"items":[{"id":"f-001"}]}', encoding="utf-8")
            self.assertFalse(snapshot.matches(project, "chapter_01", text))

    def test_snapshot_hash_covers_selected_task_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            text = project.load_chapter("chapter_01").raw
            selected = {"selected_foreshadowing": [{"id": "f-001", "title": "钥匙"}]}
            snapshot = AIContextSnapshot.capture(
                project,
                "chapter_01",
                text,
                task_context=selected,
            )
            self.assertTrue(snapshot.matches(project, "chapter_01", text, task_context=selected))
            self.assertFalse(
                snapshot.matches(
                    project,
                    "chapter_01",
                    text,
                    task_context={"selected_foreshadowing": []},
                )
            )

    def test_capture_does_not_rebuild_full_ai_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            text = project.load_chapter("chapter_01").raw
            with patch(
                "core.context_budget.build_ai_context",
                side_effect=AssertionError("snapshot should use file fingerprints only"),
            ):
                snapshot = AIContextSnapshot.capture(project, "chapter_01", text)

            self.assertTrue(snapshot.matches(project, "chapter_01", text))

    def test_expansion_snapshot_covers_world_and_power_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            text = project.load_chapter("chapter_01").raw
            snapshot = AIContextSnapshot.capture(
                project,
                "chapter_01",
                text,
                task_kind="expand",
            )
            world = project.canon_dir / "world" / "规则.md"
            world.write_text("# 规则\n\n发生了与本章无关的变化。\n", encoding="utf-8")

            self.assertFalse(
                snapshot.matches(project, "chapter_01", text, task_kind="expand")
            )
            self.assertFalse(
                snapshot.matches(project, "chapter_01", text, task_kind="check")
            )

    def test_only_generation_snapshot_tracks_style_guide_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            text = project.load_chapter("chapter_01").raw
            expansion_snapshot = AIContextSnapshot.capture(
                project, "chapter_01", text, task_kind="expand"
            )
            check_snapshot = AIContextSnapshot.capture(
                project, "chapter_01", text, task_kind="check"
            )

            set_style(project.root,
                "# 写作风格指南\n\n冷峻克制。\n")

            self.assertFalse(
                expansion_snapshot.matches(
                    project, "chapter_01", text, task_kind="expand"
                )
            )
            self.assertTrue(
                check_snapshot.matches(project, "chapter_01", text, task_kind="check")
            )
