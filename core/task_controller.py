"""Thread-safe lifecycle state for one foreground AI task."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import Event, Lock
from uuid import uuid4


class AITaskCancelled(Exception):
    """Raised when an AI task is cancelled before it produces a result."""


class AITaskState(str, Enum):
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    FINISHED = "finished"


@dataclass
class AITaskToken:
    kind: str
    chapter_id: str
    task_id: str = field(default_factory=lambda: uuid4().hex)
    cancel_event: Event = field(default_factory=Event, repr=False)
    state: AITaskState = AITaskState.RUNNING


class AITaskController:
    """Allow at most one active task and identify every result callback."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._current: AITaskToken | None = None

    def start(self, kind: str, chapter_id: str) -> AITaskToken | None:
        with self._lock:
            if self._current and self._current.state in {
                AITaskState.RUNNING,
                AITaskState.CANCEL_REQUESTED,
            }:
                return None
            token = AITaskToken(kind=kind, chapter_id=chapter_id)
            self._current = token
            return token

    def request_cancel(self, token: AITaskToken | None) -> bool:
        with self._lock:
            if token is None or self._current is not token:
                return False
            if token.state != AITaskState.RUNNING:
                return False
            token.state = AITaskState.CANCEL_REQUESTED
            token.cancel_event.set()
            return True

    def finish(self, token: AITaskToken | None) -> bool:
        with self._lock:
            if token is None or self._current is not token:
                return False
            token.state = AITaskState.FINISHED
            return True

    def is_current(self, token: AITaskToken | None) -> bool:
        with self._lock:
            return token is not None and self._current is token
