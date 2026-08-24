from threading import Event
import subprocess
from unittest import TestCase
from unittest.mock import patch

from core.dsh_client import DSHClient
from core.task_controller import AITaskCancelled, AITaskController, AITaskState


class AITaskControllerTests(TestCase):
    def test_only_one_running_task_is_allowed_and_finished_task_can_be_replaced(self) -> None:
        controller = AITaskController()
        first = controller.start("expand", "chapter_01")
        self.assertIsNotNone(first)
        self.assertIsNone(controller.start("check", "chapter_01"))

        self.assertTrue(controller.request_cancel(first))
        self.assertEqual(first.state, AITaskState.CANCEL_REQUESTED)
        self.assertTrue(first.cancel_event.is_set())
        self.assertIsNone(controller.start("memory", "chapter_01"))

        self.assertTrue(controller.finish(first))
        second = controller.start("memory", "chapter_01")
        self.assertIsNotNone(second)
        self.assertFalse(controller.is_current(first))
        self.assertTrue(controller.is_current(second))

    def test_cancel_request_is_idempotently_rejected_after_finish(self) -> None:
        controller = AITaskController()
        token = controller.start("check", "chapter_01")
        self.assertTrue(controller.finish(token))
        self.assertFalse(controller.request_cancel(token))


class CancellableDSHTests(TestCase):
    def test_already_cancelled_task_does_not_start_a_process(self) -> None:
        event = Event()
        event.set()
        client = DSHClient("dsh")
        with patch("core.dsh_client.subprocess.Popen") as popen:
            with self.assertRaises(AITaskCancelled):
                client.generate("system", "user", cancel_event=event)
        popen.assert_not_called()

    def test_cancelled_process_is_killed_and_not_reported_as_failure(self) -> None:
        event = Event()

        class FakeProcess:
            def __init__(self) -> None:
                self.returncode = None
                self.killed = False
                self.communicate_calls = 0

            def poll(self):
                return self.returncode

            def communicate(self, timeout=None):
                self.communicate_calls += 1
                if not self.killed:
                    event.set()
                    raise subprocess.TimeoutExpired("dsh", timeout)
                self.returncode = -9
                return "", ""

            def kill(self):
                self.killed = True

        process = FakeProcess()
        client = DSHClient("dsh")
        with patch("core.dsh_client.subprocess.Popen", return_value=process):
            with self.assertRaises(AITaskCancelled):
                client.generate("system", "user", cancel_event=event)
        self.assertTrue(process.killed)
        self.assertGreaterEqual(process.communicate_calls, 2)

    def test_process_tree_reap_has_a_bounded_fallback(self) -> None:
        class StuckProcess:
            def __init__(self) -> None:
                self.killed = False
                self.communicate_calls = 0

            def communicate(self, timeout=None):
                self.communicate_calls += 1
                raise subprocess.TimeoutExpired("dsh", timeout)

            def kill(self):
                self.killed = True

        process = StuckProcess()
        DSHClient._terminate_process_tree(process)

        self.assertTrue(process.killed)
        self.assertEqual(process.communicate_calls, 2)
