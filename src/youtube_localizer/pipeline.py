from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any

from .config import FFMPEG_LANGUAGE_CODES, AppConfig, language_pair, validate_config_data
from .download.direct import (
    direct_media_id,
    download_direct_media,
    inspect_direct_media,
    is_direct_media_candidate_url,
    is_direct_media_url,
)
from .download.local import import_local, inspect_local
from .download.metadata import metadata_from_probe, probe_media
from .download.webpage import inspect_webpage_media, is_webpage_url, webpage_media_id
from .download.youtube import (
    download_youtube,
    inspect_youtube,
    is_youtube_url,
    save_raw_metadata,
    save_thumbnail,
    youtube_video_id,
)
from .enhancement.super_resolution import enhance_video, super_resolution_target_height
from .errors import InputValidationError, LocalizerError, ProjectExistsError
from .inspection_cache import cached_raw_metadata, load_cached_inspection
from .logging_config import configure_logging
from .models import ProjectPaths, SourceMetadata, SubtitleCue
from .preflight import build_job_preflight
from .publishing.metadata_generator import generate_publishing_assets
from .publishing.rights import generate_rights_assets, validate_rights
from .rendering.ffmpeg import render_hardsub, render_softsub
from .rendering.media_warnings import rendering_media_warnings
from .rendering.validation import validate_rendered_video
from .reporting import build_report, write_report
from .resource_gate import heavy_workload_slot
from .state import PipelineState
from .subtitles.bilingual import align_bilingual_tracks, combine_bilingual
from .subtitles.parser import parse_subtitle, write_srt
from .subtitles.quality import audit_subtitles, select_review_cues
from .subtitles.readability import readability_pass
from .subtitles.styling import chinese_line_width, write_ass, write_bilingual_ass
from .transcription.audio import extract_transcription_audio
from .transcription.whisper_engine import transcribe_audio
from .translation.base import TranslationContext
from .translation.cache import TranslationCache
from .translation.glossary import load_glossary
from .translation.manual import ManualExportProvider
from .translation.offline import (
    LocalOfflineProvider,
    group_paragraph_cues,
    paragraph_translation_to_cues,
    translate_cues_contextually,
)
from .translation.ollama_local import LocalOllamaProvider
from .translation.openai_compatible import OpenAICompatibleProvider
from .utils.files import (
    atomic_write_json,
    available_bytes,
    load_json,
    remove_project,
    sanitize_filename,
)
from .utils.hashing import hash_file, hash_text, stable_hash

LOGGER = logging.getLogger(__name__)
LOCAL_AI_GROUPING = {
    "max_cues": 36,
    "max_characters": 1_600,
    "max_gap_ms": 1_800,
    "max_duration_ms": 75_000,
    "target_sentences": 8,
    "minimum_duration_ms": 30_000,
}


def _group_local_ai_paragraphs(
    cues: list[SubtitleCue], *, source_code: str
) -> list[list[SubtitleCue]]:
    return group_paragraph_cues(cues, source_code=source_code, **LOCAL_AI_GROUPING)
VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
FORCE_STEPS = {
    "acquire",
    "english_subtitles",
    "chinese_subtitles",
    "transcribe",
    "translate",
    "enhance",
    "render",
}


@dataclass
class PipelineResult:
    project: ProjectPaths
    status: str
    outputs: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def find_source_video(project: ProjectPaths) -> Path:
    candidates = [
        path
        for path in project.source.glob("source_video.*")
        if path.suffix.lower() in VIDEO_SUFFIXES
    ]
    if not candidates:
        raise LocalizerError(f"No imported/downloaded source video exists in {project.source}")
    return max(candidates, key=lambda path: path.stat().st_size)


def load_project_metadata(project: ProjectPaths) -> SourceMetadata:
    if not project.metadata.is_file():
        raise LocalizerError(f"Project metadata is missing: {project.metadata}")
    return SourceMetadata.model_validate(load_json(project.metadata))


def _stored_metadata(metadata: SourceMetadata) -> dict[str, Any]:
    """Return project metadata without persisting a resolved webpage media signature."""
    if metadata.source_type == "webpage_media":
        metadata = metadata.model_copy(update={"source_url": None})
    return metadata.model_dump(mode="json")


def save_project_config(project: ProjectPaths, config: AppConfig) -> Path:
    path = project.root / "config.resolved.json"
    atomic_write_json(path, config.model_dump(mode="json"))
    return path


def load_project_config(project: ProjectPaths) -> AppConfig:
    path = project.root / "config.resolved.json"
    if path.is_file():
        return validate_config_data(load_json(path))
    return AppConfig()


def _source_identifier(value: str) -> str:
    video_id = youtube_video_id(value)
    if video_id:
        return video_id
    if is_direct_media_candidate_url(value):
        return direct_media_id(value)
    if is_webpage_url(value):
        return webpage_media_id(value)
    path = Path(value).expanduser().resolve()
    return hash_text(str(path).casefold())[:10]


def _find_existing_project(output_root: Path, identifier: str) -> Path | None:
    if not output_root.is_dir():
        return None
    matches = [
        path
        for path in output_root.iterdir()
        if path.is_dir() and path.name.endswith(f"_{identifier}")
    ]
    if len(matches) > 1:
        raise LocalizerError(
            f"Multiple projects end with identifier {identifier}: "
            + ", ".join(str(path) for path in matches)
        )
    return matches[0] if matches else None


def _inspect_input(value: str) -> tuple[SourceMetadata, dict[str, Any] | None]:
    if cached := load_cached_inspection(value):
        LOGGER.info("Reusing fresh desktop media inspection cache.")
        return cached, None if cached.source_type == "local" else cached_raw_metadata(cached)
    if is_youtube_url(value):
        return inspect_youtube(value)
    if is_direct_media_url(value):
        return inspect_direct_media(value)
    if is_direct_media_candidate_url(value):
        try:
            return inspect_direct_media(value)
        except InputValidationError as direct_error:
            try:
                return inspect_webpage_media(value)
            except InputValidationError as page_error:
                if "没有返回 HTML 播放页" in str(page_error):
                    raise direct_error from page_error
                raise
    if is_webpage_url(value):
        return inspect_webpage_media(value)
    metadata = inspect_local(Path(value))
    return metadata, None


def prepare_project(
    value: str,
    config: AppConfig,
    *,
    resume: bool = False,
    overwrite: bool = False,
) -> tuple[ProjectPaths, SourceMetadata, dict[str, Any] | None]:
    if resume and overwrite:
        raise InputValidationError("--resume and --overwrite cannot be used together.")
    output_root = config.output_directory.expanduser().resolve()
    identifier = _source_identifier(value)
    existing = _find_existing_project(output_root, identifier)
    if existing and resume:
        project = ProjectPaths(existing)
        project.create()
        metadata = load_project_metadata(project)
        if metadata.source_type == "direct_media" and is_direct_media_candidate_url(value):
            # Signed media URLs often expire. Their stable path maps to the same project, while
            # this refreshes the time-limited query string before the resumed download.
            metadata = metadata.model_copy(update={"source_input": value, "source_url": value})
            atomic_write_json(project.metadata, _stored_metadata(metadata))
        save_project_config(project, config)
        return project, metadata, None

    metadata, raw_info = _inspect_input(value)
    name = f"{sanitize_filename(metadata.title, max_length=80)}_{metadata.video_id}"
    root = output_root / name
    if root.exists():
        if overwrite:
            remove_project(root, output_root)
        else:
            raise ProjectExistsError(
                f"Project already exists: {root}. Use --resume to continue it or --overwrite "
                "to replace it."
            )
    project = ProjectPaths(root)
    project.create()
    atomic_write_json(project.metadata, _stored_metadata(metadata))
    save_project_config(project, config)
    return project, metadata, raw_info


def _input_hash(metadata: SourceMetadata, fallback_video: Path | None = None) -> str:
    if metadata.source_type == "local":
        path = Path(metadata.source_input)
        if path.is_file():
            return hash_file(path)
        if fallback_video and fallback_video.is_file():
            LOGGER.warning(
                "The original local file is no longer available; resuming from the safe "
                "project copy."
            )
            return hash_file(fallback_video)
        raise InputValidationError(f"Original local source no longer exists: {path}")
    source_reference = (
        metadata.source_input
        if metadata.source_type == "webpage_media"
        else metadata.source_url or metadata.source_input
    )
    return stable_hash(
        {
            "source_url": source_reference,
            "video_id": metadata.video_id,
        }
    )


def _translation_context(metadata: SourceMetadata, glossary: dict[str, str]) -> TranslationContext:
    return TranslationContext(
        title=metadata.title,
        channel=metadata.channel,
        description=metadata.description,
        source_url=(
            metadata.source_input
            if metadata.source_type == "webpage_media"
            else metadata.source_url or metadata.source_input
        ),
        glossary=glossary,
    )


def _video_size(metadata: SourceMetadata | None) -> tuple[int, int] | None:
    if metadata and metadata.width and metadata.height:
        return metadata.width, metadata.height
    return None


def _language_pair(config: AppConfig) -> tuple[str, str]:
    return language_pair(config.translation.direction)


def _source_subtitle(project: ProjectPaths, config: AppConfig) -> Path:
    source_code, _ = _language_pair(config)
    return project.subtitle_srt(source_code)


def _target_subtitle(project: ProjectPaths, config: AppConfig) -> Path:
    _, target_code = _language_pair(config)
    return project.subtitle_srt(target_code)


def _target_ass(project: ProjectPaths, config: AppConfig) -> Path:
    _, target_code = _language_pair(config)
    return project.subtitle_ass(target_code)


def rendered_output(project: ProjectPaths, config: AppConfig) -> Path:
    _, target_code = _language_pair(config)
    return project.hardsub_output(target_code)


def enhancement_config_hash(config: AppConfig, target_height: int) -> str:
    return stable_hash({
        "pipeline_version": 2, "enhancement": config.enhancement,
        "target_height": target_height, "render": config.render,
    })


def render_source(project: ProjectPaths, config: AppConfig) -> Path:
    source = find_source_video(project)
    if config.enhancement.mode == "off":
        return source
    metadata = load_project_metadata(project)
    target = super_resolution_target_height(metadata.height or 0, config.render, config.enhancement)
    if target <= (metadata.height or 0):
        return source
    if PipelineState(project.state_file).can_skip(
        "enhance", input_hash=hash_file(source),
        config_hash=enhancement_config_hash(config, target),
        output_files=[project.enhanced_source],
    ):
        return project.enhanced_source
    raise LocalizerError("Enhanced video does not match current settings. Resume processing first.")


def softsub_output(project: ProjectPaths, config: AppConfig) -> Path:
    _, target_code = _language_pair(config)
    return project.softsub_output(target_code)


def _write_localized_subtitles(
    project: ProjectPaths,
    english: list[SubtitleCue],
    chinese: list[SubtitleCue],
    config: AppConfig,
    metadata: SourceMetadata | None = None,
    *,
    source_cues: list[SubtitleCue] | None = None,
    target_cues: list[SubtitleCue] | None = None,
) -> tuple[list[Path], list[str]]:
    video_size = _video_size(metadata)
    source_code, target_code = _language_pair(config)
    if target_cues is not None:
        # Extra language pairs have no special styling or bilingual layout.  The local LLM/API
        # output is already projected back onto the original cue timings.
        if config.subtitle_mode != "chinese":
            raise LocalizerError(
                "Bilingual subtitle layouts are available only for Chinese-English translation."
            )
        readable = target_cues
        issues = []
        if target_code == "zh":
            readable, issues = readability_pass(
                target_cues,
                width=chinese_line_width(config.subtitles, video_size),
                max_lines=config.subtitles.max_lines,
            )
        target_srt = _target_subtitle(project, config)
        target_ass = _target_ass(project, config)
        write_srt(target_srt, readable)
        write_ass(
            target_ass,
            readable,
            config.subtitles,
            bilingual_mode="chinese" if target_code == "zh" else "english",
            video_size=video_size,
        )
        return [target_srt, target_ass], [f"Cue {issue.cue_id}: {issue.message}" for issue in issues]

    if config.translation.direction == "zh-to-en":
        write_srt(project.english_srt, english)
        write_ass(
            project.english_ass,
            english,
            config.subtitles,
            bilingual_mode="english",
            video_size=video_size,
        )
        outputs = [project.english_srt, project.english_ass]
        if config.subtitle_mode != "chinese":
            bilingual_english, bilingual_chinese = align_bilingual_tracks(
                english,
                chinese,
                reference_language="en",
            )
            bilingual = combine_bilingual(
                bilingual_english, bilingual_chinese, mode=config.subtitle_mode
            )
            write_srt(project.bilingual_srt, bilingual)
            write_bilingual_ass(
                project.bilingual_ass,
                bilingual_english,
                bilingual_chinese,
                config.subtitles,
                mode=config.subtitle_mode,
                video_size=video_size,
            )
            outputs.extend([project.bilingual_srt, project.bilingual_ass])
        return outputs, []

    readable, issues = readability_pass(
        chinese,
        width=chinese_line_width(config.subtitles, video_size),
        max_lines=config.subtitles.max_lines,
    )
    write_srt(project.chinese_srt, readable)
    chinese_ass = project.subtitles / "chinese.ass"
    write_ass(chinese_ass, readable, config.subtitles, video_size=video_size)
    outputs = [project.chinese_srt, chinese_ass]
    if config.subtitle_mode != "chinese":
        bilingual_english, bilingual_chinese = align_bilingual_tracks(
            english,
            readable,
            reference_language="zh",
        )
        bilingual = combine_bilingual(
            bilingual_english, bilingual_chinese, mode=config.subtitle_mode
        )
        write_srt(project.bilingual_srt, bilingual)
        write_bilingual_ass(
            project.bilingual_ass,
            bilingual_english,
            bilingual_chinese,
            config.subtitles,
            mode=config.subtitle_mode,
            video_size=video_size,
        )
        outputs.extend([project.bilingual_srt, project.bilingual_ass])
    warnings = [f"Cue {issue.cue_id}: {issue.message}" for issue in issues]
    return outputs, warnings


def export_manual_translation(
    project: ProjectPaths,
    config: AppConfig,
    metadata: SourceMetadata | None = None,
) -> list[Path]:
    metadata = metadata or load_project_metadata(project)
    glossary_path = Path(config.translation.glossary_file)
    if not glossary_path.is_absolute():
        candidates = [project.root / glossary_path, Path.cwd() / glossary_path]
        glossary_path = next((path for path in candidates if path.is_file()), candidates[0])
    glossary = load_glossary(glossary_path)
    source_code, target_code = _language_pair(config)
    source_cues = parse_subtitle(_source_subtitle(project, config))
    provider = ManualExportProvider(source_code=source_code, target_code=target_code)
    return provider.export(
        source_cues,
        _translation_context(metadata, glossary),
        project.translation_chunks,
        batch_size=config.translation.batch_size,
    )


def translate_with_api(
    project: ProjectPaths,
    config: AppConfig,
    metadata: SourceMetadata | None = None,
) -> tuple[list[Path], list[str]]:
    metadata = metadata or load_project_metadata(project)
    source_code, target_code = _language_pair(config)
    source_cues = parse_subtitle(_source_subtitle(project, config))
    glossary_path = Path(config.translation.glossary_file)
    if not glossary_path.is_absolute():
        glossary_path = project.root / glossary_path
    glossary = load_glossary(glossary_path)
    provider = OpenAICompatibleProvider(
        endpoint=config.translation.endpoint,
        model=config.translation.model,
        cache=TranslationCache(project.temp / "translation_cache"),
        source_code=source_code,
        target_code=target_code,
    )
    translated: list[SubtitleCue] = []
    batch_size = config.translation.batch_size
    base_context = _translation_context(metadata, glossary)
    for offset in range(0, len(source_cues), batch_size):
        batch = source_cues[offset : offset + batch_size]
        nearby = " ".join(cue.text for cue in source_cues[max(0, offset - 3) : offset])
        batch_context = TranslationContext(
            title=base_context.title,
            channel=base_context.channel,
            description=(
                base_context.description
                + (f"\nNearby preceding subtitle context: {nearby}" if nearby else "")
            ),
            source_url=base_context.source_url,
            glossary=base_context.glossary,
        )
        translated.extend(provider.translate_batch(batch, batch_context))
    if target_code not in {"en", "zh"}:
        return _write_localized_subtitles(
            project,
            [],
            [],
            config,
            metadata,
            source_cues=source_cues,
            target_cues=translated,
        )
    english = source_cues if source_code == "en" else translated
    chinese = source_cues if source_code == "zh" else translated
    return _write_localized_subtitles(project, english, chinese, config, metadata)


def translate_with_offline(
    project: ProjectPaths,
    config: AppConfig,
    metadata: SourceMetadata | None = None,
) -> tuple[list[Path], list[str]]:
    with heavy_workload_slot("offline translation"):
        return _translate_with_offline(project, config, metadata)


def _translate_with_offline(
    project: ProjectPaths,
    config: AppConfig,
    metadata: SourceMetadata | None = None,
) -> tuple[list[Path], list[str]]:
    metadata = metadata or load_project_metadata(project)
    source_code, target_code = _language_pair(config)
    source_cues = parse_subtitle(_source_subtitle(project, config))
    glossary_path = Path(config.translation.glossary_file)
    if not glossary_path.is_absolute():
        candidates = [project.root / glossary_path, Path.cwd() / glossary_path]
        glossary_path = next((path for path in candidates if path.is_file()), candidates[0])
    glossary = load_glossary(glossary_path)
    if source_code == "zh":
        configured_directory = config.translation.offline_zh_en_model_directory
        model_url = config.translation.offline_zh_en_model_url
    else:
        configured_directory = config.translation.offline_model_directory
        model_url = config.translation.offline_model_url
    model_directory = configured_directory.expanduser()
    if not model_directory.is_absolute():
        model_directory = (Path.cwd() / model_directory).resolve()
    provider = LocalOfflineProvider(
        model_directory=model_directory,
        model_url=model_url,
        auto_download=config.translation.offline_auto_download,
        device=config.translation.offline_device,
        compute_type=config.translation.offline_compute_type,
        cache=TranslationCache(project.temp / "translation_cache"),
        source_code=source_code,
        target_code=target_code,
    )
    batch_size = config.translation.batch_size
    context = _translation_context(metadata, glossary)
    translated = translate_cues_contextually(
        provider,
        source_cues,
        context,
        source_code=source_code,
        target_code=target_code,
        batch_size=batch_size,
    )
    if target_code not in {"en", "zh"}:
        raise LocalizerError(
            "The fast offline translator only supports English-Chinese and Chinese-English. "
            "Choose local AI or an API for other languages."
        )
    english = source_cues if source_code == "en" else translated
    chinese = source_cues if source_code == "zh" else translated
    return _write_localized_subtitles(project, english, chinese, config, metadata)


def translate_with_local_ai(
    project: ProjectPaths,
    config: AppConfig,
    metadata: SourceMetadata | None = None,
) -> tuple[list[Path], list[str]]:
    with heavy_workload_slot("local AI paragraph translation"):
        return _translate_with_local_ai(project, config, metadata)


def _translate_with_local_ai(
    project: ProjectPaths,
    config: AppConfig,
    metadata: SourceMetadata | None = None,
) -> tuple[list[Path], list[str]]:
    metadata = metadata or load_project_metadata(project)
    source_code, target_code = _language_pair(config)
    source_cues = parse_subtitle(_source_subtitle(project, config))
    glossary_path = Path(config.translation.glossary_file)
    if not glossary_path.is_absolute():
        candidates = [project.root / glossary_path, Path.cwd() / glossary_path]
        glossary_path = next((path for path in candidates if path.is_file()), candidates[0])
    context = _translation_context(metadata, load_glossary(glossary_path))
    provider = LocalOllamaProvider(
        endpoint=config.translation.ollama_endpoint,
        model=config.translation.ollama_model,
        auto_pull=config.translation.ollama_auto_pull,
        cache=TranslationCache(project.temp / "translation_cache"),
        source_code=source_code,
        target_code=target_code,
        context_tokens=config.translation.ollama_context_tokens,
        timeout=config.translation.ollama_timeout_seconds,
    )
    # Fewer, complete spoken paragraphs reduce local-model request overhead substantially.
    # The limits remain comfortably below Qwen3:4b's context and output budget.
    paragraphs = _group_local_ai_paragraphs(source_cues, source_code=source_code)
    translated: list[SubtitleCue] = []
    next_id = 1
    max_characters = (
        config.subtitles.max_chinese_chars_per_line * config.subtitles.max_lines
        if target_code == "zh"
        else 84
    )
    for index, paragraph in enumerate(paragraphs, start=1):
        LOGGER.info("Local AI translating paragraph %s/%s…", index, len(paragraphs))
        paragraph_translation = provider.translate_paragraph(paragraph, context)
        paragraph_cues = paragraph_translation_to_cues(
            paragraph_translation,
            paragraph,
            target_code=target_code,
            first_id=next_id,
            max_characters=max_characters,
        )
        translated.extend(paragraph_cues)
        next_id += len(paragraph_cues)
    if target_code not in {"en", "zh"}:
        return _write_localized_subtitles(
            project,
            [],
            [],
            config,
            metadata,
            source_cues=source_cues,
            target_cues=translated,
        )
    english = source_cues if source_code == "en" else translated
    chinese = source_cues if source_code == "zh" else translated
    return _write_localized_subtitles(project, english, chinese, config, metadata)


def render_project(project: ProjectPaths, config: AppConfig) -> Path:
    if config.subtitle_mode == "download_only":
        raise LocalizerError(
            "This project is configured for direct download without subtitles; "
            "change subtitle_mode before rendering."
        )
    metadata = load_project_metadata(project)
    source_code, target_code = _language_pair(config)
    for warning in rendering_media_warnings(metadata):
        LOGGER.warning("%s", warning)
    source = render_source(project, config)
    if config.subtitle_mode == "chinese":
        subtitle = _target_ass(project, config)
        if not subtitle.is_file():
            target = parse_subtitle(_target_subtitle(project, config))
            target_mode = "chinese" if target_code == "zh" else "english"
            write_ass(
                subtitle,
                target,
                config.subtitles,
                bilingual_mode=target_mode,
                video_size=_video_size(metadata),
            )
    else:
        if {source_code, target_code} != {"en", "zh"}:
            raise LocalizerError(
                "Bilingual subtitle layouts are available only for Chinese-English translation."
            )
        subtitle = project.bilingual_ass
        if not subtitle.is_file():
            english = parse_subtitle(project.english_srt)
            chinese = parse_subtitle(project.chinese_srt)
            english, chinese = align_bilingual_tracks(
                english,
                chinese,
                reference_language=target_code,
            )
            write_bilingual_ass(
                subtitle,
                english,
                chinese,
                config.subtitles,
                mode=config.subtitle_mode,
                video_size=_video_size(metadata),
            )
    output = rendered_output(project, config)
    render_hardsub(
        source,
        subtitle,
        output,
        config.render,
        source_audio_codec=metadata.audio_codec,
        expected_duration=metadata.duration,
        source_frame_rate=metadata.frame_rate,
    )
    validate_rendered_video(output, expected_duration=metadata.duration)
    return output


def render_softsub_project(project: ProjectPaths, config: AppConfig) -> Path:
    """Create an MP4 with a selectable subtitle track after the styled MP4 is rendered."""
    if config.subtitle_mode == "download_only":
        raise LocalizerError("This project is configured for direct download without subtitles.")
    metadata = load_project_metadata(project)
    subtitle = (
        _target_subtitle(project, config)
        if config.subtitle_mode == "chinese"
        else project.bilingual_srt
    )
    if not subtitle.is_file():
        raise LocalizerError("Subtitle file is not ready for soft-subtitle muxing.")
    _, target_code = _language_pair(config)
    output = softsub_output(project, config)
    render_softsub(
        render_source(project, config),
        subtitle,
        output,
        language=FFMPEG_LANGUAGE_CODES[target_code],
    )
    validate_rendered_video(output, expected_duration=metadata.duration)
    return output


def process_pipeline(
    value: str,
    config: AppConfig,
    *,
    resume: bool = False,
    overwrite: bool = False,
    force_steps: set[str] | None = None,
    verbose: bool = False,
) -> PipelineResult:
    force_steps = force_steps or set()
    unknown_force_steps = force_steps - FORCE_STEPS
    if unknown_force_steps:
        raise InputValidationError(
            "Unknown --force-step value(s): "
            + ", ".join(sorted(unknown_force_steps))
            + ". Expected one of: "
            + ", ".join(sorted(FORCE_STEPS))
            + "."
        )
    project, metadata, raw_info = prepare_project(value, config, resume=resume, overwrite=overwrite)
    configure_logging(project.logs / "pipeline.log", verbose=verbose)
    LOGGER.info("Project workspace: %s", project.root)
    state = PipelineState(project.state_file, source_input=value)
    preflight = build_job_preflight(metadata, config)
    atomic_write_json(project.logs / "preflight.json", preflight.as_dict())
    for warning in preflight.warnings:
        LOGGER.warning("Preflight warning: %s", warning)
    LOGGER.info(
        "Preflight ready: package=%s; workspace estimate=%.1f GiB; %s; %s",
        preflight.package,
        preflight.estimated_working_bytes / 1024**3,
        preflight.transcription_plan,
        preflight.encoding_plan,
    )
    if preflight.warnings:
        state.data.warnings = list(dict.fromkeys([*state.data.warnings, *preflight.warnings]))
        state.save()
    if preflight.blockers:
        state.data.warnings = list(dict.fromkeys([*state.data.warnings, *preflight.blockers]))
        state.mark_status("preflight_blocked")
        raise LocalizerError(
            "Preflight stopped this job. "
            + " ".join(preflight.blockers)
            + f" See {project.logs / 'preflight.json'}."
        )
    config = preflight.config
    save_project_config(project, config)
    if not state.data.warnings:
        previous_report_path = project.logs / "report.json"
        if previous_report_path.is_file():
            try:
                previous_report = load_json(previous_report_path)
                previous_warnings = (
                    previous_report.get("warnings", [])
                    if isinstance(previous_report, dict)
                    else []
                )
                if isinstance(previous_warnings, list):
                    state.data.warnings = [str(warning) for warning in previous_warnings]
                    state.save()
            except (OSError, TypeError, ValueError):
                LOGGER.warning("Could not migrate warnings from the previous processing report.")
    warnings = list(dict.fromkeys(state.data.warnings))

    def remember_warnings(additional: list[str] | tuple[str, ...]) -> None:
        changed = False
        for warning in additional:
            if warning not in warnings:
                warnings.append(warning)
                changed = True
        if changed:
            state.data.warnings = warnings.copy()
            state.save()

    outputs: list[Path] = []
    cue_count = 0
    flagged_cues: list[int] = []
    subtitle_source = metadata.subtitle_kind
    started = monotonic()

    try:
        existing_video: Path | None
        try:
            existing_video = find_source_video(project)
        except LocalizerError:
            existing_video = None
        source_hash = _input_hash(metadata, existing_video)
        acquire_config_hash = stable_hash(config.download)
        acquire_outputs = [project.metadata] + ([existing_video] if existing_video else [])
        if not state.can_skip(
            "acquire",
            input_hash=source_hash,
            config_hash=acquire_config_hash,
            output_files=acquire_outputs,
            force="acquire" in force_steps,
        ):
            with state.step(
                "acquire", input_hash=source_hash, config_hash=acquire_config_hash
            ) as step_outputs:
                free = available_bytes(project.root)
                if metadata.source_type == "local":
                    original = Path(metadata.source_input)
                    if free < original.stat().st_size * 1.1:
                        raise LocalizerError(
                            "Insufficient free disk space to safely copy the local source video."
                        )
                    source_video = import_local(original, project.source)
                else:
                    if raw_info is None:
                        if metadata.source_type == "youtube":
                            refreshed, raw_info = inspect_youtube(metadata.source_input)
                        elif metadata.source_type == "direct_media":
                            refreshed, raw_info = inspect_direct_media(metadata.source_input)
                        elif metadata.source_type == "webpage_media":
                            refreshed, raw_info = inspect_webpage_media(metadata.source_input)
                        else:  # pragma: no cover - protects saved project metadata from corruption
                            raise LocalizerError(
                                f"Unsupported remote source type: {metadata.source_type}"
                            )
                        metadata = refreshed
                    if metadata.source_type == "youtube":
                        download = download_youtube(
                            metadata.source_url or metadata.source_input,
                            raw_info,
                            project.source,
                            config.download,
                        )
                    else:
                        download = download_direct_media(
                            metadata.source_url or metadata.source_input,
                            raw_info,
                            project.source,
                            config.download,
                        )
                    remember_warnings(download.warnings)
                    source_video = download.video
                    try:
                        probed = metadata_from_probe(
                            source_video,
                            probe_media(source_video),
                            video_id=metadata.video_id,
                        )
                        metadata = metadata.model_copy(
                            update={
                                "width": probed.width,
                                "height": probed.height,
                                "frame_rate": probed.frame_rate,
                                "video_codec": probed.video_codec,
                                "audio_codec": probed.audio_codec,
                                "pixel_format": probed.pixel_format,
                                "color_space": probed.color_space,
                                "color_transfer": probed.color_transfer,
                                "color_primaries": probed.color_primaries,
                                "variable_frame_rate": probed.variable_frame_rate,
                                "audio_streams": probed.audio_streams,
                            }
                        )
                    except LocalizerError as exc:
                        LOGGER.warning("Could not probe downloaded media characteristics: %s", exc)
                    metadata.english_subtitle_language = ""
                    metadata.english_subtitle_kind = ""
                    metadata.chinese_subtitle_language = ""
                    metadata.chinese_subtitle_kind = ""
                    metadata.subtitle_language = ""
                    metadata.subtitle_kind = (
                        ""
                        if config.subtitle_mode == "download_only"
                        else "local faster-whisper transcription"
                    )
                    subtitle_source = metadata.subtitle_kind
                    if config.download.download_metadata:
                        raw_path = project.source / "metadata.raw.json"
                        raw_to_save = raw_info
                        if metadata.source_type == "webpage_media":
                            raw_to_save = {
                                **cached_raw_metadata(metadata),
                                "source_page": metadata.source_input,
                                "media_declaration": raw_info.get(
                                    "_localizer_media_declaration", ""
                                ),
                            }
                        save_raw_metadata(raw_to_save, raw_path)
                        step_outputs.append(raw_path)
                    if config.download.download_thumbnail and metadata.thumbnail_url:
                        thumbnail = project.source / "thumbnail.jpg"
                        save_thumbnail(metadata.thumbnail_url, thumbnail)
                        if thumbnail.is_file():
                            step_outputs.append(thumbnail)
                atomic_write_json(project.metadata, _stored_metadata(metadata))
                step_outputs.extend([source_video, project.metadata])
        source_video = find_source_video(project)

        rights_outputs = generate_rights_assets(metadata, project.publishing, config.rights)
        outputs.extend(rights_outputs)
        rights_issues = validate_rights(config.rights, strict=True)
        if rights_issues:
            remember_warnings(
                [
                    "Rights record requires human review before publishing: "
                    + " ".join(rights_issues)
                ]
            )

        processing_video = source_video
        if config.enhancement.mode != "off":
            if not metadata.width or not metadata.height or not metadata.frame_rate:
                raise LocalizerError(
                    "AI super resolution needs source width, height, and frame-rate metadata."
                )
            target_height = super_resolution_target_height(
                metadata.height,
                config.render,
                config.enhancement,
            )
            if target_height <= metadata.height:
                remember_warnings(
                    [
                        "AI super resolution was selected, but the requested output is not "
                        "larger than the source; the original pixels were kept."
                    ]
                )
            else:
                enhance_input_hash = hash_file(source_video)
                enhance_config_hash = enhancement_config_hash(config, target_height)
                if not state.can_skip(
                    "enhance",
                    input_hash=enhance_input_hash,
                    config_hash=enhance_config_hash,
                    output_files=[project.enhanced_source],
                    force="enhance" in force_steps,
                ):
                    with state.step(
                        "enhance",
                        input_hash=enhance_input_hash,
                        config_hash=enhance_config_hash,
                    ) as step_outputs:
                        step_outputs.append(
                            enhance_video(
                                source_video,
                                project.enhanced_source,
                                source_width=metadata.width,
                                source_height=metadata.height,
                                frame_rate=metadata.frame_rate,
                                duration=metadata.duration,
                                source_audio_codec=metadata.audio_codec,
                                render=config.render,
                                enhancement=config.enhancement,
                                working_directory=project.temp / "super_resolution",
                                force="enhance" in force_steps,
                            )
                        )
                        validate_rendered_video(
                            project.enhanced_source,
                            expected_duration=metadata.duration,
                        )
                processing_video = project.enhanced_source
                outputs.append(processing_video)

        if config.subtitle_mode == "download_only":
            outputs = [processing_video, project.metadata, *rights_outputs]
            for optional_output in (
                project.source / "metadata.raw.json",
                project.source / "thumbnail.jpg",
            ):
                if optional_output.is_file():
                    outputs.append(optional_output)
            state.mark_status("downloaded")
            report = build_report(
                metadata,
                state.data,
                subtitle_source="not requested (download only)",
                translation_provider="not requested (download only)",
                output_paths=outputs,
                warnings=warnings,
            )
            report["total_elapsed_seconds"] = round(monotonic() - started, 3)
            write_report(project.logs, report)
            return PipelineResult(project, "downloaded", outputs, warnings)

        source_code, target_code = _language_pair(config)
        chinese_to_english = source_code == "zh"
        provided_chinese_is_target = False
        needs_english = not chinese_to_english
        english_changed = False
        if needs_english:
            english_hash_source = hash_file(source_video)
            subtitle_config_hash = stable_hash(
                {
                    "preserve_sound_descriptions": config.subtitles.preserve_sound_descriptions,
                    "transcription": config.transcription,
                }
            )
            english_changed = not state.can_skip(
                "english_subtitles",
                input_hash=english_hash_source,
                config_hash=subtitle_config_hash,
                output_files=[project.english_srt],
                force="english_subtitles" in force_steps or "transcribe" in force_steps,
            )
            if english_changed:
                with state.step(
                    "english_subtitles",
                    input_hash=english_hash_source,
                    config_hash=subtitle_config_hash,
                ) as step_outputs:
                    audio = project.audio / "transcription_audio.wav"
                    extract_transcription_audio(source_video, audio)
                    raw_transcription = project.subtitles / "transcription.raw.json"
                    cleanup = transcribe_audio(
                        audio,
                        raw_transcription,
                        project.english_srt,
                        config.transcription,
                    )
                    remember_warnings(cleanup.warnings)
                    flagged_cues.extend(cleanup.flagged_cue_ids)
                    subtitle_source = "faster-whisper (local English transcription)"
                    step_outputs.extend([audio, raw_transcription, project.english_srt])

        chinese_changed = False
        if chinese_to_english:
            chinese_hash_source = hash_file(source_video)
            chinese_config_hash = stable_hash(
                {"transcription": config.transcription, "language": "zh"}
            )
            chinese_changed = not state.can_skip(
                "chinese_subtitles",
                input_hash=chinese_hash_source,
                config_hash=chinese_config_hash,
                output_files=[project.chinese_srt],
                force="chinese_subtitles" in force_steps or "transcribe" in force_steps,
            )
            if chinese_changed:
                with state.step(
                    "chinese_subtitles",
                    input_hash=chinese_hash_source,
                    config_hash=chinese_config_hash,
                ) as step_outputs:
                    audio = project.audio / "transcription_audio.wav"
                    extract_transcription_audio(source_video, audio)
                    raw_transcription = project.subtitles / "transcription.raw.json"
                    cleanup = transcribe_audio(
                        audio,
                        raw_transcription,
                        project.chinese_srt,
                        config.transcription,
                        language="zh",
                    )
                    remember_warnings(cleanup.warnings)
                    flagged_cues.extend(cleanup.flagged_cue_ids)
                    subtitle_source = "faster-whisper (Chinese)"
                    step_outputs.extend([audio, raw_transcription, project.chinese_srt])

        if english_changed or chinese_changed:
            stale_files = [
                project.chinese_ass,
                project.english_ass,
                project.bilingual_srt,
                project.bilingual_ass,
                project.temp / "manual_translations.json",
                project.chinese_hardsub,
                project.english_hardsub,
                _target_ass(project, config),
                rendered_output(project, config),
                softsub_output(project, config),
            ]
            if english_changed:
                stale_files.append(project.chinese_srt)
            if chinese_changed:
                stale_files.append(project.english_srt)
            for stale in stale_files:
                stale.unlink(missing_ok=True)

        english = parse_subtitle(project.english_srt) if needs_english else []
        chinese = parse_subtitle(project.chinese_srt) if chinese_to_english else []
        cue_count = len(chinese) if chinese else len(english)

        source_subtitle = _source_subtitle(project, config)
        target_subtitle = _target_subtitle(project, config)
        translation_hash = hash_file(
            target_subtitle if provided_chinese_is_target else source_subtitle
        )
        translation_config_hash = stable_hash(
            {
                "translation": config.translation,
                "subtitle_mode": config.subtitle_mode,
                "subtitles": config.subtitles,
            }
        )
        translation_current = state.can_skip(
            "translate",
            input_hash=translation_hash,
            config_hash=translation_config_hash,
            output_files=[target_subtitle],
            force="translate" in force_steps,
        )
        needs_translation = not provided_chinese_is_target and not target_subtitle.is_file()
        if not provided_chinese_is_target and config.translation.provider != "manual":
            needs_translation = not translation_current
        elif "translate" in force_steps:
            needs_translation = True

        if needs_translation:
            if config.translation.provider == "manual":
                if "translate" in force_steps:
                    for stale in (
                        target_subtitle,
                        _target_ass(project, config),
                        project.bilingual_srt,
                        project.bilingual_ass,
                        project.temp / "manual_translations.json",
                        rendered_output(project, config),
                    ):
                        stale.unlink(missing_ok=True)
                manifest = project.translation_chunks / "manifest.json"
                if not state.can_skip(
                    "translation_export",
                    input_hash=translation_hash,
                    config_hash=translation_config_hash,
                    output_files=[manifest],
                    force="translate" in force_steps,
                ):
                    with state.step(
                        "translation_export",
                        input_hash=translation_hash,
                        config_hash=translation_config_hash,
                    ) as step_outputs:
                        step_outputs.extend(export_manual_translation(project, config, metadata))
                        step_outputs.append(manifest)
                state.mark_status("awaiting_manual_translation")
                outputs.extend([source_subtitle, manifest])
                report = build_report(
                    metadata,
                    state.data,
                    subtitle_source=subtitle_source,
                    whisper_model=(
                        config.transcription.model
                        if (subtitle_source or "").startswith("faster-whisper")
                        else ""
                    ),
                    translation_provider="manual",
                    cue_count=cue_count,
                    flagged_cues=flagged_cues,
                    output_paths=outputs,
                    warnings=warnings
                    + [
                        "Translation chunks are ready. Translate and import every chunk, then run "
                        "render or process --resume."
                    ],
                )
                write_report(project.logs, report)
                return PipelineResult(
                    project,
                    "awaiting_manual_translation",
                    outputs,
                    report["warnings"],
                )
            with state.step(
                "translate",
                input_hash=translation_hash,
                config_hash=translation_config_hash,
            ) as step_outputs:
                if config.translation.provider == "offline":
                    translated_outputs, translation_warnings = translate_with_offline(
                        project, config, metadata
                    )
                elif config.translation.provider == "ollama":
                    translated_outputs, translation_warnings = translate_with_local_ai(
                        project, config, metadata
                    )
                else:
                    translated_outputs, translation_warnings = translate_with_api(
                        project, config, metadata
                    )
                step_outputs.extend(translated_outputs)
                remember_warnings(translation_warnings)

        if target_code in {"en", "zh"}:
            localized_outputs, localized_warnings = _write_localized_subtitles(
                project,
                parse_subtitle(project.english_srt) if project.english_srt.is_file() else [],
                parse_subtitle(project.chinese_srt),
                config,
                metadata,
            )
        else:
            localized_outputs, localized_warnings = _write_localized_subtitles(
                project,
                [],
                [],
                config,
                metadata,
                target_cues=parse_subtitle(_target_subtitle(project, config)),
            )
        outputs.extend(localized_outputs)
        remember_warnings(localized_warnings)

        if config.publishing.generate_metadata:
            metadata_outputs = generate_publishing_assets(
                metadata, project.publishing, config.publishing, config.rights
            )
            outputs.extend(metadata_outputs)

        target_cues = parse_subtitle(_target_subtitle(project, config))
        quality = audit_subtitles(
            target_cues,
            language=target_code,
            max_lines=config.subtitles.max_lines,
            preferred_line_length=config.subtitles.max_chinese_chars_per_line,
        )
        flagged_cues.extend(quality["flagged_cue_ids"])
        quality_path = project.logs / "subtitle_quality.json"
        atomic_write_json(quality_path, quality)
        outputs.append(quality_path)
        if quality["flagged_cue_count"]:
            review_path = project.subtitles / "review_required.srt"
            write_srt(review_path, select_review_cues(target_cues, quality))
            outputs.append(review_path)
            remember_warnings(
                [
                    "Subtitle quality check flagged "
                    f"{quality['flagged_cue_count']} cue(s); review subtitles/review_required.srt "
                    "and logs/subtitle_quality.json."
                ]
            )

        render_hash = stable_hash(
            {
                "video": hash_file(render_source(project, config)),
                "subtitles": hash_file(
                    _target_ass(project, config)
                    if config.subtitle_mode == "chinese"
                    else project.bilingual_ass
                ),
            }
        )
        render_config_hash = stable_hash(config.render)
        rendered = rendered_output(project, config)
        remember_warnings(rendering_media_warnings(metadata))
        render_outputs = [rendered]
        if not state.can_skip(
            "render",
            input_hash=render_hash,
            config_hash=render_config_hash,
            output_files=render_outputs,
            force="render" in force_steps,
        ):
            with state.step(
                "render", input_hash=render_hash, config_hash=render_config_hash
            ) as step_outputs:
                step_outputs.append(render_project(project, config))
                if config.render.soft_subtitles:
                    try:
                        step_outputs.append(render_softsub_project(project, config))
                    except LocalizerError as exc:
                        remember_warnings(
                            [
                                "Selectable subtitle MP4 was not created; the hard-subtitle "
                                f"video is still ready. Details: {exc}"
                            ]
                        )
        softsub = softsub_output(project, config)
        if softsub.is_file():
            render_outputs.append(softsub)
        outputs.extend(render_outputs)
        state.mark_status("completed")
        report = build_report(
            metadata,
            state.data,
            subtitle_source=subtitle_source,
            whisper_model=(
                config.transcription.model
                if (subtitle_source or "").startswith("faster-whisper")
                else ""
            ),
            translation_provider=(
                "youtube-provided"
                if provided_chinese_is_target
                else config.translation.provider
            ),
            cue_count=cue_count,
            flagged_cues=sorted(set(flagged_cues)),
            render_parameters=config.render.model_dump(mode="json"),
            subtitle_quality=quality,
            output_paths=outputs,
            warnings=warnings,
        )
        report["total_elapsed_seconds"] = round(monotonic() - started, 3)
        write_report(project.logs, report)
        return PipelineResult(project, "completed", outputs, warnings)
    except BaseException as exc:
        state.mark_status("failed")
        report = build_report(
            metadata,
            state.data,
            subtitle_source=subtitle_source,
            whisper_model=(
                config.transcription.model
                if (subtitle_source or "").startswith("faster-whisper")
                else ""
            ),
            translation_provider=config.translation.provider,
            cue_count=cue_count,
            flagged_cues=flagged_cues,
            output_paths=outputs,
            warnings=warnings,
            errors=[str(exc)],
        )
        report["total_elapsed_seconds"] = round(monotonic() - started, 3)
        write_report(project.logs, report)
        LOGGER.exception("Pipeline failed")
        raise
