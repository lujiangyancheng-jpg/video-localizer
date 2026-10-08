from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from youtube_localizer.download import accounts, platforms
from youtube_localizer.download.youtube import PlatformDownloadLogger, _run_youtube_download
from youtube_localizer.errors import InputValidationError, LocalizerError
from youtube_localizer.gui import queue_input_values


def test_share_text_and_platform_identity():
    assert queue_input_values("复制此链接打开抖音 https://v.douyin.com/AbCd/ 看视频") == [
        "https://v.douyin.com/AbCd/"
    ]
    assert (
        platforms.platform_source_id(
            "https://www.bilibili.com/video/BV1xx123/?p=2&share_source=copy"
        )
        == "bilibili_BV1xx123_p2"
    )
    assert platforms.platform_for_url("https://www.bilibili.com.attacker.example/video/1") is None
    assert platforms.platform_for_url("https://user:password@www.douyin.com/video/123") is None


def test_account_scope_does_not_include_other_websites():
    cookies = [
        {"name": "session", "value": "secret", "domain": domain, "path": "/"}
        for domain in (
            ".bilibili.com",
            "api.bilibili.com",
            "evilbilibili.com",
            "youtube.com",
            "bilibili.com.attacker.example",
        )
    ]
    assert [
        cookie["domain"] for cookie in accounts.filter_platform_cookies("bilibili", cookies)
    ] == [".bilibili.com", "api.bilibili.com"]


@pytest.mark.skipif(os.name != "nt", reason="Windows account encryption")
def test_windows_account_roundtrip_is_encrypted_and_can_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "onboarding_state_directory", lambda: tmp_path)
    cookies = [
        {
            "name": "SESSDATA",
            "value": "test-secret-never-log",
            "domain": ".bilibili.com",
            "path": "/",
            "expires": -1,
        }
    ]
    accounts.save_account("bilibili", cookies)
    stored = next(tmp_path.rglob("*.dpapi"))
    assert b"test-secret-never-log" not in stored.read_bytes()
    assert accounts.load_account("bilibili")[0]["value"] == "test-secret-never-log"
    accounts.clear_account("bilibili")
    assert accounts.load_account("bilibili") == []


def test_platform_inspection_keeps_part_identity_and_never_exports_cookie_values():
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.extract_info.return_value = {
        "id": "123_part2",
        "title": "Part two",
        "webpage_url": "https://www.bilibili.com/video/BV1xx123/?p=2",
        "duration": 12,
        "height": 1080,
    }
    with (
        patch.object(platforms, "_youtube_dl", return_value=ydl),
        patch.object(platforms, "apply_account") as apply,
    ):
        metadata, _ = platforms.inspect_platform("https://www.bilibili.com/video/BV1xx123/?p=2")
    apply.assert_called_once_with(ydl, "bilibili")
    assert metadata.video_id == "bilibili_BV1xx123_p2"
    assert metadata.source_type == "bilibili"


def test_platform_errors_redact_upstream_session_details():
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.extract_info.side_effect = RuntimeError("SESSDATA=secret https://example.com?token=secret")
    with (
        patch.object(platforms, "_youtube_dl", return_value=ydl),
        patch.object(platforms, "apply_account"),
        pytest.raises(InputValidationError) as error,
    ):
        platforms.inspect_platform("https://www.douyin.com/video/123")
    assert "secret" not in str(error.value)


def test_download_attaches_session_to_cookiejar_not_command_options(tmp_path):
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.extract_info.return_value = {"id": "123"}
    ydl.prepare_filename.return_value = str(tmp_path / "download.mp4")
    with (
        patch("youtube_localizer.download.youtube._youtube_dl", return_value=ydl) as factory,
        patch.object(accounts, "apply_account") as apply,
    ):
        _run_youtube_download(
            "https://www.douyin.com/video/123", {"_localizer_account_platform": "douyin"}
        )
    assert factory.call_args.args[0] == {}
    apply.assert_called_once_with(ydl, "douyin")


def test_bilibili_missing_login_is_not_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "onboarding_state_directory", lambda: tmp_path)
    with pytest.raises(LocalizerError, match="尚未登录"):
        accounts.save_account(
            "bilibili", [{"name": "other", "value": "cookie", "domain": ".bilibili.com"}]
        )
    assert not list(tmp_path.rglob("*.dpapi"))


def test_platform_download_logger_only_exposes_numeric_progress(caplog):
    import logging

    logger = PlatformDownloadLogger()
    with caplog.at_level(logging.INFO):
        logger.debug("[download] 50.0% https://example.test/?token=secret")
        logger.warning("SESSDATA=secret")
        logger.error("Cookie: secret")
    assert "50.0%" in caplog.text
    assert "secret" not in caplog.text
    assert "example.test" not in caplog.text


def test_bilibili_explicit_part_survives_upstream_canonical_url(monkeypatch):
    monkeypatch.setattr(
        platforms,
        "_extract",
        lambda *_args, **_kwargs: {
            "title": "Part two",
            "id": "123",
            "duration": 12,
            "webpage_url": "https://www.bilibili.com/video/BV1xx123/",
        },
    )
    metadata, _ = platforms.inspect_platform("https://www.bilibili.com/video/BV1xx123/?p=2")
    assert metadata.video_id == "bilibili_BV1xx123_p2"
    assert metadata.source_url.endswith("?p=2")
