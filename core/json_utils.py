"""Shared tolerant JSON extraction for DSh text responses."""

from __future__ import annotations

import json
import re
from typing import Any


class JSONExtractionError(ValueError):
    """Raised when a response contains no decodable JSON value."""


def extract_json(text: str) -> Any:
    """Extract a JSON object/array from plain or fenced assistant output."""
    value_text = str(text or "").strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", value_text, re.IGNORECASE)
    if fence_match:
        value_text = fence_match.group(1).strip()

    try:
        return json.loads(value_text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(value_text):
            if character not in "[{":
                continue
            try:
                value, _end = decoder.raw_decode(value_text[index:])
            except json.JSONDecodeError:
                continue
            return value
    raise JSONExtractionError("返回内容不是合法 JSON。")
