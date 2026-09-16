from __future__ import annotations

import unicodedata
from pathlib import Path

from ..config import SubtitleConfig
from ..models import SubtitleCue
from ..utils.files import atomic_write_text
from ..utils.text import ms_to_ass

ASS_BASE_HEIGHT = 1080
ASS_BASE_WIDTH = 1920
ASS_HORIZONTAL_MARGIN = 30


def _escape_ass(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def ass_play_resolution(video_size: tuple[int, int] | None = None) -> tuple[int, int]:
    if not video_size or video_size[0] <= 0 or video_size[1] <= 0:
        return ASS_BASE_WIDTH, ASS_BASE_HEIGHT
    width, height = video_size
    return max(1, round(ASS_BASE_HEIGHT * width / height)), ASS_BASE_HEIGHT


def chinese_line_width(config: SubtitleConfig, video_size: tuple[int, int] | None = None) -> int:
    play_res_x, _ = ass_play_resolution(video_size)
    usable_width = max(1, play_res_x - 2 * ASS_HORIZONTAL_MARGIN)
    width_for_font = max(4, int(usable_width / max(1, config.font_size * 1.05)))
    return min(config.max_chinese_chars_per_line, width_for_font)


def _line_display_units(text: str) -> float:
    return sum(
        1.0 if unicodedata.east_asian_width(character) in {"W", "F"} else 0.55 for character in text
    )


def _fitted_chinese_font_size(
    text: str,
    config: SubtitleConfig,
    video_size: tuple[int, int] | None,
) -> int:
    play_res_x, _ = ass_play_resolution(video_size)
    usable_width = max(1, play_res_x - 2 * ASS_HORIZONTAL_MARGIN)
    longest_line = max((_line_display_units(line) for line in text.splitlines()), default=1.0)
    fitted = int(usable_width / max(1.0, longest_line * 0.82))
    return max(12, min(config.font_size, fitted))


def _header(config: SubtitleConfig, video_size: tuple[int, int] | None) -> str:
    play_res_x, play_res_y = ass_play_resolution(video_size)
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Chinese,{config.font},{config.font_size},&H00FFFFFF,&H000000FF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,{config.outline},{config.shadow},2,{ASS_HORIZONTAL_MARGIN},{ASS_HORIZONTAL_MARGIN},{config.margin_v},1
Style: English,{config.font},{config.english_font_size},&H00E8E8E8,&H000000FF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,{config.outline},{config.shadow},2,{ASS_HORIZONTAL_MARGIN},{ASS_HORIZONTAL_MARGIN},{config.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def subtitle_position(
    config: SubtitleConfig, video_size: tuple[int, int] | None = None
) -> tuple[int, int]:
    """Return the ASS canvas coordinate selected in the subtitle preview."""
    play_res_x, play_res_y = ass_play_resolution(video_size)
    return (
        round(play_res_x * config.position_x_percent / 100),
        round(play_res_y * config.position_y_percent / 100),
    )


def _position_override(config: SubtitleConfig, video_size: tuple[int, int] | None) -> str:
    x, y = subtitle_position(config, video_size)
    return rf"{{\an2\pos({x},{y})}}"


def write_ass(
    path: Path,
    cues: list[SubtitleCue],
    config: SubtitleConfig,
    *,
    bilingual_mode: str = "chinese",
    video_size: tuple[int, int] | None = None,
) -> None:
    if bilingual_mode not in {"chinese", "english"}:
        raise ValueError("Use write_bilingual_ass for bilingual subtitle tracks.")
    header = _header(config, video_size)
    events: list[str] = []
    default_style = "English" if bilingual_mode == "english" else "Chinese"
    position = _position_override(config, video_size)
    for cue in cues:
        text = _escape_ass(cue.text)
        if bilingual_mode != "english":
            fitted_font_size = _fitted_chinese_font_size(cue.text, config, video_size)
            if fitted_font_size < config.font_size:
                text = rf"{{\fs{fitted_font_size}}}{text}"
        events.append(
            f"Dialogue: 0,{ms_to_ass(cue.start_ms)},{ms_to_ass(cue.end_ms)},"
            f"{default_style},,0,0,0,,{position}{text}"
        )
    atomic_write_text(path, header + "\n".join(events) + "\n")


def write_bilingual_ass(
    path: Path,
    english: list[SubtitleCue],
    chinese: list[SubtitleCue],
    config: SubtitleConfig,
    *,
    mode: str,
    video_size: tuple[int, int] | None = None,
) -> None:
    if mode not in {"bilingual_en_zh", "bilingual_zh_en"}:
        raise ValueError(f"Unsupported bilingual ASS mode: {mode}")
    if len(english) != len(chinese):
        raise ValueError("English and Chinese cue counts differ.")

    events: list[str] = []
    position = _position_override(config, video_size)
    for en, zh in zip(english, chinese, strict=True):
        if en.id != zh.id or en.start_ms != zh.start_ms or en.end_ms != zh.end_ms:
            raise ValueError(f"Bilingual cue {en.id} has mismatched IDs or timestamps.")
        en_text = _escape_ass(en.text)
        zh_text = _escape_ass(zh.text)
        fitted_font_size = _fitted_chinese_font_size(zh.text, config, video_size)
        chinese_style = r"{\rChinese}"
        if fitted_font_size < config.font_size:
            chinese_style = rf"{{\rChinese\fs{fitted_font_size}}}"
        if mode == "bilingual_en_zh":
            text = rf"{position}{{\rEnglish}}{en_text}\N{chinese_style}{zh_text}"
            default_style = "English"
        else:
            text = rf"{position}{chinese_style}{zh_text}\N{{\rEnglish}}{en_text}"
            default_style = "Chinese"
        events.append(
            f"Dialogue: 0,{ms_to_ass(en.start_ms)},{ms_to_ass(en.end_ms)},"
            f"{default_style},,0,0,0,,{text}"
        )
    atomic_write_text(path, _header(config, video_size) + "\n".join(events) + "\n")
