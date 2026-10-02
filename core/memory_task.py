"""One memory run's deadline and redacted metrics, independent of Qt."""
from __future__ import annotations

from dataclasses import replace
from time import monotonic, perf_counter
from uuid import uuid4

from .memory_progress import MemoryProgress, publish_progress
from .task_controller import AITaskCancelled

DEFAULT_MEMORY_TIMEOUT = 1800


class MemoryTaskSession:
    def __init__(self, client, timeout, cancel_event, callback):
        self.client = client
        self.cancel_event = cancel_event
        self.callback = callback
        self.run_id = uuid4().hex
        self.deadline = monotonic() + max(1, float(timeout))
        self.started = perf_counter()
        self.requests = self.probes = self.retries = self.cache_hits = self.rounds = 0
        self.stage_times = {}

    def __getattr__(self, name):
        return getattr(self.client, name)

    def check_task_deadline(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise AITaskCancelled()
        if monotonic() >= self.deadline:
            raise RuntimeError("整次记忆更新等待超时，已停止新请求；可重新发起并复用已校验缓存。")

    def progress(self, event):
        self.rounds = max(self.rounds, event.round_number)
        if event.state == "cached":
            self.cache_hits += 1
        if event.state in {"done", "interrupted"}:
            self.stage_times[event.stage] = self.stage_times.get(event.stage, 0) + event.elapsed_ms
        publish_progress(self.callback, replace(event, run_id=self.run_id))

    def _report(self, report):
        self.probes += report.probe_count
        self.retries += report.file_ack_retry_count

    def generate_json(self, system_prompt, user_prompt, **options):
        self.check_task_deadline()
        self.requests += 1
        report = options.get("context_report")
        if report is not None:
            options["context_report"] = replace(report, memory_run_id=self.run_id,
                                                request_index=self.requests)
        options["task_deadline"] = self.deadline
        options["invocation_callback"] = self._report
        result = self.client.generate_json(system_prompt, user_prompt, **options)
        # Late results never enter a cache or reach review after cancellation
        # or expiration, including clients used by deterministic tests.
        self.check_task_deadline()
        return result

    def finish(self, state):
        publish_progress(self.callback, MemoryProgress("summary", state=state,
            run_id=self.run_id, elapsed_ms=(perf_counter() - self.started) * 1000,
            request_count=self.requests, probe_count=self.probes, retry_count=self.retries,
            cache_hits=self.cache_hits, round_number=self.rounds,
            stage_times=tuple(self.stage_times.items())))
