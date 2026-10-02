"""Bounded memory scheduling, source ordering and isolated transport contracts."""
import json
import re
import sys
import tempfile
import threading
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from core.ai_workflow import AIWorkflowService
from core.chapter_facts import extract_chapter_fact_ledger
from core.chapter_memory import ChapterMemoryCache, generate_chapter_memory_proposal
from core.config import normalize_config
from core.dsh_client import DSHClient
from core.memory_task import MemoryTaskSession
from core.project import NovelProject
from core.task_controller import AITaskCancelled
from core.token_budget import DEFAULT_TOKEN_ESTIMATOR as EST
from tests.test_chapter_facts import _LedgerDSH
from tests.test_chapter_memory import base_state
from tests.test_memory_budget_policy import dense_ledger, snapshot
from tests.test_memory_checkpoints import ReducingDSH


class FakeFactory:
    input_token_budget = 24000
    token_estimator = EST

    def __init__(self, hook=None):
        self.hook = hook
        self.lock = threading.Lock()
        self.active = self.maximum = 0
        self.busy = set()
        self.calls, self.clients, self.cleaned = [], [], []

    def fork_for_memory(self):
        client = FakeLane(self, len(self.clients))
        self.clients.append(client)
        return client

    def generate_json(self, system, user, **options):
        return self.generate(-1, system, user, options)

    def generate(self, lane, system, user, options):
        report = options.get("context_report")
        unit = report.source_unit if report else user
        kind = report.task_kind if report else "job"
        with self.lock:
            if lane in self.busy:
                raise AssertionError("client used simultaneously")
            self.busy.add(lane)
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.calls.append((kind, unit, lane, report))
        try:
            if self.hook:
                self.hook(self, kind, unit, lane, options)
            if kind == "job":
                result = int(user)
            elif kind == "chapter_chunk_facts":
                result = _LedgerDSH().generate_json(system, user)
            else:
                result = ReducingDSH().generate_json(system, user)
            if report and options.get("invocation_callback"):
                options["invocation_callback"](replace(report, probe_count=1, file_ack_retry_count=1))
            return result
        finally:
            with self.lock:
                self.active -= 1
                self.busy.remove(lane)

    def cleanup(self):
        raise AssertionError("captured client must remain owned by caller")


class FakeLane:
    def __init__(self, root, lane):
        self.root, self.lane = root, lane

    def generate_json(self, system, user, **options):
        return self.root.generate(self.lane, system, user, options)

    def cleanup(self):
        if self.root.active:
            raise AssertionError("cleanup before workers joined")
        self.root.cleaned.append(self.lane)


class MemorySchedulerTests(TestCase):
    def session(self, factory, cancel=None, callback=None, concurrency=2):
        session = MemoryTaskSession(factory, 30, cancel, callback, concurrency)
        self.addCleanup(session.close)
        return session

    def work(self, worker, job):
        return worker.generate_json("", str(job))

    def test_two_independent_lanes_and_out_of_order_completion_are_bounded(self):
        gate, release = threading.Barrier(2), threading.Event()
        def hook(_root, _kind, unit, _lane, _options):
            if unit in {"0", "1"}:
                gate.wait(timeout=3)
            if unit == "0" and not release.wait(3):
                raise AssertionError("second result not committed")
        factory, accepted = FakeFactory(hook), []
        session = self.session(factory)
        def accept(job, result):
            accepted.append((job, result))
            if job == 1:
                release.set()
        session.run_jobs(range(8), self.work, accept)
        session.close()
        self.assertEqual(factory.maximum, 2)
        self.assertEqual(len(factory.clients), 2)
        self.assertEqual(sorted(accepted), [(i, i) for i in range(8)])
        self.assertLess([job for job, _ in accepted].index(1), [job for job, _ in accepted].index(0))
        self.assertEqual(sorted(factory.cleaned), [0, 1])

    def test_serial_default_and_single_job_do_not_create_clients(self):
        for count, concurrency in ((5, 1), (1, 2), (0, 2)):
            factory = FakeFactory()
            session = self.session(factory, concurrency=concurrency)
            session.run_jobs(range(count), self.work, lambda *_args: None)
            self.assertEqual(factory.clients, [])
            self.assertLessEqual(factory.maximum, 1)

    def test_failure_stops_queue_joins_peer_and_does_not_retry(self):
        gate, stopped = threading.Barrier(2), threading.Event()
        def hook(_root, _kind, unit, _lane, options):
            gate.wait(timeout=3)
            if unit == "0":
                raise ValueError("invalid evidence")
            for _ in range(200):
                if options["cancel_event"].is_set():
                    stopped.set()
                    raise AITaskCancelled()
                time.sleep(.005)
            raise AssertionError("peer never stopped")
        factory, accepted = FakeFactory(hook), []
        session = self.session(factory)
        with self.assertRaisesRegex(ValueError, "invalid evidence"):
            session.run_jobs(range(10), self.work, lambda *args: accepted.append(args))
        session.close()
        self.assertTrue(stopped.is_set())
        self.assertEqual({unit for _kind, unit, _lane, _report in factory.calls}, {"0", "1"})
        self.assertEqual((factory.active, accepted, session.fallbacks), (0, [], 0))

    def test_cancel_and_deadline_join_both_lanes_and_ignore_late_success(self):
        for outcome in ("cancel", "timeout"):
            with self.subTest(outcome=outcome):
                gate, clock, cancel = threading.Barrier(2), [0.0], threading.Event()
                def hook(_root, _kind, _unit, _lane, options):
                    gate.wait(timeout=3)
                    if outcome == "cancel":
                        cancel.set()
                    else:
                        clock[0] = 31.0
                    # Deliberately return successful data despite cancellation.
                    return
                factory, accepted = FakeFactory(hook), []
                with patch("core.memory_task.monotonic", side_effect=lambda: clock[0]):
                    session = self.session(factory, cancel)
                    with self.assertRaises(AITaskCancelled if outcome == "cancel" else RuntimeError):
                        session.run_jobs(range(5), self.work, lambda *args: accepted.append(args))
                session.close()
                self.assertEqual((factory.active, accepted), (0, []))
                self.assertEqual(len(factory.calls), 2)

    def test_capacity_rejection_retries_only_unfinished_jobs_once_serially(self):
        gate, accepted_zero = threading.Barrier(2), threading.Event()
        counts = Counter()
        def hook(root, _kind, unit, _lane, options):
            counts[unit] += 1
            if unit in {"0", "1"} and counts[unit] == 1:
                gate.wait(timeout=3)
            if unit == "1" and counts[unit] == 1:
                if not accepted_zero.wait(3):
                    raise AssertionError("first result was not saved")
                raise RuntimeError("HTTP 429 Too Many Requests")
            if unit not in {"0", "1"} and options.get("cancel_event") is not None:
                for _ in range(200):
                    if options["cancel_event"].is_set():
                        raise AITaskCancelled()
                    time.sleep(.005)
                raise AssertionError("peer never stopped")
            if options.get("cancel_event") is None:
                self.assertEqual(root.active, 1)
        factory, events, accepted = FakeFactory(hook), [], []
        session = self.session(factory, callback=events.append)
        def accept(job, result):
            accepted.append(job)
            if job == 0:
                accepted_zero.set()
        session.run_jobs(range(5), self.work, accept)
        self.assertEqual(sorted(accepted), list(range(5)))
        self.assertEqual((counts["0"], counts["1"], session.parallelism, session.fallbacks), (1, 2, 1, 1))
        self.assertEqual(len([e for e in events if e.state == "fallback"]), 1)
        # The whole run stays serial, including subsequent independent batches.
        previous = factory.maximum
        session.run_jobs([6, 7], self.work, lambda *_args: None)
        self.assertEqual(factory.maximum, previous)

    def test_second_capacity_rejection_stops_without_an_unbounded_retry(self):
        gate = threading.Barrier(2)
        counts = Counter()
        def hook(_root, _kind, unit, _lane, _options):
            counts[unit] += 1
            if counts[unit] == 1:
                gate.wait(timeout=3)
            if unit == "0":
                raise RuntimeError("concurrency limit")
        session = self.session(FakeFactory(hook))
        with self.assertRaisesRegex(RuntimeError, "concurrency limit"):
            session.run_jobs([0, 1], self.work, lambda *_args: None)
        self.assertEqual(counts["0"], 2)
        self.assertEqual(session.fallbacks, 1)

    def test_client_creation_failure_cleans_partial_pool_and_falls_back(self):
        factory = FakeFactory()
        fork = factory.fork_for_memory
        def fail_second():
            if factory.clients:
                raise OSError("cannot create private directory")
            return fork()
        factory.fork_for_memory = fail_second
        session, accepted = self.session(factory), []
        session.run_jobs([1, 2, 3], self.work, lambda job, _result: accepted.append(job))
        self.assertEqual((accepted, factory.cleaned, factory.maximum), ([1, 2, 3], [0], 1))
        self.assertEqual(session.parallelism, 1)

    def test_shared_client_factory_is_rejected_without_cleaning_captured_client(self):
        factory = FakeFactory()
        factory.fork_for_memory = lambda: factory
        session = self.session(factory)
        session.run_jobs([1, 2], self.work, lambda *_args: None)
        self.assertEqual((factory.maximum, factory.clients, session.parallelism), (1, [], 1))

    def test_concurrency_config_is_bounded_and_defaults_to_serial(self):
        for raw, expected in ((None, 1), ("bad", 1), (-1, 1), (2, 2), (100, 2)):
            self.assertEqual(normalize_config({"ai_memory_concurrency": raw})["ai_memory_concurrency"], expected)


class MemoryParallelWorkflowTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = NovelProject.create(self.root / "project", "并发临时测试")
        content = "\n\n".join(f"段落{i}。" + "字" * 600 for i in range(10))
        self.project.save_chapter("chapter_01", outline="", content=content)
        self.before = snapshot(self.project)

    def extract(self, factory, cache, events):
        session = MemoryTaskSession(factory, 30, None, events.append, 2)
        try:
            return extract_chapter_fact_ledger(self.project, "chapter_01", session,
                input_token_budget=24000, chunk_token_budget=1000, overlap_tokens=0,
                cache=cache, progress_callback=session.progress)
        finally:
            session.finish("done")

    def test_fact_completion_order_does_not_change_ledger_and_warm_run_needs_no_pool(self):
        from core.chapter_facts import FactLedgerCache
        release = threading.Event()
        def hook(_root, kind, unit, _lane, _options):
            if kind == "chapter_chunk_facts" and ":c0001:" in unit:
                if not release.wait(3):
                    raise AssertionError("second chunk was not saved")
        events = []
        def progress(event):
            events.append(event)
            if event.stage == "facts" and event.state == "done" and event.current == 1:
                release.set()
        cache = FactLedgerCache(self.project, self.root / "facts")
        factory = FakeFactory(hook)
        session = MemoryTaskSession(factory, 30, None, progress, 2)
        try:
            parallel = extract_chapter_fact_ledger(self.project, "chapter_01", session,
                chunk_token_budget=1000, overlap_tokens=0, cache=cache, progress_callback=session.progress)
        finally:
            session.finish("done")
        serial = extract_chapter_fact_ledger(self.project, "chapter_01", _LedgerDSH(),
            chunk_token_budget=1000, overlap_tokens=0,
            cache=FactLedgerCache(self.project, self.root / "serial"))
        self.assertEqual(parallel.facts, serial.facts)
        self.assertEqual(parallel.chunks, serial.chunks)
        done = [e.current for e in events if e.stage == "facts" and e.state == "done"]
        self.assertEqual(done, list(range(1, len(parallel.chunks) + 1)))
        self.assertEqual(factory.maximum, 2)
        reports = [report for _kind, _unit, _lane, report in factory.calls]
        self.assertEqual(len({r.request_index for r in reports}), len(reports))
        self.assertEqual(len({r.memory_run_id for r in reports}), 1)
        self.assertEqual(events[-1].request_count, len(reports))
        self.assertEqual((events[-1].probe_count, events[-1].retry_count), (len(reports), len(reports)))
        warm_factory, warm_events = FakeFactory(), []
        warm = self.extract(warm_factory, cache, warm_events)
        self.assertEqual((warm.cache_hits, len(warm_factory.clients), warm_factory.calls), (len(parallel.chunks), 0, []))
        self.assertEqual(snapshot(self.project), self.before)

    def test_parallel_reduction_uses_ordered_checkpoints_and_final_proposal_waits(self):
        release, events = threading.Event(), []
        def hook(root, kind, unit, _lane, _options):
            if kind == "chapter_digest_shard" and unit.endswith("batch:1"):
                if not release.wait(3):
                    raise AssertionError("second batch was not saved")
            if kind == "chapter_memory_proposal":
                self.assertEqual(root.active, 1)
        def progress(event):
            events.append(event)
            if event.stage == "reduction" and event.state == "done" and event.current == 1:
                release.set()
        cache = ChapterMemoryCache(self.project, self.root / "memory")
        factory = FakeFactory(hook)
        session = MemoryTaskSession(factory, 30, None, progress, 2)
        try:
            result = generate_chapter_memory_proposal(self.project, dense_ledger(), session,
                base_state=base_state(), input_token_budget=4000, reduce_batch_tokens=1000,
                cache=cache, progress_callback=session.progress)
        finally:
            session.finish("done")
        self.assertEqual(factory.maximum, 2)
        self.assertEqual(factory.calls[-1][0], "chapter_memory_proposal")
        self.assertEqual(len(list(cache.project_dir.rglob("batch-*.json"))), result.reduction_calls)
        from core.chapter_memory import reduction_config_fingerprint
        reduced = cache.load_reduction(dense_ledger(), reduction_config_fingerprint(factory))
        first_ids = reduced["shards"][0]["claims"][0]["fact_ids"]
        self.assertEqual(first_ids[0], "fact_0000")
        self.assertEqual(snapshot(self.project), self.before)

    def test_parallel_failure_preserves_only_completed_fact_and_batch_checkpoints(self):
        from core.chapter_facts import FactLedgerCache
        for stage in ("facts", "reduction"):
            with self.subTest(stage=stage):
                accepted_first = threading.Event()
                def hook(_root, _kind, unit, _lane, options):
                    first = ":c0001:" in unit if stage == "facts" else unit.endswith("batch:1")
                    second = ":c0002:" in unit if stage == "facts" else unit.endswith("batch:2")
                    if first:
                        return
                    if second:
                        if not accepted_first.wait(3):
                            raise AssertionError("first checkpoint was not committed")
                        raise RuntimeError("模拟当前批次失败")
                    for _ in range(200):
                        if options["cancel_event"].is_set():
                            return  # successful but late payload is deliberately discarded
                        time.sleep(.005)
                    raise AssertionError("peer was not cancelled")
                def progress(event):
                    if event.stage == stage and event.state == "done" and event.current == 1:
                        accepted_first.set()
                factory = FakeFactory(hook)
                session = MemoryTaskSession(factory, 30, None, progress, 2)
                cache = (FactLedgerCache(self.project, self.root / stage) if stage == "facts"
                    else ChapterMemoryCache(self.project, self.root / stage))
                def run(current):
                    if stage == "facts":
                        return extract_chapter_fact_ledger(self.project, "chapter_01", current,
                            chunk_token_budget=1000, overlap_tokens=0,
                            cache=cache, progress_callback=current.progress)
                    return generate_chapter_memory_proposal(self.project, dense_ledger(), current,
                        base_state=base_state(), input_token_budget=4000, reduce_batch_tokens=1000,
                        cache=cache, progress_callback=current.progress)
                try:
                    with self.assertRaisesRegex(RuntimeError, "当前批次失败"):
                        run(session)
                finally:
                    session.finish("failed")
                self.assertEqual(len(list(cache.project_dir.rglob("*.json"))), 1)
                first_unit = factory.calls[0][1]
                retry_factory, events = FakeFactory(), []
                retry = MemoryTaskSession(retry_factory, 30, None, events.append, 2)
                try:
                    run(retry)
                finally:
                    retry.finish("done")
                self.assertNotIn(first_unit, [unit for _kind, unit, _lane, _report in retry_factory.calls])
                self.assertEqual(sum(e.state == "cached" and e.stage == stage for e in events), 1)
                self.assertEqual(snapshot(self.project), self.before)

    def test_workflow_finishes_all_facts_before_proposal_and_closes_owned_clients(self):
        gate = threading.Barrier(2)
        def hook(root, kind, _unit, _lane, _options):
            if kind == "chapter_chunk_facts" and len(root.calls) <= 2:
                gate.wait(timeout=3)
            if kind == "chapter_memory_proposal":
                self.assertEqual(root.active, 1)
        factory = FakeFactory(hook)
        workflow = AIWorkflowService(factory, memory_concurrency=2, chunk_token_budget=1000,
            chunk_overlap_tokens=0, fact_cache_root=self.root / "facts", memory_cache_root=self.root / "memory")
        workflow.update_memory(self.project, "chapter_01")
        self.assertEqual(factory.calls[-1][0], "chapter_memory_proposal")
        self.assertEqual(sorted(factory.cleaned), [0, 1])
        self.assertEqual(snapshot(self.project), self.before)


class MemoryClientIsolationTests(TestCase):
    def test_cancel_terminates_two_real_process_trees_before_clients_are_released(self):
        # A local Python stub exercises the actual adapter process lifecycle;
        # it reads only temporary task files and never contacts a model/service.
        import os
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_engine.py"
            script.write_text('''import json, os, re, subprocess, sys, time
from pathlib import Path
name = re.search(r"(\\.novalist-task-[a-f0-9]+\\.md)", sys.argv[-1]).group(1)
payload = Path(name).read_text(encoding="utf-8")
head = re.search(r"task_nonce_head: ([A-F0-9]+)", payload).group(1)
middle = re.search(r"task_nonce_middle=([A-F0-9]+)", payload).group(1)
tail = re.search(r"task_nonce_tail: ([A-F0-9]+)", payload).group(1)
marker = re.search(r"NOVALIST_FILE_PROBE_[A-F0-9]+", payload)
if marker:
    print("NOVALIST_FILE_ACK:" + head + ":" + middle + ":" + tail)
    print(marker.group(0))
else:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    Path("live-pids.json").write_text(json.dumps([os.getpid(), child.pid]))
    time.sleep(120)
''', encoding="utf-8")
            client = DSHClient(dsh_command=sys.executable, launcher_args=[str(script)])
            self.addCleanup(client.cleanup)
            cancel, observed, errors = threading.Event(), set(), []
            session = MemoryTaskSession(client, 30, cancel, None, 2)
            def watch():
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    for lane in list(session._clients):
                        try:
                            observed.update(json.loads((lane.working_directory / "live-pids.json").read_text()))
                        except (OSError, json.JSONDecodeError):
                            pass
                    if len(observed) == 4:
                        cancel.set()
                        return
                    time.sleep(.01)
                errors.append("two process trees never started")
                cancel.set()
            watcher = threading.Thread(target=watch, daemon=True)
            watcher.start()
            try:
                with self.assertRaises(AITaskCancelled):
                    session.run_jobs([0, 1, 2, 3], lambda worker, job: worker.generate_json("", str(job)),
                        lambda *_args: self.fail("cancelled result was accepted"))
                directories = [lane.working_directory for lane in session._clients]
            finally:
                cancel.set()
                watcher.join(12)
                session.close()
            self.assertEqual(errors, [])
            self.assertEqual((len(observed), session.requests), (4, 2))
            self.assertTrue(all(not directory.exists() for directory in directories))
            if os.name == "nt":
                import ctypes
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
                kernel.OpenProcess.restype = ctypes.c_void_p
                kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                for pid in observed:
                    handle = kernel.OpenProcess(0x00100000, False, pid)
                    if handle:
                        try:
                            self.assertNotEqual(kernel.WaitForSingleObject(handle, 0), 0x102)
                        finally:
                            kernel.CloseHandle(handle)
            else:
                for pid in observed:
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)

    def test_fork_snapshots_options_and_never_copies_probe_or_workspace(self):
        client = DSHClient(launcher_args=["launch"], extra_args=["--model", "captured"], timeout=99,
            input_token_budget=7000, model_context_window_tokens=10000, runtime_reserve_tokens=1000)
        client.use_isolated_workspace()
        self.addCleanup(client.cleanup)
        client._file_transport_supported = True
        forks = [client.fork_for_memory(), client.fork_for_memory()]
        for fork in forks:
            self.addCleanup(fork.cleanup)
            self.assertIsNone(fork._file_transport_supported)
            self.assertEqual((fork.timeout, fork.token_budget), (client.timeout, client.token_budget))
        self.assertEqual(len({c.working_directory for c in [client, *forks]}), 3)
        forks[0].extra_args.append("changed")
        forks[0].launcher_args.append("changed")
        self.assertEqual(client.extra_args, ["--model", "captured"])
        self.assertEqual(forks[1].launcher_args, ["launch"])

    def test_real_client_transport_reports_are_distinct_and_workspace_cleanup_follows_join(self):
        from tests.test_dsh_client import current_task_ack
        from core.chapter_facts import build_chunk_facts_prompt
        from core.text_chunking import chunk_chapter
        client = DSHClient()
        self.addCleanup(client.cleanup)
        gate, paths, reports = threading.Barrier(2), set(), []
        def execute(lane, _prompt, **_options):
            path = next(lane.working_directory.glob(".novalist-task-*.md"))
            payload = path.read_text("utf-8")
            paths.add(path)
            marker = re.search(r"NOVALIST_FILE_PROBE_[A-F0-9]+", payload)
            if marker:
                return current_task_ack(lane) + "\n" + marker.group(0)
            gate.wait(timeout=3)
            # Strip only the task challenge; no actual model runs here.
            payload = re.sub(r"\[NOVALIST_MIDDLE_CHALLENGE[^\n]*\]", "", payload)
            return current_task_ack(lane) + "\n" + json.dumps(_LedgerDSH().generate_json("", payload), ensure_ascii=False)
        session = MemoryTaskSession(client, 30, None, None, 2, reports.append)
        chunks = [chunk_chapter("chapter_01", "临时正文一。")[0], chunk_chapter("chapter_02", "临时正文二。")[0]]
        prompts = [build_chunk_facts_prompt("测试", chunk) for chunk in chunks]
        accepted = []
        with patch.object(DSHClient, "_execute_prompt", execute):
            session.run_jobs(prompts, lambda worker, prompt: worker.generate_json(prompt.system_prompt,
                prompt.user_prompt, context_report=prompt.report), lambda _job, value: accepted.append(value))
        directories = {path.parent for path in paths}
        self.assertEqual((len(paths), len(directories), len(reports), session.probes), (4, 2, 2, 2))
        self.assertEqual({r.memory_run_id for r in reports}, {session.run_id})
        self.assertEqual({r.request_index for r in reports}, {1, 2})
        self.assertTrue(all(r.task_file_cleaned and r.file_ack_verified for r in reports))
        self.assertEqual({item["chapter_id"] for item in accepted}, {"chapter_01", "chapter_02"})
        session.close()
        self.assertTrue(all(not path.exists() for path in directories))
