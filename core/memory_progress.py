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
