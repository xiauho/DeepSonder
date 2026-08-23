import time
import unittest

from PySide6.QtCore import QCoreApplication

from ui.ai_task_runner import AITaskRunner


class AITaskRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def _drain(self) -> None:
        self.app.processEvents()
        time.sleep(0.01)
        self.app.processEvents()

    def test_runner_emits_typed_success_and_rejects_duplicate_start(self) -> None:
        runner = AITaskRunner()
        results = []
        runner.succeeded.connect(lambda token, result: results.append((token.kind, result)))

        token = runner.start("check", "chapter_01", lambda _event: "report")
        self.assertIsNotNone(token)
        self.assertIsNone(runner.start("memory", "chapter_01", lambda _event: "ignored"))
        runner._thread.wait(1000)
        self._drain()

        self.assertEqual(results, [("check", "report")])
        self.assertFalse(runner.is_running())

    def test_runner_cancel_emits_cancelled_without_success(self) -> None:
        runner = AITaskRunner()
        results = []
        cancelled = []
        runner.succeeded.connect(lambda _token, result: results.append(result))
        runner.cancelled.connect(lambda token: cancelled.append(token.kind))

        runner.start(
            "expand",
            "chapter_01",
            lambda event: self._wait_for_cancel(event),
        )
        for _ in range(20):
            if runner.is_running():
                break
            self._drain()
        self.assertTrue(runner.cancel())
        runner._thread.wait(1000)
        self._drain()

        self.assertEqual(results, [])
        self.assertEqual(cancelled, ["expand"])

    @staticmethod
    def _wait_for_cancel(event):
        while not event.is_set():
            time.sleep(0.01)
        from core.task_controller import AITaskCancelled

        raise AITaskCancelled()
