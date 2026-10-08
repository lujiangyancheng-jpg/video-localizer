from pathlib import Path
from unittest.mock import MagicMock

import pytest

from youtube_localizer.config import AppConfig
from youtube_localizer.enhancement import preview
from youtube_localizer.enhancement.super_resolution import EnhancementPreviewResult
from youtube_localizer.errors import LocalizerError
from youtube_localizer.models import SourceMetadata


def test_remote_preview_downloads_only_requested_section_with_platform_session(
    tmp_path, monkeypatch
):
    metadata = SourceMetadata(
        source_type="bilibili",
        source_input="",
        source_url="https://www.bilibili.com/video/BVtest/",
        video_id="test",
        title="test",
        duration=100,
        width=64,
        height=64,
        frame_rate=2,
    )
    monkeypatch.setattr("youtube_localizer.pipeline._inspect_input", lambda _: (metadata, {}))
    download = MagicMock(return_value=tmp_path / "download.mp4")
    monkeypatch.setattr(preview, "download_media", download)
    monkeypatch.setattr(preview, "probe_media", lambda _: {})
    actual = metadata.model_copy(update={"duration": 10})
    monkeypatch.setattr(preview, "metadata_from_probe", lambda *_args, **_kwargs: actual)
    render = MagicMock(return_value=EnhancementPreviewResult(tmp_path / "preview.mp4", 2, 2))
    monkeypatch.setattr(preview, "render_enhancement_comparison", render)
    result = preview.preview_source(
        metadata.source_url,
        tmp_path / "preview.mp4",
        AppConfig(),
        start=30,
        duration=10,
    )
    assert download.call_args.kwargs["section"] == (30, 40)
    assert download.call_args.kwargs["account_platform"] == "bilibili"
    assert render.call_args.kwargs["start_seconds"] == 0
    assert result.estimated_full_seconds == 20


def test_preview_rejects_out_of_range_start_before_download(tmp_path, monkeypatch):
    metadata = SourceMetadata(
        source_type="youtube",
        source_input="",
        video_id="test",
        title="test",
        duration=10,
    )
    monkeypatch.setattr("youtube_localizer.pipeline._inspect_input", lambda _: (metadata, {}))
    download = MagicMock()
    monkeypatch.setattr(preview, "download_media", download)
    with pytest.raises(LocalizerError, match="预览起点"):
        preview.preview_source("https://youtu.be/test", Path("unused.mp4"), AppConfig(), start=20)
    download.assert_not_called()
