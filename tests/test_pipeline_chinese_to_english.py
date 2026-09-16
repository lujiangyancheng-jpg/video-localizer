from __future__ import annotations

from unittest.mock import patch

from youtube_localizer.config import AppConfig
from youtube_localizer.download.youtube import YouTubeDownloadResult
from youtube_localizer.models import ProjectPaths, SourceMetadata, SubtitleCue
from youtube_localizer.pipeline import _write_localized_subtitles, process_pipeline
from youtube_localizer.subtitles.cleanup import CleanupResult
from youtube_localizer.subtitles.parser import parse_subtitle, write_srt


def _fake_extract_audio(_source, output, **_kwargs):
    output.write_bytes(b"synthetic wav")
    return output


def test_pipeline_ignores_provided_chinese_and_transcribes_locally(tmp_path) -> None:
    project = ProjectPaths(tmp_path / "project")
    project.create()
    url = "https://youtu.be/dQw4w9WgXcQ"
    metadata = SourceMetadata(
        source_type="youtube",
        source_input=url,
        source_url=url,
        video_id="dQw4w9WgXcQ",
        title="Chinese to English test",
        duration=2.0,
        audio_codec="aac",
    )
    raw_info = {"id": metadata.video_id, "title": metadata.title}

    def fake_download(*_args, **_kwargs):
        video = project.source / "source_video.mp4"
        video.write_bytes(b"synthetic video")
        # A stale caption file from an older project must not be consumed.
        (project.source / "source.zh.vtt").write_text(
            "WEBVTT\n\n00:00:00.100 --> 00:00:01.900\nYouTube 字幕\n",
            encoding="utf-8",
        )
        return YouTubeDownloadResult(video=video)

    def fake_transcribe(_audio, output_json, output_srt, _config, *, language):
        assert language == "zh"
        cues = [SubtitleCue(id=1, start_ms=100, end_ms=1900, text="本地识别字幕")]
        output_json.write_text("{}", encoding="utf-8")
        write_srt(output_srt, cues)
        return CleanupResult(cues, [], [])

    def fake_translate(active_project, active_config, _metadata):
        chinese = parse_subtitle(active_project.chinese_srt)
        english = [cue.model_copy(update={"text": "Welcome to my channel"}) for cue in chinese]
        return _write_localized_subtitles(
            active_project,
            english,
            chinese,
            active_config,
        )

    def fake_render(active_project, _config):
        active_project.english_hardsub.write_bytes(b"rendered")
        return active_project.english_hardsub

    config = AppConfig.model_validate(
        {
            "translation": {"provider": "offline", "direction": "zh-to-en"},
            "publishing": {"generate_metadata": False},
            "render": {"soft_subtitles": False},
        }
    )
    with (
        patch(
            "youtube_localizer.pipeline.prepare_project",
            return_value=(project, metadata, raw_info),
        ),
        patch("youtube_localizer.pipeline.download_youtube", side_effect=fake_download),
        patch("youtube_localizer.pipeline.translate_with_offline", side_effect=fake_translate),
        patch(
            "youtube_localizer.pipeline.extract_transcription_audio",
            side_effect=_fake_extract_audio,
        ),
        patch(
            "youtube_localizer.pipeline.transcribe_audio", side_effect=fake_transcribe
        ) as transcribe,
        patch("youtube_localizer.pipeline.render_project", side_effect=fake_render),
    ):
        result = process_pipeline(url, config)

    assert result.status == "completed"
    assert parse_subtitle(project.chinese_srt)[0].text == "本地识别字幕"
    assert parse_subtitle(project.english_srt)[0].text == "Welcome to my channel"
    assert project.english_ass.is_file()
    assert project.english_hardsub.is_file()
    assert not project.chinese_hardsub.exists()
    transcribe.assert_called_once()


def test_pipeline_transcribes_chinese_when_no_caption_track_exists(tmp_path) -> None:
    project = ProjectPaths(tmp_path / "project")
    project.create()
    url = "https://youtu.be/noCaptions1"
    metadata = SourceMetadata(
        source_type="youtube",
        source_input=url,
        source_url=url,
        video_id="noCaptions1",
        title="Chinese transcription test",
        duration=2.0,
        audio_codec="aac",
    )

    def fake_download(*_args, **_kwargs):
        video = project.source / "source_video.mp4"
        video.write_bytes(b"synthetic video")
        return YouTubeDownloadResult(video=video)

    def fake_transcribe(_audio, output_json, output_srt, _config, *, language):
        assert language == "zh"
        cue = {
            "id": 1,
            "start_ms": 100,
            "end_ms": 1900,
            "text": "这是中文语音",
        }
        cues = [SubtitleCue.model_validate(cue)]
        output_json.write_text("{}", encoding="utf-8")
        write_srt(output_srt, cues)
        return CleanupResult(cues, [], [])

    def fake_translate(active_project, active_config, _metadata):
        chinese = parse_subtitle(active_project.chinese_srt)
        english = [cue.model_copy(update={"text": "This is Chinese speech"}) for cue in chinese]
        return _write_localized_subtitles(
            active_project,
            english,
            chinese,
            active_config,
        )

    config = AppConfig.model_validate(
        {
            "translation": {"provider": "offline", "direction": "zh-to-en"},
            "publishing": {"generate_metadata": False},
            "render": {"soft_subtitles": False},
        }
    )
    with (
        patch(
            "youtube_localizer.pipeline.prepare_project",
            return_value=(project, metadata, {"id": metadata.video_id}),
        ),
        patch("youtube_localizer.pipeline.download_youtube", side_effect=fake_download),
        patch(
            "youtube_localizer.pipeline.extract_transcription_audio",
            side_effect=_fake_extract_audio,
        ),
        patch("youtube_localizer.pipeline.transcribe_audio", side_effect=fake_transcribe),
        patch("youtube_localizer.pipeline.translate_with_offline", side_effect=fake_translate),
        patch("youtube_localizer.pipeline.render_project") as render,
    ):
        render.side_effect = lambda active_project, _config: (
            active_project.english_hardsub.write_bytes(b"rendered")
            and active_project.english_hardsub
        )
        result = process_pipeline(url, config)

    assert result.status == "completed"
    assert parse_subtitle(project.chinese_srt)[0].text == "这是中文语音"
    assert parse_subtitle(project.english_srt)[0].text == "This is Chinese speech"
