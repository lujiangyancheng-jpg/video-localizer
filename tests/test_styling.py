from __future__ import annotations

from youtube_localizer.config import SubtitleConfig
from youtube_localizer.models import SubtitleCue
from youtube_localizer.subtitles.styling import (
    ass_play_resolution,
    chinese_line_width,
    subtitle_position,
    write_ass,
    write_bilingual_ass,
)


def test_portrait_ass_canvas_and_line_width_follow_video_aspect_ratio(tmp_path) -> None:
    config = SubtitleConfig()
    video_size = (360, 640)
    path = tmp_path / "portrait.ass"

    write_ass(
        path,
        [SubtitleCue(id=1, start_ms=0, end_ms=1000, text="竖屏字幕")],
        config,
        video_size=video_size,
    )

    content = path.read_text(encoding="utf-8")
    assert ass_play_resolution(video_size) == (608, 1080)
    assert chinese_line_width(config, video_size) == 10
    assert "PlayResX: 608" in content
    assert "PlayResY: 1080" in content


def test_bilingual_ass_keeps_all_chinese_lines_in_chinese_style(tmp_path) -> None:
    english = [SubtitleCue(id=1, start_ms=0, end_ms=1000, text="English line")]
    chinese = [SubtitleCue(id=1, start_ms=0, end_ms=1000, text="中文第一行\n中文第二行")]
    path = tmp_path / "bilingual.ass"

    write_bilingual_ass(
        path,
        english,
        chinese,
        SubtitleConfig(),
        mode="bilingual_zh_en",
    )

    dialogue = next(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("Dialogue:")
    )
    assert r"{\rChinese}中文第一行\N中文第二行\N{\rEnglish}English line" in dialogue
    assert r"\N{\rEnglish}中文第二行" not in dialogue


def test_extreme_portrait_line_gets_a_per_cue_font_override(tmp_path) -> None:
    path = tmp_path / "long-portrait.ass"

    write_ass(
        path,
        [SubtitleCue(id=1, start_ms=0, end_ms=1000, text="中" * 50)],
        SubtitleConfig(),
        video_size=(360, 640),
    )

    dialogue = next(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("Dialogue:")
    )
    assert r"{\fs" in dialogue


def test_ass_uses_the_selected_preview_position_for_every_cue(tmp_path) -> None:
    config = SubtitleConfig(position_x_percent=25, position_y_percent=72)
    path = tmp_path / "positioned.ass"

    write_ass(
        path,
        [SubtitleCue(id=1, start_ms=0, end_ms=1000, text="可拖动的位置")],
        config,
        video_size=(360, 640),
    )

    dialogue = next(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("Dialogue:")
    )
    assert subtitle_position(config, (360, 640)) == (152, 778)
    assert r"{\an2\pos(152,778)}" in dialogue
