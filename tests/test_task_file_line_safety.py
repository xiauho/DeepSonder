"""DSH task files must stay readable under read/grep line truncation.

DSH's read tool keeps only 2000 characters per line by default and grep has
the same limit without any configuration option. These tests pin the
DeepSonder-side guarantees that keep every task-file line safely below the
truncation point.
"""
import json
import re
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from core.dsh_client import (
    DSHClient,
    DSH_FILE_ACK_PREFIX,
    DSH_FILE_READ_FAILED,
    DSH_FILE_PROBE_PREFIX,
)
from core.memory_wire import render_wire_payload
from core.prompt_transport import (
    DSH_READ_LINE_TRUNCATION,
    TASK_FILE_SAFE_LINE_CHARS,
    task_file_envelope,
    wrap_long_lines,
)


def max_line_length(text: str) -> int:
    return max((len(line) for line in text.split("\n")), default=0)


class WrapLongLinesTests(TestCase):
    def test_long_lines_are_split_losslessly(self) -> None:
        original = "x" * 5000
        wrapped = wrap_long_lines(original)
        self.assertEqual("".join(wrapped.split("\n")), original)
        self.assertLessEqual(max_line_length(wrapped), TASK_FILE_SAFE_LINE_CHARS)

    def test_short_lines_are_untouched(self) -> None:
        text = "第一行\nshort line\n\n最后一行"
        self.assertEqual(wrap_long_lines(text), text)

    def test_limit_line_length_is_preserved_exactly(self) -> None:
        line = "y" * TASK_FILE_SAFE_LINE_CHARS
        self.assertEqual(wrap_long_lines(line), line)


class TaskFileEnvelopeTests(TestCase):
    def test_envelope_keeps_every_line_below_dsh_truncation(self) -> None:
        prompt = "头部\n" + "z" * 30_000 + "\n尾部"
        envelope, _ = task_file_envelope(
            prompt, "t" * 32, "a" * 32, "b" * 32, "c" * 32
        )
        self.assertLessEqual(max_line_length(envelope), TASK_FILE_SAFE_LINE_CHARS)
        self.assertLess(TASK_FILE_SAFE_LINE_CHARS, DSH_READ_LINE_TRUNCATION)
        self.assertIn("payload_max_line_chars: ", envelope)
        reported = int(
            re.search(r"^payload_max_line_chars: (\d+)$", envelope, re.MULTILINE).group(1)
        )
        self.assertLessEqual(reported, TASK_FILE_SAFE_LINE_CHARS)

    def test_envelope_reports_real_max_line_for_short_payloads(self) -> None:
        envelope, _ = task_file_envelope(
            "短任务", "t" * 32, "a" * 32, "b" * 32, "c" * 32
        )
        reported = int(
            re.search(r"^payload_max_line_chars: (\d+)$", envelope, re.MULTILINE).group(1)
        )
        self.assertGreater(reported, 0)


class RenderWirePayloadTests(TestCase):
    def test_wire_payload_renders_valid_json_one_record_per_line(self) -> None:
        record = ["f1", "state", "林凡", "location", "山门前空地",
                  "chapter_01:p0012", "explicit"]
        value = {
            "facts": [record] * 120,
            "unknowns": [["去向未明", "chapter_01:p0002"]],
            "chunks": [["chunk_01", "首段摘要"]],
        }
        rendered = render_wire_payload(value)
        self.assertEqual(json.loads(rendered), value)
        lines = rendered.split("\n")
        # Every fact record occupies exactly one line (trailing commas included).
        record_line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        record_lines = sum(
            1 for line in lines if line in (record_line, record_line + ",")
        )
        self.assertEqual(record_lines, 120)
        self.assertLess(max_line_length(rendered), 200)

    def test_nested_containers_explode_but_scalar_records_stay_compact(self) -> None:
        value = {"shards": [
            {"summary": "批次摘要", "claims": [
                {"text": "林凡到达山门前空地", "fact_ids": ["f1", "f2"]},
                {"text": "众人休整", "fact_ids": ["f3"]},
            ]},
        ]}
        rendered = render_wire_payload(value)
        self.assertEqual(json.loads(rendered), value)
        self.assertIn('\n"text": "林凡到达山门前空地"', rendered)
        self.assertIn('\n"fact_ids": ["f1","f2"]', rendered)
        self.assertIn('\n"summary": "批次摘要"', rendered)

    def test_envelope_wraps_rendered_long_string_fields(self) -> None:
        value = {"validation_feedback": "f" * 6_000}
        rendered = render_wire_payload(value)
        # The renderer keeps one field per line; the envelope is the safety
        # net that hard-wraps any residual long line before writing.
        self.assertGreater(max_line_length(rendered), 6_000)
        prompt = f"任务\n{rendered}\n结束"
        envelope, _ = task_file_envelope(
            prompt, "t" * 32, "a" * 32, "b" * 32, "c" * 32
        )
        self.assertLessEqual(max_line_length(envelope), TASK_FILE_SAFE_LINE_CHARS)


class FileProbeLineDetectionTests(TestCase):
    def _client(self) -> DSHClient:
        client = DSHClient("dsh")
        client.use_isolated_workspace()
        self.addCleanup(client.cleanup)
        return client

    @staticmethod
    def _truncating_agent(client: DSHClient, line_limit: int):
        def fake_execute(_loader: str, **_kwargs) -> str:
            task_path = next(client.working_directory.glob(".novalist-task-*.md"))
            content = task_path.read_text(encoding="utf-8")
            truncated = "\n".join(
                line[:line_limit] for line in content.split("\n")
            )
            head = re.search(r"^task_nonce_head: ([A-F0-9]+)$", truncated, re.MULTILINE)
            middle = re.search(r"task_nonce_middle=([A-F0-9]+)", truncated)
            tail = re.search(r"^task_nonce_tail: ([A-F0-9]+)$", truncated, re.MULTILINE)
            ack = (
                f"{DSH_FILE_ACK_PREFIX}{head.group(1)}:"
                f"{middle.group(1)}:{tail.group(1)}"
            )
            marker = re.search(rf"{DSH_FILE_PROBE_PREFIX}[A-F0-9]+", truncated)
            return ack + "\n" + (marker.group(0) if marker else "（读不到标记）")

        return fake_execute

    def test_probe_detects_dsh_line_truncation_below_safe_width(self) -> None:
        client = self._client()
        with patch.object(
            client,
            "_execute_prompt",
            side_effect=self._truncating_agent(client, 1_000),
        ):
            supported = client._ensure_file_transport_support(timeout=5, cancel_event=None)
        self.assertFalse(supported)
        self.assertIsNone(client._file_transport_supported)

    def test_probe_passes_with_default_dsh_truncation_limit(self) -> None:
        client = self._client()
        with patch.object(
            client,
            "_execute_prompt",
            side_effect=self._truncating_agent(client, DSH_READ_LINE_TRUNCATION),
        ):
            supported = client._ensure_file_transport_support(timeout=5, cancel_event=None)
        self.assertTrue(supported)
        self.assertTrue(client._file_transport_supported)


class ReadFailedFastFailTests(TestCase):
    def test_read_failed_reply_stops_without_retry(self) -> None:
        client = DSHClient("dsh")
        client._file_transport_supported = True
        self.addCleanup(client.cleanup)
        with patch.object(
            client, "_execute_prompt", return_value=DSH_FILE_READ_FAILED
        ) as execute:
            with self.assertRaisesRegex(RuntimeError, "无法完整读取任务文件"):
                client.generate("系统", "任务")
        execute.assert_called_once()


class GeneratedPromptLineSafetyTests(TestCase):
    def test_memory_prompt_payload_stays_line_safe(self) -> None:
        from core.chapter_memory import build_digest_shard_prompt
        from core.chapter_facts import ChapterFactLedger
        from core.token_budget import ConservativeTokenEstimator

        facts = [
            {"fact_id": f"fact_{index:04d}", "category": "state",
             "subject": "林凡", "predicate": "location",
             "value": f"地点{index}", "anchor": f"chapter_01:p{index:04d}",
             "certainty": "explicit"}
            for index in range(150)
        ]
        ledger = ChapterFactLedger(
            chapter_id="chapter_01",
            chapter_hash="h" * 16,
            facts=(),
            chunks=(),
            unknowns=(),
            cache_hits=0,
            extracted_chunks=0,
        )
        bundle, _ = build_digest_shard_prompt(
            ledger,
            facts,
            batch_index=1,
            batch_count=1,
            input_token_budget=2_000_000,
            estimator=ConservativeTokenEstimator(),
        )
        envelope, _ = task_file_envelope(
            bundle.user_prompt, "t" * 32, "a" * 32, "b" * 32, "c" * 32
        )
        self.assertLessEqual(max_line_length(envelope), TASK_FILE_SAFE_LINE_CHARS)
        # The payload itself is already multi-line before the envelope net.
        self.assertLess(max_line_length(bundle.user_prompt), 500)
