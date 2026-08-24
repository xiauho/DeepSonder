import tempfile
import time
import unittest
from pathlib import Path
from threading import Event

from PySide6.QtCore import QCoreApplication

from core.project import NovelProject
from ui.ai_controller import AIController


class AIControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def _drain(self) -> None:
        self.app.processEvents()
        time.sleep(0.01)
        self.app.processEvents()

    def test_start_captures_context_and_clears_after_finish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            release = Event()
            results = []
            controller = AIController()
            controller.succeeded.connect(lambda _token, result: results.append(result))

            token = controller.start(
                "check",
                project,
                "chapter_01",
                project.load_chapter("chapter_01").raw,
                lambda _cancel_event: (release.wait(1), "报告")[1],
            )
            self.assertIsNotNone(token)
            self.assertEqual(controller.task_chapter_id, "chapter_01")
            self.assertTrue(
                controller.context_matches(
                    project,
                    "chapter_01",
                    project.load_chapter("chapter_01").raw,
                )
            )

            state = project.load_story_state()
            state["current_location"] = "新地点"
            project.save_story_state(state)
            self.assertFalse(
                controller.context_matches(
                    project,
                    "chapter_01",
                    project.load_chapter("chapter_01").raw,
                )
            )

            release.set()
            thread = controller.task_runner._thread
            self.assertIsNotNone(thread)
            thread.wait(1000)
            self._drain()

            self.assertEqual(results, ["报告"])
            self.assertIsNone(controller.task_chapter_id)
            self.assertFalse(controller.is_running())

    def test_start_rejects_duplicate_and_cancel_is_forwarded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            release = Event()
            controller = AIController()
            text = project.load_chapter("chapter_01").raw
            worker = lambda _cancel_event: (release.wait(1), "完成")[1]

            first = controller.start("check", project, "chapter_01", text, worker)
            self.assertIsNotNone(first)
            self.assertIsNone(controller.start("memory", project, "chapter_01", text, worker))
            self.assertTrue(controller.cancel())
            release.set()

            thread = controller.task_runner._thread
            self.assertIsNotNone(thread)
            thread.wait(1000)
            self._drain()
            self.assertFalse(controller.is_running())

    def test_success_context_survives_worker_finish_until_result_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            text = project.load_chapter("chapter_01").raw
            controller = AIController()
            token = controller.start(
                "expand",
                project,
                "chapter_01",
                text,
                lambda _cancel_event: "生成结果",
            )
            self.assertIsNotNone(token)

            thread = controller.task_runner._thread
            self.assertIsNotNone(thread)
            thread.wait(1000)
            self._drain()

            # The worker is finished, but a UI preview/confirmation may still
            # be open.  The token-scoped snapshot must remain usable then.
            self.assertTrue(controller.context_matches(project, "chapter_01", text, token))
            self.assertFalse(controller.context_matches(project, "chapter_01", text))

            controller.release_result(token)
            self.assertFalse(controller.context_matches(project, "chapter_01", text, token))
