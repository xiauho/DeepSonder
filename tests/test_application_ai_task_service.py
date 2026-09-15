import shutil
import tempfile
import time
import unittest
from pathlib import Path

from application.ai_task_service import AITaskService
from application.document_service import DocumentService
from core.project import NovelProject
from core.task_controller import AITaskCancelled


FIXTURE_PROJECT = Path(__file__).parent / "fixtures" / "electron_migration" / "golden_project"


def _wait_for(service: AITaskService, task_id: str, status: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = next(item for item in service.status()["recent"] if item["taskId"] == task_id)
        if task["status"] == status:
            return task
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} did not reach {status}")


class AITaskServiceTests(unittest.TestCase):
    def _project(self, root: Path) -> NovelProject:
        copied = root / "project"
        shutil.copytree(FIXTURE_PROJECT, copied)
        return NovelProject(copied)

    def test_writing_result_is_review_first_and_revision_guarded(self) -> None:
        events = []

        def execute(kind, project, chapter_id, options, cancel_event, report):
            self.assertEqual(kind, "expand")
            return ({"type": "writing", "mode": "replace", "text": "新的正文", "charCount": 4}, None)

        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            chapter = project.chapters_dir / "chapter_01.md"
            revision = DocumentService.revision_for_path(chapter)
            service = AITaskService(event_sink=lambda name, data: events.append((name, data)), execution_factory=execute)
            try:
                task = service.start(project, "expand", "chapter_01", source_revision=revision, notice_accepted=True)
                _wait_for(service, task["taskId"], "succeeded")
                self.assertNotIn("新的正文", chapter.read_text(encoding="utf-8"))

                result = service.result(task["taskId"])
                self.assertEqual(result["result"]["text"], "新的正文")
                saved = service.apply_writing_result(project, DocumentService(), task["taskId"])
                self.assertIn("新的正文", saved.content)
                self.assertEqual(service.status()["recent"][0]["status"], "applied")
                self.assertTrue(any(name == "ai.taskUpdated" for name, _data in events))
            finally:
                service.shutdown()

    def test_stale_context_blocks_apply(self) -> None:
        def execute(*_args):
            return ({"type": "writing", "mode": "append", "text": "续写", "charCount": 2}, None)

        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            chapter = project.chapters_dir / "chapter_01.md"
            service = AITaskService(execution_factory=execute)
            try:
                task = service.start(
                    project, "continuation", "chapter_01",
                    source_revision=DocumentService.revision_for_path(chapter),
                    notice_accepted=True,
                )
                _wait_for(service, task["taskId"], "succeeded")
                chapter.write_text(chapter.read_text(encoding="utf-8") + "\n外部修改\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "上下文已经变化"):
                    service.apply_writing_result(project, DocumentService(), task["taskId"])
            finally:
                service.shutdown()

    def test_running_task_can_be_cancelled_cooperatively(self) -> None:
        def execute(kind, project, chapter_id, options, cancel_event, report):
            while not cancel_event.wait(0.01):
                pass
            raise AITaskCancelled()

        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            chapter = project.chapters_dir / "chapter_01.md"
            service = AITaskService(execution_factory=execute)
            try:
                task = service.start(
                    project, "check", "chapter_01",
                    source_revision=DocumentService.revision_for_path(chapter),
                    notice_accepted=True,
                )
                cancelled = service.cancel(task["taskId"])
                self.assertEqual(cancelled["status"], "cancel_requested")
                terminal = _wait_for(service, task["taskId"], "cancelled")
                self.assertFalse(terminal["hasResult"])
            finally:
                service.shutdown()

    def test_notice_and_single_active_task_are_enforced(self) -> None:
        gate = __import__("threading").Event()

        def execute(kind, project, chapter_id, options, cancel_event, report):
            gate.wait(1)
            return ({"type": "consistency", "report": {}, "formatted": "完成"}, None)

        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            chapter = project.chapters_dir / "chapter_01.md"
            revision = DocumentService.revision_for_path(chapter)
            service = AITaskService(execution_factory=execute)
            try:
                with self.assertRaisesRegex(ValueError, "数据处理告知"):
                    service.start(project, "check", "chapter_01", source_revision=revision)
                task = service.start(project, "check", "chapter_01", source_revision=revision, notice_accepted=True)
                with self.assertRaisesRegex(RuntimeError, "已有 AI 任务"):
                    service.start(project, "check", "chapter_01", source_revision=revision, notice_accepted=True)
                gate.set()
                _wait_for(service, task["taskId"], "succeeded")
            finally:
                gate.set()
                service.shutdown()


if __name__ == "__main__":
    unittest.main()
