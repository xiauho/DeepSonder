"""One memory run's deadline and redacted metrics, independent of Qt."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Event, Lock
from time import monotonic, perf_counter
from uuid import uuid4

from .memory_progress import MemoryProgress, publish_progress
from .task_controller import AITaskCancelled

DEFAULT_MEMORY_TIMEOUT = 1800


def memory_config_fingerprint(client) -> str:
    """Hash captured invocation settings without persisting sensitive arguments."""
    values = {}
    for name in ("dsh_command", "launcher_args", "profile", "extra_args", "context_strategy"):
        value = getattr(client, name, None)
        values[name] = value if isinstance(value, (str, list, tuple, dict, int, float, bool)) else None
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


class MemoryTaskSession:
    def __init__(self, client, timeout, cancel_event, callback, concurrency=1, report_callback=None):
        self.client = client
        self.cancel_event = cancel_event
        self.callback = callback
        self.report_callback = report_callback
        self.run_id = uuid4().hex
        self.deadline = monotonic() + max(1, float(timeout))
        self.started = perf_counter()
        self.requests = self.probes = self.retries = self.cache_hits = self.rounds = 0
        self.stage_times = {}
        self._lock = Lock()
        self._clients = []
        self.parallelism = 2 if int(concurrency) == 2 and callable(
            getattr(client, "fork_for_memory", None)) else 1
        self.fallbacks = 0
        if int(concurrency) == 2 and self.parallelism == 1:
            self._downgrade("client_unavailable")

    def __getattr__(self, name):
        return getattr(self.client, name)

    def check_task_deadline(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise AITaskCancelled()
        if monotonic() >= self.deadline:
            raise RuntimeError("整次记忆更新等待超时，已停止新请求；可重新发起并复用已校验缓存。")

    def progress(self, event):
        with self._lock:
            self.rounds = max(self.rounds, event.round_number)
            if event.state == "cached":
                self.cache_hits += 1
            if event.state in {"done", "interrupted"}:
                self.stage_times[event.stage] = self.stage_times.get(event.stage, 0) + event.elapsed_ms
        publish_progress(self.callback, replace(event, run_id=self.run_id))

    def _report(self, report):
        with self._lock:
            self.probes += report.probe_count
            self.retries += report.file_ack_retry_count
        if self.report_callback is not None:
            try:
                self.report_callback(report)
            except Exception:
                pass

    def generate_json(self, system_prompt, user_prompt, **options):
        # Once lanes exist, reuse one after they have joined (e.g. final proposal).
        client = self._clients[0] if self._clients else self.client
        return self._generate(client, None, system_prompt, user_prompt, options)

    def _generate(self, client, stop, system_prompt, user_prompt, options):
        self.check_task_deadline()
        cancellation = _Cancellation(self.cancel_event, options.get("cancel_event"), stop)
        if cancellation.is_set():
            raise AITaskCancelled()
        with self._lock:
            self.requests += 1
            request_index = self.requests
        report = options.get("context_report")
        if report is not None:
            options["context_report"] = replace(report, memory_run_id=self.run_id,
                                                request_index=request_index)
        if cancellation.events:
            options["cancel_event"] = cancellation
        options["task_deadline"] = self.deadline
        options["invocation_callback"] = self._report
        result = client.generate_json(system_prompt, user_prompt, **options)
        # Late results never enter a cache or reach review after cancellation
        # or expiration, including clients used by deterministic tests.
        self.check_task_deadline()
        if cancellation.is_set():
            raise AITaskCancelled()
        return result

    @property
    def parallel_enabled(self):
        return self.parallelism == 2

    def _downgrade(self, reason):
        self.parallelism = 1
        self.fallbacks += 1
        self.progress(MemoryProgress("parallel", state="fallback", parallelism=1,
            fallback_count=self.fallbacks, cache_reason=reason))

    def run_jobs(self, jobs, work, accept):
        """At most two in-flight jobs; commit on coordinator before queuing more.

        On a capacity rejection, drain/stop both lanes first and retry only
        unfinished jobs once serially. Other failures require explicit retry.
        """
        jobs = list(jobs)
        if not jobs:
            return
        self.check_task_deadline()
        if self.parallel_enabled and len(jobs) > 1 and not self._clients:
            try:
                for _ in range(2):
                    client = self.client.fork_for_memory()
                    if client is self.client or any(client is item for item in self._clients):
                        raise RuntimeError("记忆并发客户端未隔离。")
                    self._clients.append(client)
            except Exception:
                self.close()
                self._downgrade("client_unavailable")
        if not self.parallel_enabled or len(jobs) < 2:
            for job in jobs:
                self.check_task_deadline()
                result = work(self, job)
                self.check_task_deadline()
                accept(job, result)
            return

        self.progress(MemoryProgress("parallel", state="running", parallelism=2))
        stop, completed = Event(), set()
        pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="DeepSonder-memory")
        pending, next_index = {}, 0
        error = None

        def submit(lane):
            nonlocal next_index
            self.check_task_deadline()
            index = next_index
            next_index += 1
            worker = _WorkerSession(self, self._clients[lane], stop)
            def execute():
                result = work(worker, jobs[index])
                worker.check_task_deadline()
                return result
            pending[pool.submit(execute)] = (index, lane)

        try:
            submit(0)
            submit(1)
            while pending:
                self.check_task_deadline()
                ready, _ = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
                self.check_task_deadline()
                failures = [future.exception() for future in ready if future.exception() is not None]
                if failures:
                    ready = {future for future in pending if future.done()}
                    failures = [future.exception() for future in ready if future.exception() is not None]
                    stop.set()  # no new jobs, including while committing ready successes
                free_lanes = []
                for future in sorted(ready, key=lambda item: pending[item][0]):
                    index, lane = pending.pop(future)
                    if future.exception() is not None:
                        continue
                    self.check_task_deadline()
                    accept(jobs[index], future.result())
                    completed.add(index)
                    free_lanes.append(lane)
                if failures:
                    # A protocol/other error must not be hidden by a concurrent rate limit.
                    raise next((exc for exc in failures if not _capacity_error(exc)), failures[0])
                for lane in free_lanes:
                    if next_index < len(jobs):
                        submit(lane)
        except Exception as exc:
            stop.set()
            error = exc
        finally:
            stop.set()
            pool.shutdown(wait=True, cancel_futures=True)
        self.check_task_deadline()
        if error is not None:
            if not _capacity_error(error):
                raise error
            for future, (index, _lane) in pending.items():
                if future.cancelled():
                    continue
                failure = future.exception()
                if failure is not None and not isinstance(failure, AITaskCancelled) and not _capacity_error(failure):
                    raise failure
                if failure is None:
                    self.check_task_deadline()
                    accept(jobs[index], future.result())
                    completed.add(index)
            self._downgrade("capacity")
            for index, job in enumerate(jobs):
                if index in completed:
                    continue
                self.check_task_deadline()
                result = work(self, job)
                self.check_task_deadline()
                accept(job, result)

    def close(self):
        clients, self._clients = self._clients, []
        for client in clients:
            client.cleanup()

    def finish(self, state):
        self.close()
        publish_progress(self.callback, MemoryProgress("summary", state=state,
            run_id=self.run_id, elapsed_ms=(perf_counter() - self.started) * 1000,
            request_count=self.requests, probe_count=self.probes, retry_count=self.retries,
            cache_hits=self.cache_hits, round_number=self.rounds,
            stage_times=tuple(self.stage_times.items()), parallelism=self.parallelism,
            fallback_count=self.fallbacks))


def _capacity_error(error):
    if not isinstance(error, RuntimeError):
        return False
    text = str(error).casefold()
    return any(marker in text for marker in ("http 429", "status 429", "429 too many",
        "too many requests", "rate limit", "限流", "concurrency limit", "并发限制",
        "并发上限", "sqlite_busy", "database is locked"))


class _Cancellation:
    def __init__(self, *events):
        self.events = tuple(event for event in events if event is not None)

    def is_set(self):
        return any(event.is_set() for event in self.events)


class _WorkerSession:
    def __init__(self, parent, client, stop):
        self.parent, self.client, self.stop = parent, client, stop

    def __getattr__(self, name):
        return getattr(self.parent, name)

    def check_task_deadline(self):
        self.parent.check_task_deadline()
        if self.stop.is_set():
            raise AITaskCancelled()

    def generate_json(self, system_prompt, user_prompt, **options):
        return self.parent._generate(self.client, self.stop, system_prompt, user_prompt, options)
