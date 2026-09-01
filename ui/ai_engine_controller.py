"""Lifecycle management for the configured DSH engine client."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject

from core.config import normalize_config
from core.dsh_client import DSHClient


class AIEngineController(QObject):
    """Create, rotate and release DSH clients independently of AI tasks."""

    def __init__(
        self,
        config: dict,
        is_task_running: Callable[[], bool],
        parent=None,
    ):
        super().__init__(parent)
        self._is_task_running = is_task_running
        self._client: DSHClient | None = None
        self._retired_clients: list[DSHClient] = []
        self.configure(config)

    @property
    def client(self) -> DSHClient | None:
        return self._client

    def configure(self, config: dict) -> DSHClient:
        """Replace the active client, deferring cleanup while a task runs."""
        normalized = normalize_config(config)
        previous = self._client
        if previous is not None:
            if self._is_task_running():
                self._retired_clients.append(previous)
            else:
                previous.cleanup()

        client = DSHClient(
            dsh_command=normalized["dsh_command"],
            launcher_args=normalized["dsh_launcher_args"],
            profile="headless",
            timeout=normalized["dsh_timeout"],
            extra_args=normalized["dsh_extra_args"],
            prompt_transport=normalized["dsh_prompt_transport"],
            file_prompt_budget=normalized["dsh_file_prompt_budget"],
        )
        client.use_isolated_workspace()
        self._client = client
        return client

    def cleanup_retired(self) -> None:
        for client in self._retired_clients:
            client.cleanup()
        self._retired_clients.clear()

    def cleanup(self) -> None:
        if self._client is not None:
            self._client.cleanup()
            self._client = None
        self.cleanup_retired()
