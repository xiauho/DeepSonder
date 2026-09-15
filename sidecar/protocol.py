"""NDJSON message validation and serialization for the local sidecar."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


RPC_PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 16 * 1024 * 1024
RequestId = str | int


class _DuplicateKeyError(ValueError):
    pass


@dataclass(frozen=True)
class ProtocolFault(Exception):
    """A renderer-safe error that can be serialized without a traceback."""

    code: str
    message: str
    data: dict[str, Any] | None = None
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class RequestEnvelope:
    request_id: RequestId
    method: str
    params: dict[str, Any]


@dataclass(frozen=True)
class EventMessage:
    name: str
    data: dict[str, Any]


def parse_request_line(line: bytes) -> RequestEnvelope:
    """Decode and validate exactly one request line."""
    if len(line) > MAX_MESSAGE_BYTES:
        raise ProtocolFault(
            "MESSAGE_TOO_LARGE",
            f"消息超过 {MAX_MESSAGE_BYTES} 字节限制。",
        )
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolFault("INVALID_ENCODING", "消息必须使用 UTF-8 编码。") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        data = (
            {"line": exc.lineno, "column": exc.colno}
            if isinstance(exc, json.JSONDecodeError)
            else None
        )
        raise ProtocolFault(
            "INVALID_JSON",
            "消息不是有效的 JSON。",
            data,
        ) from exc
    if not isinstance(value, dict):
        raise ProtocolFault("INVALID_REQUEST", "请求必须是 JSON 对象。")

    request_id = value.get("id")
    if not _is_request_id(request_id):
        raise ProtocolFault("INVALID_REQUEST", "请求 id 必须是字符串或整数。")
    if value.get("type") != "request":
        raise ProtocolFault("INVALID_REQUEST", "消息 type 必须为 request。")
    version = value.get("protocolVersion")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != RPC_PROTOCOL_VERSION
    ):
        raise ProtocolFault(
            "PROTOCOL_VERSION_MISMATCH",
            "客户端与 Sidecar 协议版本不兼容。",
            {
                "received": version,
                "supported": RPC_PROTOCOL_VERSION,
            },
        )
    method = value.get("method")
    if not isinstance(method, str) or not method.strip():
        raise ProtocolFault("INVALID_REQUEST", "请求 method 必须是非空字符串。")
    params = value.get("params", {})
    if not isinstance(params, dict):
        raise ProtocolFault("INVALID_PARAMS", "请求 params 必须是 JSON 对象。")
    unknown = set(value) - {"type", "protocolVersion", "id", "method", "params"}
    if unknown:
        raise ProtocolFault(
            "INVALID_REQUEST",
            "请求包含未知字段。",
            {"fields": sorted(unknown)},
        )
    return RequestEnvelope(request_id, method.strip(), params)


def success_response(request_id: RequestId, result: Any) -> dict[str, Any]:
    return {
        "type": "response",
        "protocolVersion": RPC_PROTOCOL_VERSION,
        "id": request_id,
        "ok": True,
        "result": result,
    }


def error_response(
    request_id: RequestId | None,
    fault: ProtocolFault,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": fault.code,
        "message": fault.message,
        "retryable": bool(fault.retryable),
    }
    if fault.data is not None:
        error["data"] = fault.data
    return {
        "type": "response",
        "protocolVersion": RPC_PROTOCOL_VERSION,
        "id": request_id,
        "ok": False,
        "error": error,
    }


def event_message(name: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "event",
        "protocolVersion": RPC_PROTOCOL_VERSION,
        "event": str(name),
        "data": dict(data or {}),
    }


def encode_message(message: dict[str, Any]) -> bytes:
    """Serialize one compact JSON object terminated by one newline."""
    return (
        json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def is_request_id(value: object) -> bool:
    return _is_request_id(value)


def request_id_hint(line: bytes) -> RequestId | None:
    """Best-effort correlation id for errors raised before full validation."""
    try:
        value = json.loads(
            line.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    request_id = value.get("id")
    return request_id if _is_request_id(request_id) else None


def _is_request_id(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return False
    if isinstance(value, str):
        return 0 < len(value) <= 128
    return -(2**53) < value < 2**53


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError(f"duplicate key: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")
