import json
import unittest

from sidecar.protocol import (
    RPC_PROTOCOL_VERSION,
    ProtocolFault,
    encode_message,
    error_response,
    parse_request_line,
    request_id_hint,
)


class SidecarProtocolTests(unittest.TestCase):
    def test_valid_request_is_decoded_from_one_utf8_line(self) -> None:
        request = parse_request_line(
            encode_message(
                {
                    "type": "request",
                    "protocolVersion": RPC_PROTOCOL_VERSION,
                    "id": "open-1",
                    "method": "project.open",
                    "params": {"path": "D:/project"},
                }
            )
        )

        self.assertEqual(request.request_id, "open-1")
        self.assertEqual(request.method, "project.open")
        self.assertEqual(request.params, {"path": "D:/project"})

    def test_version_mismatch_is_structured_and_keeps_id_hint(self) -> None:
        line = encode_message(
            {
                "type": "request",
                "protocolVersion": RPC_PROTOCOL_VERSION + 1,
                "id": 42,
                "method": "system.handshake",
                "params": {},
            }
        )

        with self.assertRaises(ProtocolFault) as raised:
            parse_request_line(line)

        self.assertEqual(raised.exception.code, "PROTOCOL_VERSION_MISMATCH")
        self.assertEqual(request_id_hint(line), 42)
        payload = error_response(42, raised.exception)
        self.assertEqual(payload["id"], 42)
        self.assertEqual(payload["error"]["data"]["supported"], 1)

    def test_protocol_version_must_be_an_integer(self) -> None:
        for value in (True, 1.0, "1"):
            with self.subTest(value=value):
                line = encode_message(
                    {
                        "type": "request",
                        "protocolVersion": value,
                        "id": "typed-version",
                        "method": "system.ping",
                        "params": {},
                    }
                )
                with self.assertRaises(ProtocolFault) as raised:
                    parse_request_line(line)
                self.assertEqual(
                    raised.exception.code, "PROTOCOL_VERSION_MISMATCH"
                )

    def test_malformed_json_does_not_invent_a_request_id(self) -> None:
        line = b'{"id":"partial"\n'

        with self.assertRaises(ProtocolFault) as raised:
            parse_request_line(line)

        self.assertEqual(raised.exception.code, "INVALID_JSON")
        self.assertIsNone(request_id_hint(line))

    def test_unknown_envelope_fields_are_rejected(self) -> None:
        line = (
            json.dumps(
                {
                    "type": "request",
                    "protocolVersion": 1,
                    "id": "x",
                    "method": "system.ping",
                    "params": {},
                    "unsafe": True,
                }
            ).encode("utf-8")
            + b"\n"
        )

        with self.assertRaises(ProtocolFault) as raised:
            parse_request_line(line)

        self.assertEqual(raised.exception.code, "INVALID_REQUEST")
        self.assertEqual(raised.exception.data, {"fields": ["unsafe"]})

    def test_nonstandard_constants_and_duplicate_keys_are_rejected(self) -> None:
        invalid_lines = (
            b'{"type":"request","protocolVersion":1,"id":"x",'
            b'"method":"system.ping","params":{"value":NaN}}\n',
            b'{"type":"request","protocolVersion":1,"id":"x","id":"y",'
            b'"method":"system.ping","params":{}}\n',
        )

        for line in invalid_lines:
            with self.subTest(line=line):
                with self.assertRaises(ProtocolFault) as raised:
                    parse_request_line(line)
                self.assertEqual(raised.exception.code, "INVALID_JSON")


if __name__ == "__main__":
    unittest.main()
