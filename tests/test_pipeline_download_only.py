from __future__ import annotations

from unittest.mock import patch

from youtube_localizer.config import AppConfig
from youtube_localizer.download.direct import DirectMediaDownloadResult
from youtube_localizer.download.youtube import YouTubeDownloadResult
from youtube_localizer.models import ProjectPaths, SourceMetadata
from youtube_localizer.pipeline import load_project_metadata, process_pipeline
from youtube_localizer.utils.files import load_json


def test_render_source_rejects_stale_enhancement_after_settings_change(tmp_path):
    import pytest

    from youtube_localizer.errors import LocalizerError
    from youtube_localizer.pipeline import render_source
    from youtube_localizer.utils.files import atomic_write_json

    project = ProjectPaths(tmp_path / "stale")
    project.create()
    original = project.source / "source_video.mp4"
    original.write_bytes(b"original")
    project.enhanced_source.parent.mkdir(parents=True, exist_ok=True)
    project.enhanced_source.write_bytes(b"old-enhancement")
    metadata = SourceMetadata(
        source_type="local",
        source_input=str(original),
        video_id="test",
        title="test",
        width=1920,
        height=1080,
        duration=1,
    )
    atomic_write_json(project.metadata, metadata.model_dump(mode="json"))
    lowered = AppConfig.model_validate(
        {"enhancement": {"mode": "general"}, "render": {"output_height": 720}}
    )
    assert render_source(project, lowered) == original
    changed = AppConfig.model_validate({"enhancement": {"mode": "animation"}})
    with pytest.raises(LocalizerError, match="Resume processing"):
        render_source(project, changed)


def test_download_only_stops_after_high_quality_acquisition(tmp_path) -> None:
    project = ProjectPaths(tmp_path / "project")
    project.create()
    url = "https://youtu.be/downloadOnly1"
    metadata = SourceMetadata(
        source_type="youtube",
        source_input=url,
        source_url=url,
        video_id="downloadOnly1",
        title="Download only test",
        duration=2.0,
        audio_codec="aac",
    )

    def fake_download(*_args, **_kwargs):
        video = project.source / "source_video.mp4"
        video.write_bytes(b"highest quality merged video")
        return YouTubeDownloadResult(video=video)

    config = AppConfig.model_validate(
        {
            "subtitle_mode": "download_only",
            "translation": {"provider": "offline"},
            "publishing": {"generate_metadata": False},
        }
    )
    with (
        patch(
            "youtube_localizer.pipeline.prepare_project",
            return_value=(project, metadata, {"id": metadata.video_id}),
        ),
        patch("youtube_localizer.pipeline.download_youtube", side_effect=fake_download),
        patch("youtube_localizer.pipeline.transcribe_audio") as transcribe,
        patch("youtube_localizer.pipeline.translate_with_offline") as translate,
        patch("youtube_localizer.pipeline.render_project") as render,
    ):
        result = process_pipeline(url, config)

    source_video = project.source / "source_video.mp4"
    assert result.status == "downloaded"
    assert source_video in result.outputs
    assert load_project_metadata(project).subtitle_kind == ""
    assert load_json(project.state_file)["project_status"] == "downloaded"
    report = load_json(project.logs / "report.json")
    assert report["translation_provider"] == "not requested (download only)"
    assert str(source_video.resolve()) in report["output_paths"]
    transcribe.assert_not_called()
    translate.assert_not_called()
    render.assert_not_called()


def test_webpage_media_refreshes_declared_url_before_a_resumed_acquisition(tmp_path) -> None:
    project = ProjectPaths(tmp_path / "webpage-project")
    project.create()
    page_url = "https://creator.example.com/watch/lesson.html"
    stale_url = "https://cdn.example.com/master.m3u8?token=stale"
    fresh_url = "https://cdn.example.com/master.m3u8?token=fresh"
    metadata = SourceMetadata(
        source_type="webpage_media",
        source_input=page_url,
        source_url=stale_url,
        video_id="lesson",
        title="Authorized lesson",
        duration=2.0,
        audio_codec="aac",
    )
    refreshed = metadata.model_copy(update={"source_url": fresh_url})

    def fake_download(url, *_args, **_kwargs):
        assert url == fresh_url
        video = project.source / "source_video.mp4"
        video.write_bytes(b"highest quality webpage video")
        return DirectMediaDownloadResult(video=video)

    config = AppConfig.model_validate(
        {
            "subtitle_mode": "download_only",
            "translation": {"provider": "offline"},
            "publishing": {"generate_metadata": False},
        }
    )
    with (
        patch(
            "youtube_localizer.pipeline.prepare_project",
            return_value=(project, metadata, None),
        ),
        patch(
            "youtube_localizer.pipeline.inspect_webpage_media",
            return_value=(refreshed, {"id": "lesson"}),
        ) as inspect_page,
        patch("youtube_localizer.pipeline.download_direct_media", side_effect=fake_download),
    ):
        result = process_pipeline(page_url, config)

    assert result.status == "downloaded"
    inspect_page.assert_called_once_with(page_url)
    stored = load_project_metadata(project)
    assert stored.source_input == page_url
    assert stored.source_url is None
    raw_text = (project.source / "metadata.raw.json").read_text(encoding="utf-8")
    assert "token=fresh" not in raw_text
