from __future__ import annotations

from youtube_localizer.config import AppConfig
from youtube_localizer.models import ProjectPaths, SourceMetadata, SubtitleCue
from youtube_localizer.pipeline import save_project_config
from youtube_localizer.review import (
    load_subtitle_review_session,
    retranslate_paragraph,
    save_reviewed_subtitles,
)
from youtube_localizer.subtitles.parser import parse_subtitle, write_srt
from youtube_localizer.utils.files import atomic_write_json


def _project_with_target(tmp_path, *, direction: str = "en-to-zh", mode: str = "chinese"):
    project = ProjectPaths(tmp_path / "review-project")
    project.create()
    atomic_write_json(
        project.metadata,
        SourceMetadata(
            source_type="local", source_input="owned.mp4", video_id="review", title="Review"
        ).model_dump(mode="json"),
    )
    config = AppConfig(
        subtitle_mode=mode,
        translation={"direction": direction},
        subtitles={"font": "Arial"},
    )
    save_project_config(project, config)
    source_code, target_code = direction.split("-to-")
    source = [SubtitleCue(id=1, start_ms=0, end_ms=1000, text="Hello")]
    target = [SubtitleCue(id=1, start_ms=0, end_ms=1000, text="你好")]
    write_srt(project.subtitle_srt(source_code), source)
    write_srt(project.subtitle_srt(target_code), target)
    return project, config


def test_review_save_rebuilds_target_ass(tmp_path) -> None:
    project, config = _project_with_target(tmp_path)
    session = load_subtitle_review_session(project, config)
    edited = [session.cues[0].model_copy(update={"text": "您好，世界。"})]

    outputs = save_reviewed_subtitles(session, config, edited)

    assert parse_subtitle(project.chinese_srt)[0].text == "您好，世界。"
    assert project.chinese_ass in outputs
    assert "您好，世界。" in project.chinese_ass.read_text(encoding="utf-8")


def test_review_save_rebuilds_bilingual_tracks(tmp_path) -> None:
    project, config = _project_with_target(tmp_path, mode="bilingual_en_zh")
    session = load_subtitle_review_session(project, config)
    edited = [session.cues[0].model_copy(update={"text": "你好，朋友。"})]

    outputs = save_reviewed_subtitles(session, config, edited)

    assert project.bilingual_srt in outputs
    assert project.bilingual_ass in outputs
    assert "你好，朋友。" in project.bilingual_ass.read_text(encoding="utf-8")


def test_review_save_keeps_translation_resumable_and_invalidates_only_render(tmp_path):
    from youtube_localizer.state import PipelineState

    project, config = _project_with_target(tmp_path)
    state = PipelineState(project.state_file)
    with state.step("translate", input_hash="source", config_hash="config") as outputs:
        outputs.append(project.chinese_srt)
    session = load_subtitle_review_session(project, config)
    save_reviewed_subtitles(
        session, config, [session.cues[0].model_copy(update={"text": "人工修改"})]
    )
    state = PipelineState(project.state_file)
    assert state.can_skip(
        "translate", input_hash="source", config_hash="config", output_files=[project.chinese_srt]
    )


def test_retranslate_selected_paragraph_preserves_other_edited_paragraph_and_backup(
    tmp_path, monkeypatch
):
    from unittest.mock import MagicMock

    project, config = _project_with_target(tmp_path)
    config.translation.provider = "ollama"
    sources = [
        SubtitleCue(id=1, start_ms=0, end_ms=1000, text="First paragraph."),
        SubtitleCue(id=2, start_ms=10000, end_ms=11000, text="Second paragraph."),
    ]
    targets = [
        SubtitleCue(id=1, start_ms=0, end_ms=1000, text="第一段人工修改"),
        SubtitleCue(id=2, start_ms=10000, end_ms=11000, text="第二段旧译文"),
    ]
    write_srt(project.english_srt, sources)
    write_srt(project.chinese_srt, targets)
    provider = MagicMock()
    provider.translate_paragraph.return_value = "第二段新译文。"
    monkeypatch.setattr(
        "youtube_localizer.translation.ollama_local.LocalOllamaProvider", lambda **_kwargs: provider
    )
    cues = retranslate_paragraph(project, config, 2)
    assert cues[0].text == "第一段人工修改"
    assert cues[1].text == "第二段新译文。"
    assert provider.translate_paragraph.call_args.args[0] == [sources[1]]
    backups = list(project.subtitles.glob("*.before-retranslate-*.srt"))
    assert len(backups) == 1
    assert parse_subtitle(backups[0]) == targets
