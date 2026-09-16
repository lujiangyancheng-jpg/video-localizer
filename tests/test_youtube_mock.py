from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from youtube_localizer.config import DownloadConfig
from youtube_localizer.download.youtube import (
    discover_javascript_runtimes,
    download_youtube,
    inspect_youtube,
)
from youtube_localizer.errors import LocalizerError


class FakeYDL:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def extract_info(self, url, download=False):
        return {
            "id": "dQw4w9WgXcQ",
            "title": "Owned demo",
            "channel": "Creator",
            "duration": 12.5,
            "availability": "public",
            "webpage_url": url,
        }


def test_youtube_inspection_uses_metadata_without_downloading() -> None:
    with patch("youtube_localizer.download.youtube._youtube_dl", side_effect=FakeYDL):
        metadata, raw = inspect_youtube("https://youtu.be/dQw4w9WgXcQ")
    assert metadata.title == "Owned demo"
    assert metadata.channel == "Creator"
    assert raw["id"] == "dQw4w9WgXcQ"


def test_javascript_runtime_is_discovered_in_embedded_python_scripts(tmp_path) -> None:
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    deno = scripts / "deno.exe"
    deno.write_bytes(b"")

    with patch("youtube_localizer.download.youtube.sys.executable", str(python)):
        runtimes = discover_javascript_runtimes()

    assert runtimes["deno"] == str(deno.resolve())


class FakeDownloadYDL:
    last_options = None

    def __init__(self, options):
        self.options = options
        FakeDownloadYDL.last_options = options

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def extract_info(self, url, download=True):
        destination = Path(self.options["outtmpl"]).parent
        (destination / "download.mp4").write_bytes(b"video")
        for language in self.options["subtitleslangs"]:
            (destination / f"download.{language}.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nText\n",
                encoding="utf-8",
            )
        return {"id": "dQw4w9WgXcQ", "ext": "mp4", "webpage_url": url}

    def prepare_filename(self, info):
        return str(Path(self.options["outtmpl"]).parent / "download.mp4")


class FakeSubtitleLimitedYDL:
    options_seen = []

    def __init__(self, options):
        self.options = options
        FakeSubtitleLimitedYDL.options_seen.append(options)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def extract_info(self, url, download=True):
        if self.options["writesubtitles"] or self.options["writeautomaticsub"]:
            raise RuntimeError(
                "ERROR: Unable to download video subtitles for 'zh-Hans': "
                "HTTP Error 429: Too Many Requests"
            )
        destination = Path(self.options["outtmpl"]).parent
        (destination / "download.mp4").write_bytes(b"video")
        return {"id": "dQw4w9WgXcQ", "ext": "mp4", "webpage_url": url}

    def prepare_filename(self, info):
        return str(Path(self.options["outtmpl"]).parent / "download.mp4")


class FakeTemporaryRateLimitedYDL:
    attempts = 0

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def extract_info(self, _url, download=True):
        assert download
        FakeTemporaryRateLimitedYDL.attempts += 1
        if FakeTemporaryRateLimitedYDL.attempts == 1:
            raise RuntimeError("HTTP Error 429: Too Many Requests")
        destination = Path(self.options["outtmpl"]).parent
        (destination / "download.mp4").write_bytes(b"video")
        return {"id": "dQw4w9WgXcQ", "ext": "mp4"}

    def prepare_filename(self, _info):
        return str(Path(self.options["outtmpl"]).parent / "download.mp4")


class FakePermanentRateLimitedYDL(FakeTemporaryRateLimitedYDL):
    def extract_info(self, _url, download=True):
        assert download
        raise RuntimeError("HTTP Error 429: Too Many Requests")


def test_download_never_requests_youtube_subtitles(tmp_path) -> None:
    info = {
        "subtitles": {"en": [{}]},
        "automatic_captions": {"zh-Hans": [{}]},
    }
    with (
        patch("youtube_localizer.download.youtube._youtube_dl", side_effect=FakeDownloadYDL),
        patch(
            "youtube_localizer.download.youtube.discover_javascript_runtimes",
            return_value={"deno": r"C:\tools\deno.exe"},
        ),
    ):
        result = download_youtube(
            "https://youtu.be/dQw4w9WgXcQ",
            info,
            tmp_path,
            DownloadConfig(),
        )

    assert result.video.name == "source_video.mp4"
    assert FakeDownloadYDL.last_options["format"] == "bestvideo+bestaudio/best"
    assert FakeDownloadYDL.last_options["format_sort"] == ["res", "fps", "br", "size"]
    assert FakeDownloadYDL.last_options["concurrent_fragment_downloads"] == 4
    assert FakeDownloadYDL.last_options["merge_output_format"] == "mp4"
    assert FakeDownloadYDL.last_options["js_runtimes"] == {"deno": {"path": r"C:\tools\deno.exe"}}
    assert FakeDownloadYDL.last_options["writesubtitles"] is False
    assert FakeDownloadYDL.last_options["writeautomaticsub"] is False
    assert FakeDownloadYDL.last_options["subtitleslangs"] == []
    assert not list(tmp_path.glob("source.*.vtt"))


def test_video_download_runs_once_when_caption_catalog_exists(tmp_path) -> None:
    FakeSubtitleLimitedYDL.options_seen = []
    info = {
        "subtitles": {"en": [{}]},
        "automatic_captions": {"zh-Hans": [{}]},
    }
    with patch(
        "youtube_localizer.download.youtube._youtube_dl",
        side_effect=FakeSubtitleLimitedYDL,
    ):
        result = download_youtube(
            "https://youtu.be/dQw4w9WgXcQ",
            info,
            tmp_path,
            DownloadConfig(),
        )

    assert result.video == tmp_path / "source_video.mp4"
    assert result.warnings == ()
    assert len(FakeSubtitleLimitedYDL.options_seen) == 1
    options = FakeSubtitleLimitedYDL.options_seen[0]
    assert options["writesubtitles"] is False
    assert options["writeautomaticsub"] is False
    assert options["subtitleslangs"] == []


def test_temporary_youtube_rate_limit_retries_without_requesting_cookies(tmp_path) -> None:
    FakeTemporaryRateLimitedYDL.attempts = 0
    with (
        patch(
            "youtube_localizer.download.youtube._youtube_dl",
            side_effect=FakeTemporaryRateLimitedYDL,
        ),
        patch("youtube_localizer.download.youtube.time.sleep") as sleep,
    ):
        result = download_youtube(
            "https://youtu.be/dQw4w9WgXcQ",
            {},
            tmp_path,
            DownloadConfig(retry_attempts=2, retry_delay_seconds=3),
        )

    assert result.video == tmp_path / "source_video.mp4"
    assert FakeTemporaryRateLimitedYDL.attempts == 2
    sleep.assert_called_once_with(3)


def test_persistent_youtube_rate_limit_has_an_actionable_resume_message(tmp_path) -> None:
    with (
        patch(
            "youtube_localizer.download.youtube._youtube_dl",
            side_effect=FakePermanentRateLimitedYDL,
        ),
        patch("youtube_localizer.download.youtube.time.sleep"),
        pytest.raises(LocalizerError, match="wait a few minutes and resume"),
    ):
        download_youtube(
            "https://youtu.be/dQw4w9WgXcQ",
            {},
            tmp_path,
            DownloadConfig(retry_attempts=2, retry_delay_seconds=1),
        )
