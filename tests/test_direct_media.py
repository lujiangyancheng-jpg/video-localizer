from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from youtube_localizer.config import AppConfig, DownloadConfig
from youtube_localizer.download.direct import (
    _probe_direct_media_content_type,
    assess_direct_media_url,
    direct_media_id,
    download_direct_media,
    inspect_direct_media,
    is_direct_media_candidate_url,
    is_direct_media_url,
)
from youtube_localizer.errors import InputValidationError
from youtube_localizer.models import SourceMetadata
from youtube_localizer.pipeline import _inspect_input, _source_identifier, prepare_project


class FakeInspectionYDL:
    def __init__(self, _options):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def extract_info(self, url, download=False):
        assert download is False
        return {
            "id": "remote-file",
            "title": "Authorized clip",
            "duration": 12.5,
            "availability": "public",
            "webpage_url": url,
            "width": 1920,
            "height": 1080,
        }


class FakeDownloadYDL:
    last_options: dict[str, object] | None = None

    def __init__(self, options):
        self.options = options
        FakeDownloadYDL.last_options = options

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def extract_info(self, _url, download=True):
        assert download is True
        destination = Path(self.options["outtmpl"]).parent
        (destination / "download.mp4").write_bytes(b"video")
        return {"id": "remote-file", "ext": "mp4"}

    def prepare_filename(self, _info):
        return str(Path(self.options["outtmpl"]).parent / "download.mp4")


def test_direct_media_recognition_rejects_playback_pages_and_credentials() -> None:
    assert is_direct_media_url("https://cdn.example.test/owned/demo.MP4?signature=current")
    assert is_direct_media_url("https://cdn.example.test/owned/master.m3u8")
    assert not is_direct_media_url("https://www.lmm85.com/play/7072_2_2.html")
    assert not is_direct_media_url("https://user:pass@cdn.example.test/owned/demo.mp4")
    assert is_direct_media_candidate_url("https://cdn.example.test/opaque/video/resource/")
    assert not is_direct_media_url("https://cdn.example.test/opaque/video/resource/")
    assert not is_direct_media_candidate_url("https://www.example.test/play/123.html")


def test_extensionless_cdn_url_is_explained_without_contacting_the_server() -> None:
    assessment = assess_direct_media_url(
        "https://groupvideo.photo.qq.com/1071_0bc/opaque-resource/"
    )

    assert assessment.media_kind == "无扩展名 CDN 媒体直链"
    assert assessment.signed is False
    assert assessment.expired is False


def test_direct_media_assessment_rejects_a_truncated_player_display_url() -> None:
    with pytest.raises(InputValidationError, match="截断"):
        assess_direct_media_url("https://groupvideo.photo.qq.com/1071_0bc…")


def test_direct_media_assessment_detects_an_expired_epoch_signature() -> None:
    assessment = assess_direct_media_url(
        "https://cdn.example.test/video.mp4?expires=1735689600&token=example",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert assessment.signed is True
    assert assessment.expired is True
    assert assessment.expires_at == datetime(2025, 1, 1, tzinfo=UTC)


def test_extensionless_probe_falls_back_when_cdn_rejects_head() -> None:
    url = "https://groupvideo.photo.qq.com/opaque-resource/"
    head_response = httpx.Response(403, request=httpx.Request("HEAD", url))
    range_response = httpx.Response(
        206,
        headers={"content-type": "video/mp4"},
        request=httpx.Request("GET", url),
    )

    with (
        patch("youtube_localizer.download.direct.httpx.head", return_value=head_response),
        patch(
            "youtube_localizer.download.direct.httpx.stream",
            return_value=nullcontext(range_response),
        ) as stream,
    ):
        assert _probe_direct_media_content_type(url) == "video/mp4"

    assert stream.call_args.kwargs["headers"]["Range"] == "bytes=0-0"


def test_direct_media_identifier_ignores_refreshed_signed_query_values() -> None:
    first = "https://cdn.example.test/owned/demo.m3u8?token=first"
    refreshed = "https://cdn.example.test/owned/demo.m3u8?token=second"

    assert direct_media_id(first) == direct_media_id(refreshed)
    assert _source_identifier(first) == _source_identifier(refreshed)


def test_direct_media_inspection_creates_direct_media_metadata() -> None:
    url = "https://cdn.example.test/owned/demo.m3u8?token=current"
    with patch("youtube_localizer.download.direct._youtube_dl", side_effect=FakeInspectionYDL):
        metadata, raw = inspect_direct_media(url)

    assert metadata.source_type == "direct_media"
    assert metadata.source_input == url
    assert metadata.source_url == url
    assert metadata.video_id == direct_media_id(url)
    assert metadata.title == "Authorized clip"
    assert raw["width"] == 1920


def test_extensionless_video_url_is_verified_by_content_type_before_inspection() -> None:
    url = "https://cdn.example.test/opaque/video/resource/"
    with (
        patch(
            "youtube_localizer.download.direct._probe_direct_media_content_type",
            return_value="video/mp4",
        ) as probe,
        patch("youtube_localizer.download.direct._youtube_dl", side_effect=FakeInspectionYDL),
    ):
        metadata, _ = inspect_direct_media(url)

    probe.assert_called_once_with(url)
    assert metadata.source_type == "direct_media"
    assert metadata.video_id == direct_media_id(url)


def test_resuming_a_direct_media_project_refreshes_an_expired_signed_url(
    tmp_path, monkeypatch
) -> None:
    first = "https://cdn.example.test/owned/demo.m3u8?token=first"
    refreshed = "https://cdn.example.test/owned/demo.m3u8?token=second"
    app_config = AppConfig(output_directory=tmp_path)
    metadata = SourceMetadata(
        source_type="direct_media",
        source_input=first,
        source_url=first,
        video_id=direct_media_id(first),
        title="Authorized clip",
    )
    monkeypatch.setattr(
        "youtube_localizer.pipeline.inspect_direct_media", lambda _url: (metadata, {"id": "remote"})
    )

    project, _, _ = prepare_project(first, app_config)
    _, resumed, _ = prepare_project(refreshed, app_config, resume=True)

    assert project.root.name.endswith(direct_media_id(first))
    assert resumed.source_input == refreshed
    assert resumed.source_url == refreshed


def test_direct_media_download_uses_best_quality_without_caption_requests(tmp_path) -> None:
    url = "https://cdn.example.test/owned/demo.mp4"
    with patch("youtube_localizer.download.youtube._youtube_dl", side_effect=FakeDownloadYDL):
        result = download_direct_media(url, {}, tmp_path, DownloadConfig())

    assert result.video == tmp_path / "source_video.mp4"
    assert FakeDownloadYDL.last_options is not None
    assert FakeDownloadYDL.last_options["writesubtitles"] is False
    assert FakeDownloadYDL.last_options["writeautomaticsub"] is False
    assert FakeDownloadYDL.last_options["subtitleslangs"] == []


def test_pipeline_sends_a_playback_page_to_the_public_page_resolver(monkeypatch) -> None:
    url = "https://creator.example.test/play/7072_2_2.html"
    metadata = SourceMetadata(
        source_type="webpage_media",
        source_input=url,
        source_url="https://cdn.example.test/owned/demo.mp4",
        video_id="page",
        title="Authorized page",
    )
    monkeypatch.setattr("youtube_localizer.pipeline.load_cached_inspection", lambda _url: None)
    monkeypatch.setattr(
        "youtube_localizer.pipeline.inspect_webpage_media",
        lambda _url: (metadata, {"id": "page"}),
    )

    inspected, raw = _inspect_input(url)

    assert inspected == metadata
    assert raw == {"id": "page"}
