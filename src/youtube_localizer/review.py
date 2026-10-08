"""Safe project subtitle review helpers used by the desktop editor."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import AppConfig, language_pair
from .errors import LocalizerError
from .models import ProjectPaths, SubtitleCue
from .pipeline import _target_ass, _target_subtitle, find_source_video, load_project_metadata
from .rendering.preview import render_preview
from .subtitles.bilingual import align_bilingual_tracks, combine_bilingual
from .subtitles.parser import parse_subtitle, write_srt
from .subtitles.styling import write_ass, write_bilingual_ass


def _record_reviewed_translation(project: ProjectPaths, target: Path) -> None:
    from .state import PipelineState, build_output_artifact

    if not project.state_file.is_file():
        return
    state = PipelineState(project.state_file)
    if record := state.data.steps.get("translate"):
        record.output_files = [str(target.resolve())]
        record.output_artifacts = [build_output_artifact(target)]
    state.data.steps.pop("render", None)
    state.mark_status("incomplete")


def retranslate_paragraph(
    project: ProjectPaths, config: AppConfig, cue_id: int
) -> list[SubtitleCue]:
    """Translate only the source paragraph covering the selected target cue, preserving edits elsewhere."""
    from .pipeline import _group_local_ai_paragraphs, _source_subtitle, _translation_context
    from .resource_gate import heavy_workload_slot
    from .translation.cache import TranslationCache
    from .translation.glossary import load_glossary
    from .translation.offline import (
        LocalOfflineProvider,
        group_paragraph_cues,
        paragraph_translation_to_cues,
        translate_cues_contextually,
    )
    from .translation.ollama_local import LocalOllamaProvider
    from .translation.openai_compatible import OpenAICompatibleProvider

    session = load_subtitle_review_session(project, config)
    selected = next((cue for cue in session.cues if cue.id == cue_id), None)
    if selected is None:
        raise LocalizerError("所选字幕不存在，请重新打开审核。")
    source_code, target_code = language_pair(config.translation.direction)
    sources = parse_subtitle(_source_subtitle(project, config))
    groups = (
        _group_local_ai_paragraphs(sources, source_code=source_code)
        if config.translation.provider == "ollama"
        else group_paragraph_cues(sources, source_code=source_code)
    )
    midpoint = (selected.start_ms + selected.end_ms) / 2
    group = next(
        (items for items in groups if items[0].start_ms <= midpoint < items[-1].end_ms), None
    )
    if group is None:
        raise LocalizerError("所选字幕没有对应原文段落。")
    glossary_path = Path(config.translation.glossary_file)
    if not glossary_path.is_absolute():
        glossary_path = next(
            (
                path
                for path in (project.root / glossary_path, Path.cwd() / glossary_path)
                if path.is_file()
            ),
            project.root / glossary_path,
        )
    context = _translation_context(load_project_metadata(project), load_glossary(glossary_path))
    project.temp.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(prefix="retranslate-", dir=project.temp) as directory,
        heavy_workload_slot("selected paragraph translation"),
    ):
        cache = TranslationCache(Path(directory))
        settings = config.translation
        if settings.provider == "ollama":
            provider = LocalOllamaProvider(
                endpoint=settings.ollama_endpoint,
                model=settings.ollama_model,
                auto_pull=settings.ollama_auto_pull,
                cache=cache,
                source_code=source_code,
                target_code=target_code,
                context_tokens=settings.ollama_context_tokens,
                timeout=settings.ollama_timeout_seconds,
            )
            text = provider.translate_paragraph(group, context)
            replacement = paragraph_translation_to_cues(
                text,
                group,
                target_code=target_code,
                first_id=1,
                max_characters=config.subtitles.max_chinese_chars_per_line
                * config.subtitles.max_lines
                if target_code == "zh"
                else 84,
            )
        elif settings.provider == "offline":
            provider = LocalOfflineProvider(
                model_directory=(
                    settings.offline_zh_en_model_directory
                    if source_code == "zh"
                    else settings.offline_model_directory
                ).expanduser(),
                model_url=settings.offline_zh_en_model_url
                if source_code == "zh"
                else settings.offline_model_url,
                auto_download=settings.offline_auto_download,
                device=settings.offline_device,
                compute_type=settings.offline_compute_type,
                cache=cache,
                source_code=source_code,
                target_code=target_code,
            )
            replacement = translate_cues_contextually(
                provider,
                group,
                context,
                source_code=source_code,
                target_code=target_code,
                batch_size=settings.batch_size,
            )
        elif settings.provider == "openai-compatible":
            provider = OpenAICompatibleProvider(
                endpoint=settings.endpoint,
                model=settings.model,
                cache=cache,
                source_code=source_code,
                target_code=target_code,
            )
            replacement = provider.translate_batch(group, context)
        else:
            raise LocalizerError("人工翻译模式请直接编辑文字；重译需要本地翻译模型或 API。")
    start, end = group[0].start_ms, group[-1].end_ms
    retained = [cue for cue in session.cues if not start <= (cue.start_ms + cue.end_ms) / 2 < end]
    updated = sorted([*retained, *replacement], key=lambda cue: (cue.start_ms, cue.end_ms))
    updated = [cue.model_copy(update={"id": index}) for index, cue in enumerate(updated, 1)]
    backup = session.subtitle_path.with_name(
        session.subtitle_path.stem
        + ".before-retranslate-"
        + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        + ".srt"
    )
    write_srt(backup, session.cues)
    save_reviewed_subtitles(session, config, updated)
    return updated


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
    _record_reviewed_translation(session.project, target_path)
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
