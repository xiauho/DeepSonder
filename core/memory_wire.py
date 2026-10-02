"""Lossless request-local projection of memory facts for model transport.

Canonical evidence and anchors stay local. Only reference fields are decoded;
free text and patch values are never rewritten.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


MEMORY_WIRE_VERSION = 1
FACT_COLUMNS = ("fact_id", "category", "subject", "predicate", "value", "anchor", "certainty")
WIRE_LEGEND = (
    "事实传输版本：1。facts 的每行依次为 [短引用,category,subject,predicate,value,anchor,certainty]；"
    "chunks 每行为 [chunk_id,summary]；unknowns 每行为 [description,anchor]。"
    "输入项含 kind 时，其 record 使用上述列顺序。anchor 保留正文先后位置。"
    "所有 fact_ids/evidence_fact_ids 只能填写本次输入的短引用（如 f1），不能编造或沿用其他请求的引用。"
    "必须原样返回本次 wire_request_id。"
)


class MemoryWireError(ValueError):
    """An untrusted response does not belong to this request's evidence map."""


@dataclass(frozen=True)
class MemoryWirePayload:
    value: Any
    wire_request_id: str
    aliases: dict[str, str]

    def decode(self, response: dict[str, Any]) -> dict[str, Any]:
        if response.get("wire_request_id") != self.wire_request_id:
            raise MemoryWireError("事实短引用的 wire_request_id 与当前请求不一致。")

        def visit(value: Any) -> Any:
            if isinstance(value, list):
                return [visit(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = {}
            for key, item in value.items():
                if key in {"fact_ids", "evidence_fact_ids"}:
                    if not isinstance(item, list) or any(
                        not isinstance(ref, str) or ref not in self.aliases for ref in item
                    ):
                        raise MemoryWireError("返回内容引用了本次请求中不存在的事实短引用。")
                    result[key] = [self.aliases[ref] for ref in item]
                else:
                    result[key] = visit(item)
            return result

        return visit(response)


def encode_memory_source(value: Any, *, prompt_version: int) -> MemoryWirePayload:
    """Retain every semantic field and input order, replacing only redundancy."""
    ids: list[str] = []
    seen: set[str] = set()

    def collect(item: Any) -> None:
        if isinstance(item, dict):
            for key, field in item.items():
                refs = [field] if key == "fact_id" else (
                    field if key in {"fact_ids", "evidence_fact_ids"} and isinstance(field, list) else []
                )
                for ref in refs:
                    if isinstance(ref, str) and ref not in seen:
                        seen.add(ref)
                        ids.append(ref)
                collect(field)
        elif isinstance(item, list):
            for field in item:
                collect(field)

    collect(value)
    encode_ids = {ref: f"f{index}" for index, ref in enumerate(ids, 1)}

    def project(item: Any) -> Any:
        if isinstance(item, list):
            return [project(field) for field in item]
        if not isinstance(item, dict):
            return item
        record = None
        if all(column in item for column in FACT_COLUMNS) and set(item) <= {*FACT_COLUMNS, "kind"}:
            record = [encode_ids[item["fact_id"]], *[item[column] for column in FACT_COLUMNS[1:]]]
        elif "description" in item and "anchor" in item and set(item) <= {"description", "anchor", "kind"}:
            record = [item["description"], item["anchor"]]
        elif "chunk_id" in item and "summary" in item and set(item) <= {"chunk_id", "summary", "kind"}:
            record = [item["chunk_id"], item["summary"]]
        if record is not None:
            return {"kind": item["kind"], "record": record} if "kind" in item else record
        return {
            key: (encode_ids[field] if key == "fact_id" else
                  [encode_ids[ref] for ref in field] if key in {"fact_ids", "evidence_fact_ids"}
                  else project(field))
            for key, field in item.items()
        }

    fingerprint = json.dumps([MEMORY_WIRE_VERSION, prompt_version, value],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    request_id = "wire_" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]
    return MemoryWirePayload(project(value), request_id, {alias: ref for ref, alias in encode_ids.items()})
