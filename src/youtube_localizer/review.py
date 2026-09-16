"""Safe project subtitle review helpers used by the desktop editor."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig, language_pair
from .errors import LocalizerError
from .models import ProjectPaths, SubtitleCue
from .pipeline import _target_ass, _target_subtitle, find_source_video, load_project_metadata
from .rendering.preview import render_preview
from .subtitles.bilingual import align_bilingual_tracks, combine_bilingual
from .subtitles.parser import parse_subtitle, write_srt
from .subtitles.styling import write_ass, write_bilingual_ass


@dataclass(frozen=True)
class SubtitleReviewSession:
    """The editable target track and its project-relative paths."""

    project: ProjectPaths
    subtitle_path: Path
    ass_path: Path
    cues: list[SubtitleCue]


def _video_size(project: ProjectPaths) -> tuple[int, int] | None:
    metadata = load_project_metadata(project)
    if metadata.width and metadata.height:
        return metadata.width, metadata.height
    return None


def load_subtitle_review_session(project: ProjectPaths, config: AppConfig) -> SubtitleReviewSession:
    subtitle_path = _target_subtitle(project, config)
    if not subtitle_path.is_file():
        raise LocalizerError("目标字幕尚未生成，无法开始审核。请先完成翻译或导入字幕。")
    return SubtitleReviewSession(
        project=project,
        subtitle_path=subtitle_path,
        ass_path=_target_ass(project, config),
        cues=parse_subtitle(subtitle_path),
    )


def save_reviewed_subtitles(
    session: SubtitleReviewSession,
    config: AppConfig,
    cues: list[SubtitleCue],
) -> list[Path]:
    """Save edited target cues and rebuild every styled track affected by the edit."""
    if not cues:
        raise LocalizerError("字幕不能为空。")
    source_code, target_code = language_pair(config.translation.direction)
    target_path = _target_subtitle(session.project, config)
    if target_path != session.subtitle_path:
        raise LocalizerError("项目配置已变化；请重新打开字幕审核。")
    for cue in cues:
        cue.validate_timing()
    write_srt(target_path, cues)
    video_size = _video_size(session.project)
    outputs = [target_path]
    if config.subtitle_mode == "chinese":
        write_ass(
            _target_ass(session.project, config),
            cues,
            config.subtitles,
            bilingual_mode="chinese" if target_code == "zh" else "english",
            video_size=video_size,
        )
        return [*outputs, _target_ass(session.project, config)]

    if {source_code, target_code} != {"en", "zh"}:
        raise LocalizerError("双语字幕审核仅适用于中英互译项目。")
    source_path = session.project.subtitle_srt(source_code)
    if not source_path.is_file():
        raise LocalizerError("原语言字幕缺失，无法重建双语字幕。")
    source_cues = parse_subtitle(source_path)
    english, chinese = (source_cues, cues) if source_code == "en" else (cues, source_cues)
    english, chinese = align_bilingual_tracks(english, chinese, reference_language=target_code)
    bilingual = combine_bilingual(english, chinese, mode=config.subtitle_mode)
    write_srt(session.project.bilingual_srt, bilingual)
    write_bilingual_ass(
        session.project.bilingual_ass,
        english,
        chinese,
        config.subtitles,
        mode=config.subtitle_mode,
        video_size=video_size,
    )
    return [*outputs, session.project.bilingual_srt, session.project.bilingual_ass]


def render_subtitle_review_preview(
    session: SubtitleReviewSession,
    config: AppConfig,
    *,
    start_seconds: float,
    duration_seconds: float = 12,
) -> Path:
    """Render a short clip after a save, never touching the final rendered output."""
    if start_seconds < 0 or duration_seconds <= 0:
        raise ValueError("Preview start and duration must be positive.")
    subtitle = _target_ass(session.project, config)
    if config.subtitle_mode != "chinese":
        subtitle = session.project.bilingual_ass
    if not subtitle.is_file():
        raise LocalizerError("请先保存字幕修改，再生成预览。")
    metadata = load_project_metadata(session.project)
    output = session.project.rendered / f"review_preview_{start_seconds:g}_{duration_seconds:g}.mp4"
    return render_preview(
        find_source_video(session.project),
        subtitle,
        output,
        config.render,
        source_audio_codec=metadata.audio_codec,
        start=start_seconds,
        duration=duration_seconds,
    )
