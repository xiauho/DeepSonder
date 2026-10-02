"""Source-free, optional progress events for the memory pipeline."""

from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import Callable


@dataclass(frozen=True)
class MemoryProgress:
    stage: str
    state: str = "running"
    current: int = 0
    total: int = 0
    elapsed_ms: float = 0.0
    cache_hits: int = 0
    round_number: int = 0
    run_id: str = ""
    request_count: int = 0
    probe_count: int = 0
    retry_count: int = 0
    cache_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    token_budget: int = 0
    evidence_count: int = 0
    stage_times: tuple[tuple[str, float], ...] = ()
    parallelism: int = 1
    fallback_count: int = 0


class ParallelMemoryPhase:
    """Coordinator-only progress: completed count and non-overlapping wall time."""

    def __init__(self, callback, stage, total, round_number=0):
        self.callback, self.stage, self.total = callback, stage, total
        self.round_number = round_number
        self.completed = 0
        self.last_time = perf_counter()

    def emit(self, state, **fields):
        now = perf_counter()
        if state in {"cached", "done"}:
            self.completed += 1
        elapsed = (now - self.last_time) * 1000 if state in {"done", "interrupted"} else 0
        if state != "running":
            self.last_time = now
        publish_progress(self.callback, MemoryProgress(self.stage, state=state,
            current=self.completed, total=self.total, round_number=self.round_number,
            elapsed_ms=elapsed, **fields))


ProgressCallback = Callable[[MemoryProgress], None]


def publish_progress(callback: ProgressCallback | None, event: MemoryProgress) -> None:
    if callback is not None:
        try:
            callback(event)
        except Exception:
            # Display failures must never invalidate a memory result.
            pass


@contextmanager
def memory_phase(callback: ProgressCallback | None, stage: str, **counts):
    started = perf_counter()
    publish_progress(callback, MemoryProgress(stage, **counts))
    try:
        yield
    except BaseException:
        publish_progress(callback, MemoryProgress(
            stage, state="interrupted", elapsed_ms=(perf_counter() - started) * 1000, **counts
        ))
        raise
    else:
        publish_progress(callback, MemoryProgress(
            stage, state="done", elapsed_ms=(perf_counter() - started) * 1000, **counts
        ))
