"""Concurrent NDJSON reader with serialized application-service execution."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import logging
import threading
from typing import BinaryIO, Callable

from sidecar.application import ApplicationResult, SidecarApplication
from sidecar.protocol import (
    MAX_MESSAGE_BYTES,
    RPC_PROTOCOL_VERSION,
    ProtocolFault,
    RequestEnvelope,
    RequestId,
    encode_message,
    error_response,
    event_message,
    is_request_id,
    parse_request_line,
    request_id_hint,
    success_response,
)


LOGGER = logging.getLogger("novalist.sidecar")


@dataclass(frozen=True)
class ScheduledOutcome:
    request: RequestEnvelope
    result: ApplicationResult | None = None
    fault: ProtocolFault | None = None


@dataclass(frozen=True)
class CancelResult:
    accepted: bool
    state: str


class RequestScheduler:
    """Serialize project operations while allowing immediate queued cancellation."""

    def __init__(
        self,
        handler: Callable[[RequestEnvelope], ApplicationResult],
        completion: Callable[[ScheduledOutcome], None],
    ) -> None:
        self._handler = handler
        self._completion = completion
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="novalist-sidecar",
        )
        self._lock = threading.Lock()
        self._jobs: dict[tuple[type, RequestId], Future[ScheduledOutcome]] = {}
        self._closed = False

    def submit(self, request: RequestEnvelope) -> None:
        key = _request_key(request.request_id)
        with self._lock:
            if self._closed:
                raise ProtocolFault("SIDECAR_STOPPING", "Sidecar 正在关闭。")
            if key in self._jobs:
                raise ProtocolFault(
                    "DUPLICATE_REQUEST_ID",
                    "存在尚未完成的同名请求。",
                    {"id": request.request_id},
                )
            future = self._executor.submit(self._execute, request)
            self._jobs[key] = future
        future.add_done_callback(
            lambda completed, item=request: self._finish(item, completed)
        )

    def cancel(self, request_id: RequestId) -> CancelResult:
        key = _request_key(request_id)
        with self._lock:
            future = self._jobs.get(key)
            if future is None:
                return CancelResult(False, "notFound")
        if future.cancel():
            return CancelResult(True, "cancelled")
        if future.running():
            return CancelResult(False, "running")
        return CancelResult(False, "finished")

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
        # EOF and graceful shutdown must finish every request already accepted.
        # Individual queued work is cancelled only through request.cancel.
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _execute(self, request: RequestEnvelope) -> ScheduledOutcome:
        try:
            return ScheduledOutcome(request, result=self._handler(request))
        except ProtocolFault as exc:
            if exc.code == "INTERNAL_ERROR":
                LOGGER.exception("Internal sidecar request failure: %s", request.method)
            return ScheduledOutcome(request, fault=exc)
        except Exception:
            LOGGER.exception("Unhandled sidecar request failure: %s", request.method)
            return ScheduledOutcome(
                request,
                fault=ProtocolFault("INTERNAL_ERROR", "Sidecar 内部错误。"),
            )

    def _finish(
        self,
        request: RequestEnvelope,
        future: Future[ScheduledOutcome],
    ) -> None:
        key = _request_key(request.request_id)
        with self._lock:
            self._jobs.pop(key, None)
        if future.cancelled():
            outcome = ScheduledOutcome(
                request,
                fault=ProtocolFault("REQUEST_CANCELLED", "请求已取消。"),
            )
        else:
            outcome = future.result()
        self._completion(outcome)


class ProtocolWriter:
    """Thread-safe stdout writer that emits protocol messages only."""

    def __init__(self, output: BinaryIO) -> None:
        self._output = output
        self._lock = threading.Lock()

    def write(self, message: dict) -> None:
        payload = encode_message(message)
        with self._lock:
            self._output.write(payload)
            self._output.flush()


class SidecarServer:
    """Read requests until EOF or an explicit graceful shutdown command."""

    def __init__(
        self,
        application: SidecarApplication,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
    ) -> None:
        self.application = application
        self.input_stream = input_stream
        self.writer = ProtocolWriter(output_stream)
        self.application.set_event_sink(
            lambda name, data: self.writer.write(event_message(name, data))
        )
        self.scheduler = RequestScheduler(application.dispatch, self._complete)
        self._stopping = False

    def serve(self) -> int:
        self.writer.write(
            event_message(
                "sidecar.ready",
                {
                    "protocolVersion": RPC_PROTOCOL_VERSION,
                    "transport": "ndjson-stdio",
                },
            )
        )
        try:
            while not self._stopping:
                line = self.input_stream.readline(MAX_MESSAGE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_MESSAGE_BYTES:
                    self._drain_oversized_line(line)
                    self.writer.write(
                        error_response(
                            None,
                            ProtocolFault(
                                "MESSAGE_TOO_LARGE",
                                f"消息超过 {MAX_MESSAGE_BYTES} 字节限制。",
                            ),
                        )
                    )
                    continue
                self._accept_line(line)
        finally:
            self.scheduler.shutdown(wait=True)
            self.application.shutdown()
        return 0

    def _accept_line(self, line: bytes) -> None:
        request_id = request_id_hint(line)
        try:
            request = parse_request_line(line)
            if request.method == "request.cancel":
                self._cancel(request)
                return
            if request.method == "system.shutdown":
                self._shutdown(request)
                return
            self.scheduler.submit(request)
        except ProtocolFault as exc:
            self.writer.write(error_response(request_id, exc))

    def _cancel(self, request: RequestEnvelope) -> None:
        unknown = set(request.params) - {"targetId"}
        target_id = request.params.get("targetId")
        if unknown or not is_request_id(target_id):
            raise ProtocolFault(
                "INVALID_PARAMS",
                "request.cancel 需要有效的 targetId。",
                {"fields": sorted(unknown)} if unknown else None,
            )
        result = self.scheduler.cancel(target_id)
        self.writer.write(
            success_response(
                request.request_id,
                {
                    "targetId": target_id,
                    "accepted": result.accepted,
                    "state": result.state,
                },
            )
        )

    def _shutdown(self, request: RequestEnvelope) -> None:
        if request.params:
            raise ProtocolFault(
                "INVALID_PARAMS", "system.shutdown 不接受参数。"
            )
        self.writer.write(
            success_response(request.request_id, {"shuttingDown": True})
        )
        self.writer.write(event_message("sidecar.stopping"))
        self._stopping = True

    def _complete(self, outcome: ScheduledOutcome) -> None:
        if outcome.fault is not None:
            self.writer.write(
                error_response(outcome.request.request_id, outcome.fault)
            )
            return
        assert outcome.result is not None
        self.writer.write(
            success_response(outcome.request.request_id, outcome.result.result)
        )
        for event in outcome.result.events:
            self.writer.write(event_message(event.name, event.data))

    def _drain_oversized_line(self, first_chunk: bytes) -> None:
        chunk = first_chunk
        while chunk and not chunk.endswith(b"\n"):
            chunk = self.input_stream.readline(MAX_MESSAGE_BYTES + 1)


def _request_key(request_id: RequestId) -> tuple[type, RequestId]:
    return type(request_id), request_id
