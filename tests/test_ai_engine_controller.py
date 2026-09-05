import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from ui.ai_engine_controller import AIEngineController


class FakeDSHClient:
    created = []

    def __init__(self, **kwargs):
        self.options = kwargs
        self.cleaned = False
        self.isolated = False
        self.created.append(self)

    def use_isolated_workspace(self):
        self.isolated = True

    def cleanup(self):
        self.cleaned = True


class AIEngineControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        FakeDSHClient.created.clear()

    def test_running_task_defers_old_client_cleanup(self) -> None:
        running = [False]
        with patch("ui.ai_engine_controller.DSHClient", FakeDSHClient):
            controller = AIEngineController({"dsh_command": "dsh"}, lambda: running[0])
            first = controller.client
            self.assertIsNotNone(first)
            self.assertTrue(first.isolated)
            self.assertNotIn("prompt_transport", first.options)
            self.assertEqual(first.options["file_prompt_budget"], 20_424)
            self.assertEqual(first.options["input_token_budget"], 24_000)
            self.assertEqual(first.options["runtime_reserve_tokens"], 6_000)
            self.assertEqual(first.options["model_context_window_tokens"], 0)
            self.assertEqual(first.options["context_strategy"], "balanced")
            self.assertTrue(callable(first.options["report_callback"]))

            running[0] = True
            second = controller.configure({"dsh_command": "dsh-new"})

            self.assertIsNot(first, second)
            self.assertFalse(first.cleaned)
            controller.cleanup_retired()
            self.assertTrue(first.cleaned)
            self.assertFalse(second.cleaned)

            controller.cleanup()
            self.assertTrue(second.cleaned)
            self.assertIsNone(controller.client)

    def test_idle_reconfiguration_cleans_old_client_immediately(self) -> None:
        with patch("ui.ai_engine_controller.DSHClient", FakeDSHClient):
            controller = AIEngineController({"dsh_command": "dsh"}, lambda: False)
            first = controller.client
            controller.configure({"dsh_command": "dsh-new"})
            self.assertTrue(first.cleaned)
