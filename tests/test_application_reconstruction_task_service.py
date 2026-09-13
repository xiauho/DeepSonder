import tempfile
import threading
import time
import unittest
from pathlib import Path

from application.manuscript_import_service import ManuscriptImportService
from application.project_v2_service import ProjectV2Service
from application.reconstruction_service import ReconstructionCancelled, ReconstructionService
from application.reconstruction_task_service import ReconstructionTaskService


def _project(root: Path):
    source = root / "source"
    source.mkdir()
    (source / "01.md").write_text("# 一\n\n林砚拆开信封。\n", encoding="utf-8")
    (source / "02.md").write_text("# 二\n\n苏乔低声说：走吧。\n", encoding="utf-8")
    output = root / "output"
    output.mkdir()
    return ProjectV2Service().create_project(
        output, "后台识别", import_plan=ManuscriptImportService().scan(source)
    )


class ReconstructionTaskServiceTests(unittest.TestCase):
    def test_dsh_mode_requires_per_run_consent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(Path(tmp))
            service = ReconstructionTaskService()
            try:
                with self.assertRaises(ValueError):
                    service.start(project, extraction_mode="dsh", remote_consent=False)
            finally:
                service.shutdown()

    def test_cancelled_scan_keeps_checkpoint_and_next_run_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(Path(tmp))
            service = ReconstructionService()
            cancel = threading.Event()

            def progress(stage: str, _value: int) -> None:
                if stage.endswith("chapter_0002"):
                    cancel.set()

            with self.assertRaises(ReconstructionCancelled):
                service.generate(project, cancel_event=cancel, progress_callback=progress)
            checkpoint = project.root / "cache" / "reconstruction-checkpoint.json"
            self.assertTrue(checkpoint.is_file())

            batch = service.generate(project)
            self.assertEqual(len(batch["chapter_revisions"]), 2)
            self.assertFalse(checkpoint.exists())

    def test_background_task_emits_progress_and_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(Path(tmp))
            events: list[dict] = []
            service = ReconstructionTaskService(
                event_sink=lambda _name, data: events.append(data["task"])
            )
            try:
                started = service.start(project)
                self.assertIn(started["status"], {"queued", "running"})
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    recent = service.status()["recent"]
                    if recent and recent["status"] in {"succeeded", "failed", "cancelled"}:
                        break
                    time.sleep(0.01)
                self.assertEqual(recent["status"], "succeeded")
                self.assertTrue(recent["batchId"].startswith("batch_"))
                self.assertTrue(any(item["status"] == "running" for item in events))
                self.assertEqual(events[-1]["progress"], 100)
            finally:
                service.shutdown()


if __name__ == "__main__":
    unittest.main()
