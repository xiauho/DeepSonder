import json
from io import BytesIO
from unittest import TestCase
from urllib.error import HTTPError

from core.update_service import (
    MAX_RESPONSE_BYTES,
    UpdateCheckError,
    check_for_updates,
    fetch_releases,
    is_allowed_release_url,
    select_latest_release,
)
from core.version import AppVersion


def release(
    tag: str,
    *,
    prerelease: bool = False,
    draft: bool = False,
    url: str | None = None,
) -> dict:
    return {
        "tag_name": tag,
        "name": tag,
        "body": f"Notes for {tag}",
        "published_at": "2026-08-30T00:00:00Z",
        "html_url": url or f"https://github.com/xiauho/novalist/releases/tag/{tag}",
        "prerelease": prerelease,
        "draft": draft,
    }


class FakeResponse:
    def __init__(self, payload: bytes):
        self._stream = BytesIO(payload)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


class UpdateSelectionTests(TestCase):
    def test_beta_channel_selects_newest_beta_or_stable(self) -> None:
        latest = select_latest_release(
            [
                release("v2.0.6-beta", prerelease=True),
                release("v2.0.5"),
                release("v2.1.0-alpha", prerelease=True),
            ],
            "beta",
        )
        self.assertIsNotNone(latest)
        self.assertEqual(latest.tag_name, "v2.0.6-beta")

    def test_stable_channel_ignores_prereleases(self) -> None:
        latest = select_latest_release(
            [release("v2.0.6-beta", prerelease=True), release("v2.0.5")],
            "stable",
        )
        self.assertEqual(latest.tag_name, "v2.0.5")

    def test_drafts_invalid_versions_and_untrusted_urls_are_ignored(self) -> None:
        latest = select_latest_release(
            [
                release("v9.0.0", draft=True),
                release("not-a-version"),
                release("v8.0.0", url="https://example.com/update"),
            ],
            "beta",
        )
        self.assertIsNone(latest)

    def test_update_is_available_only_when_remote_is_newer(self) -> None:
        result = check_for_updates(
            AppVersion.parse("2.0.5-beta"),
            "beta",
            opener=lambda _request, timeout: FakeResponse(
                json.dumps([release("v2.0.6-beta", prerelease=True)]).encode()
            ),
        )
        self.assertTrue(result.update_available)

        same = check_for_updates(
            AppVersion.parse("2.0.6-beta"),
            "beta",
            opener=lambda _request, timeout: FakeResponse(
                json.dumps([release("v2.0.6-beta", prerelease=True)]).encode()
            ),
        )
        self.assertFalse(same.update_available)

        older = check_for_updates(
            AppVersion.parse("2.0.6-beta"),
            "beta",
            opener=lambda _request, timeout: FakeResponse(
                json.dumps([release("v2.0.5")]).encode()
            ),
        )
        self.assertFalse(older.update_available)

    def test_allowed_release_url_is_restricted_to_project(self) -> None:
        self.assertTrue(
            is_allowed_release_url(
                "https://github.com/xiauho/novalist/releases/tag/v2.0.6-beta"
            )
        )
        self.assertFalse(is_allowed_release_url("http://github.com/xiauho/novalist"))
        self.assertFalse(is_allowed_release_url("https://github.com/other/project"))


class UpdateNetworkTests(TestCase):
    def test_fetch_uses_expected_headers_and_timeout(self) -> None:
        captured = {}

        def opener(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse(b"[]")

        result = fetch_releases(AppVersion.parse("2.0.5-beta"), timeout=7, opener=opener)
        self.assertEqual(result, [])
        self.assertEqual(captured["timeout"], 7)
        self.assertEqual(captured["request"].host, "api.github.com")
        self.assertEqual(captured["request"].get_header("User-agent"), "Novalist/2.0.5-beta")

    def test_malformed_response_is_rejected(self) -> None:
        with self.assertRaisesRegex(UpdateCheckError, "无法识别"):
            fetch_releases(
                AppVersion.parse("2.0.5"),
                opener=lambda _request, timeout: FakeResponse(b"{invalid"),
            )

    def test_non_list_response_is_rejected(self) -> None:
        with self.assertRaisesRegex(UpdateCheckError, "格式不正确"):
            fetch_releases(
                AppVersion.parse("2.0.5"),
                opener=lambda _request, timeout: FakeResponse(b"{}"),
            )

    def test_oversized_response_is_rejected(self) -> None:
        with self.assertRaisesRegex(UpdateCheckError, "过大"):
            fetch_releases(
                AppVersion.parse("2.0.5"),
                opener=lambda _request, timeout: FakeResponse(
                    b"[" + b" " * MAX_RESPONSE_BYTES
                ),
            )

    def test_network_failure_has_user_presentable_message(self) -> None:
        def failing_opener(_request, timeout):
            raise OSError("offline")

        with self.assertRaisesRegex(UpdateCheckError, "无法连接 GitHub"):
            fetch_releases(
                AppVersion.parse("2.0.5"),
                opener=failing_opener,
            )

    def test_rate_limit_has_user_presentable_message(self) -> None:
        def rate_limited_opener(request, timeout):
            raise HTTPError(request.full_url, 403, "Forbidden", {}, None)

        with self.assertRaisesRegex(UpdateCheckError, "限制"):
            fetch_releases(
                AppVersion.parse("2.0.5"),
                opener=rate_limited_opener,
            )
