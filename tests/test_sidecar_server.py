import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from sidecar.application import ApplicationResult
from sidecar.protocol import RequestEnvelope, encode_message
from sidecar.server import RequestScheduler


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PROJECT = (
    REPOSITORY_ROOT
    / "tests"
    / "fixtures"
    / "electron_migration"
    / "golden_project"
)


def _request_line(request_id, method, params=None, version=1) -> bytes:
    return encode_message(
        {
            "type": "request",
            "protocolVersion": version,
            "id": request_id,
            "method": method,
            "params": params or {},
        }
    )


def _run_sidecar(payload: bytes) -> tuple[list[dict], bytes, int]:
    completed = subprocess.run(
        [sys.executable, "-m", "sidecar"],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=REPOSITORY_ROOT,
        timeout=10,
        check=False,
    )
    messages = [
        json.loads(line)
        for line in completed.stdout.decode("utf-8").splitlines()
        if line
    ]
    return messages, completed.stderr, completed.returncode


class RequestSchedulerTests(unittest.TestCase):
    def test_queued_request_can_be_cancelled_without_execution(self) -> None:
        first_started = threading.Event()
        release_first = threading.Event()
        second_executed = threading.Event()
        outcomes = []
        outcomes_changed = threading.Event()

        def handler(request: RequestEnvelope) -> ApplicationResult:
            if request.request_id == "first":
                first_started.set()
                release_first.wait(2)
            else:
                second_executed.set()
            return ApplicationResult({"id": request.request_id})

        def completed(outcome) -> None:
            outcomes.append(outcome)
            outcomes_changed.set()

        scheduler = RequestScheduler(handler, completed)
        try:
            scheduler.submit(RequestEnvelope("first", "test.block", {}))
            self.assertTrue(first_started.wait(1))
            scheduler.submit(RequestEnvelope("second", "test.quick", {}))

            cancelled = scheduler.cancel("second")

            self.assertTrue(cancelled.accepted)
            self.assertEqual(cancelled.state, "cancelled")
            self.assertTrue(outcomes_changed.wait(1))
            cancelled_outcome = next(
                item for item in outcomes if item.request.request_id == "second"
            )
            self.assertEqual(cancelled_outcome.fault.code, "REQUEST_CANCELLED")
            self.assertFalse(second_executed.is_set())
        finally:
            release_first.set()
            scheduler.shutdown(wait=True)


class SidecarProcessTests(unittest.TestCase):
    def test_handshake_over_stdio_emits_only_ndjson_on_stdout(self) -> None:
        messages, stderr, returncode = _run_sidecar(
            _request_line(
                "hello",
                "system.handshake",
                {"clientName": "contract-test", "clientVersion": "1"},
            )
        )

        self.assertEqual(returncode, 0)
        self.assertEqual(stderr, b"")
        self.assertEqual(messages[0]["event"], "sidecar.ready")
        response = next(item for item in messages if item.get("id") == "hello")
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["protocolVersion"], 1)
        self.assertTrue(all(item["protocolVersion"] == 1 for item in messages))

    def test_malformed_and_version_mismatch_requests_are_recoverable(self) -> None:
        messages, _stderr, returncode = _run_sidecar(
            b"{broken json\n"
            + _request_line("future", "system.handshake", version=99)
        )

        self.assertEqual(returncode, 0)
        errors = [item for item in messages if item.get("ok") is False]
        self.assertEqual(errors[0]["error"]["code"], "INVALID_JSON")
        self.assertIsNone(errors[0]["id"])
        self.assertEqual(errors[1]["id"], "future")
        self.assertEqual(
            errors[1]["error"]["code"], "PROTOCOL_VERSION_MISMATCH"
        )

    def test_explicit_shutdown_is_acknowledged(self) -> None:
        messages, stderr, returncode = _run_sidecar(
            _request_line("bye", "system.shutdown")
        )

        self.assertEqual(returncode, 0)
        self.assertEqual(stderr, b"")
        response = next(item for item in messages if item.get("id") == "bye")
        self.assertEqual(response["result"], {"shuttingDown": True})
        self.assertEqual(messages[-1]["event"], "sidecar.stopping")

    def test_golden_project_and_document_cross_the_real_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "golden_project"
            shutil.copytree(FIXTURE_PROJECT, copied)
            messages, stderr, returncode = _run_sidecar(
                _request_line("project", "project.open", {"path": str(copied)})
                + _request_line(
                    "document",
                    "document.open",
                    {
                        "category": "章节",
                        "path": "outline/chapters/chapter_02.md",
                    },
                )
            )

        self.assertEqual(returncode, 0)
        self.assertEqual(stderr, b"")
        responses = {item.get("id"): item for item in messages if "id" in item}
        self.assertTrue(responses["project"]["ok"])
        self.assertTrue(responses["document"]["ok"], responses["document"])
        self.assertEqual(
            responses["document"]["result"]["document"]["relativePath"],
            "outline/chapters/chapter_02.md",
        )

    def test_cancel_of_unknown_request_is_an_acknowledged_noop(self) -> None:
        messages, _stderr, returncode = _run_sidecar(
            _request_line(
                "cancel",
                "request.cancel",
                {"targetId": "missing"},
            )
        )

        self.assertEqual(returncode, 0)
        response = next(item for item in messages if item.get("id") == "cancel")
        self.assertEqual(
            response["result"],
            {"targetId": "missing", "accepted": False, "state": "notFound"},
        )


if __name__ == "__main__":
    unittest.main()
