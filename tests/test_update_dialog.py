from unittest import TestCase

from core.update_service import UpdateCheckResult, UpdateInfo
from core.version import AppVersion
from ui.update_dialog import no_update_notice


class NoUpdateNoticeTests(TestCase):
    def test_missing_release_has_distinct_notice(self) -> None:
        result = UpdateCheckResult(
            current_version=AppVersion.parse("2.0.1-beta"),
            latest=None,
        )

        title, message = no_update_notice(result)

        self.assertEqual(title, "未找到发布版本")
        self.assertIn("没有可用的 GitHub Release", message)
        self.assertNotIn("已是最新版", message)

    def test_existing_non_newer_release_is_up_to_date(self) -> None:
        result = UpdateCheckResult(
            current_version=AppVersion.parse("2.0.5-beta"),
            latest=UpdateInfo(
                version=AppVersion.parse("2.0.5-beta"),
                tag_name="v2.0.5-beta",
                title="Novalist v2.0.5-beta",
                notes="",
                published_at="2026-08-30T12:40:00Z",
                release_url=(
                    "https://github.com/xiauho/novalist/releases/tag/v2.0.5-beta"
                ),
                prerelease=True,
            ),
        )

        title, message = no_update_notice(result)

        self.assertEqual(title, "已是最新版")
        self.assertIn("最新可用版本：v2.0.5-beta", message)
