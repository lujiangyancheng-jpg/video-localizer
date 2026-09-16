from __future__ import annotations

import shutil
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import TRANSLATION_DIRECTIONS, AppConfig, load_config
from .doctor import run_doctor
from .enhancement.super_resolution import render_enhancement_comparison
from .errors import LocalizerError
from .logging_config import configure_logging
from .models import ProjectPaths
from .pipeline import (
    FORCE_STEPS,
    _inspect_input,
    _language_pair,
    _source_subtitle,
    _target_ass,
    _target_subtitle,
    _write_localized_subtitles,
    export_manual_translation,
    find_source_video,
    load_project_config,
    load_project_metadata,
    process_pipeline,
    render_project,
    render_softsub_project,
    render_source,
    rendered_output,
    save_project_config,
    translate_with_api,
    translate_with_local_ai,
    translate_with_offline,
)
from .preflight import build_job_preflight
from .profiles import (
    OutputQuality,
    ProcessingProfile,
    apply_output_quality,
    apply_processing_profile,
)
from .publishing.metadata_generator import generate_publishing_assets
from .rendering.media_warnings import rendering_media_warnings
from .rendering.preview import render_preview
from .rendering.validation import validate_rendered_video
from .reporting import build_report, load_report_context, write_report
from .state import PipelineState
from .subtitles.normalize import validate_cues
from .subtitles.parser import parse_subtitle
from .subtitles.quality import audit_subtitles
from .support import create_support_bundle
from .transcription.audio import extract_transcription_audio
from .transcription.whisper_engine import transcribe_audio
from .translation.glossary import load_glossary
from .translation.manual import import_translation_file
from .utils.files import atomic_write_json, ensure_within
from .utils.hashing import hash_file, stable_hash

app = typer.Typer(
    name="youtube-localizer",
    no_args_is_help=True,
    add_completion=False,
    help=(
        "Create bilingual localization assets from public, authorized YouTube videos, public "
        "HTML5 media pages, direct "
        "media URLs, or local video files. Do not use it for content you lack permission to "
        "download, translate, or redistribute."
    ),
)
console = Console()
error_console = Console(stderr=True)


@dataclass(frozen=True)
class BatchOutcome:
    input_value: str
    status: str
    project_path: str = ""
    error: str = ""


def _run_batch_item(input_value: str, config: AppConfig, resume: bool) -> BatchOutcome:
    """Run one batch project in a separate process so downloads can overlap safely."""
    try:
        result = process_pipeline(input_value, config, resume=resume)
    except LocalizerError as exc:
        return BatchOutcome(input_value=input_value, status="failed", error=str(exc))
    return BatchOutcome(
        input_value=input_value,
        status=result.status,
        project_path=str(result.project.root),
    )


ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="YAML configuration file.", exists=True, dir_okay=False),
]


def _project(path: Path) -> ProjectPaths:
    root = path.expanduser().resolve()
    if not root.is_dir() or not (root / "pipeline_state.json").is_file():
        raise LocalizerError(f"Not a localization project directory: {root}")
    return ProjectPaths(root)


def _configured(
    config_path: Path | None,
    *,
    output_dir: Path | None = None,
    subtitle_mode: str | None = None,
    translation_provider: str | None = None,
    translation_direction: str | None = None,
    subtitle_font: str | None = None,
    subtitle_font_size: int | None = None,
    subtitle_position_x: int | None = None,
    subtitle_position_y: int | None = None,
    processing_profile: ProcessingProfile | None = None,
    output_quality: OutputQuality | None = None,
    output_height: int | None = None,
    output_fps: int | None = None,
    super_resolution: str | None = None,
    rights_basis: str | None = None,
    rights_holder: str | None = None,
    license_url: str | None = None,
    permission_reference: str | None = None,
    attribution_text: str | None = None,
    commercial_use: bool | None = None,
) -> AppConfig:
    config = load_config(config_path)
    changes = {}
    if output_dir:
        changes["output_directory"] = output_dir
    if subtitle_mode:
        if subtitle_mode not in {
            "download_only",
            "chinese",
            "bilingual_en_zh",
            "bilingual_zh_en",
        }:
            raise LocalizerError(
                "--subtitle-mode must be download_only, chinese, bilingual_en_zh, "
                "or bilingual_zh_en."
            )
        changes["subtitle_mode"] = subtitle_mode
    subtitle_changes: dict[str, int | str] = {}
    if subtitle_font is not None:
        normalized_font = subtitle_font.strip()
        if not normalized_font or "\n" in normalized_font or "\r" in normalized_font:
            raise LocalizerError("--subtitle-font must be a non-empty font family name.")
        subtitle_changes["font"] = normalized_font
    if subtitle_font_size is not None:
        if not 12 <= subtitle_font_size <= 120:
            raise LocalizerError("--subtitle-font-size must be between 12 and 120.")
        subtitle_changes["font_size"] = subtitle_font_size
        subtitle_changes["english_font_size"] = max(
            10, min(100, round(subtitle_font_size * 34 / 48))
        )
    for value, option, key in (
        (subtitle_position_x, "--subtitle-position-x", "position_x_percent"),
        (subtitle_position_y, "--subtitle-position-y", "position_y_percent"),
    ):
        if value is None:
            continue
        if not 2 <= value <= 98:
            raise LocalizerError(f"{option} must be between 2 and 98.")
        subtitle_changes[key] = value
    if subtitle_changes:
        changes["subtitles"] = config.subtitles.model_copy(update=subtitle_changes)
    render_changes: dict[str, int] = {}
    if output_height is not None:
        if not 144 <= output_height <= 4320:
            raise LocalizerError("--output-height must be between 144 and 4320 pixels.")
        render_changes["output_height"] = output_height
    if output_fps is not None:
        if not 1 <= output_fps <= 240:
            raise LocalizerError("--output-fps must be between 1 and 240.")
        render_changes["output_fps"] = output_fps
    if render_changes:
        changes["render"] = config.render.model_copy(update=render_changes)
    if super_resolution is not None:
        if super_resolution not in {"off", "general", "animation"}:
            raise LocalizerError("--super-resolution must be off, general, or animation.")
        changes["enhancement"] = config.enhancement.model_copy(update={"mode": super_resolution})
    rights_changes: dict[str, str | bool] = {}
    if rights_basis is not None:
        valid_rights = {
            "unspecified",
            "owned",
            "written_permission",
            "cc_by",
            "cc_by_sa",
            "cc_by_nc",
            "public_domain",
            "other",
        }
        if rights_basis not in valid_rights:
            raise LocalizerError(
                "--rights-basis must be owned, written_permission, cc_by, cc_by_sa, "
                "cc_by_nc, public_domain, other, or unspecified."
            )
        rights_changes["basis"] = rights_basis
    for value, key in (
        (rights_holder, "rights_holder"),
        (license_url, "license_url"),
        (permission_reference, "permission_reference"),
        (attribution_text, "attribution_text"),
    ):
        if value is not None:
            rights_changes[key] = value.strip()
    if commercial_use is not None:
        rights_changes["commercial_use"] = commercial_use
    if rights_changes:
        changes["rights"] = config.rights.model_copy(update=rights_changes)
    translation_changes: dict[str, str] = {}
    if translation_provider:
        if translation_provider not in {"manual", "offline", "ollama", "openai-compatible"}:
            raise LocalizerError(
                "--translation-provider must be manual, offline, ollama, or openai-compatible."
            )
        translation_changes["provider"] = translation_provider
    if translation_direction:
        if translation_direction not in TRANSLATION_DIRECTIONS:
            raise LocalizerError(
                "--translation-direction must be one of: " + ", ".join(TRANSLATION_DIRECTIONS) + "."
            )
        translation_changes["direction"] = translation_direction
    if translation_changes:
        changes["translation"] = config.translation.model_copy(update=translation_changes)
    resolved = config.model_copy(update=changes)
    if processing_profile:
        resolved = apply_processing_profile(resolved, processing_profile)
    resolved = apply_output_quality(resolved, output_quality) if output_quality else resolved
    # model_copy intentionally skips Pydantic validation; validate CLI overrides before a job
    # starts so unsupported language/provider combinations fail immediately.
    return AppConfig.model_validate(resolved.model_dump())


@app.command("preflight")
def preflight_command(
    input_value: Annotated[
        str,
        typer.Argument(
            help="Public YouTube/HTML5 media-page URL, direct media URL, or local video path."
        ),
    ],
    config_path: ConfigOption = None,
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", "-o", help="Override output directory.")
    ] = None,
    processing_profile: Annotated[
        ProcessingProfile | None,
        typer.Option("--processing-profile", help="auto, fast, balanced, quality, or safe_cpu."),
    ] = None,
) -> None:
    """Preview the offline model, hardware fallback, and disk requirements without starting."""
    config = _configured(
        config_path,
        output_dir=output_dir,
        processing_profile=processing_profile,
    )
    metadata, _ = _inspect_input(input_value)
    plan = build_job_preflight(metadata, config)
    table = Table("Item", "Plan")
    table.add_row("Package", plan.package)
    table.add_row("Workspace estimate", f"{plan.estimated_working_bytes / 1024**3:.1f} GiB")
    table.add_row(
        "Available space",
        f"{plan.available_bytes / 1024**3:.1f} GiB"
        if plan.available_bytes is not None
        else "unavailable",
    )
    table.add_row("Transcription", plan.transcription_plan)
    table.add_row("Encoding", plan.encoding_plan)
    console.print(table)
    for warning in plan.warnings:
        console.print(f"[yellow]Warning:[/] {warning}")
    for blocker in plan.blockers:
        error_console.print(f"[red]Blocked:[/] {blocker}")
    if plan.blockers:
        raise typer.Exit(1)


@app.command("process")
def process_command(
    input_value: Annotated[
        str,
        typer.Argument(
            help="Public YouTube/HTML5 media-page URL, direct media URL, or local video path."
        ),
    ],
    config_path: ConfigOption = None,
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", "-o", help="Override output directory.")
    ] = None,
    subtitle_mode: Annotated[
        str | None,
        typer.Option(
            "--subtitle-mode",
            help="download_only, chinese, bilingual_en_zh, or bilingual_zh_en.",
        ),
    ] = None,
    translation_provider: Annotated[
        str | None,
        typer.Option(
            "--translation-provider",
            help="manual, offline, ollama, or openai-compatible.",
        ),
    ] = None,
    translation_direction: Annotated[
        str | None,
        typer.Option(
            "--translation-direction",
            help="en-to-zh, zh-to-en, or English/Chinese to ja, ko, es, fr, de, pt, ru, ar.",
        ),
    ] = None,
    subtitle_font: Annotated[
        str | None,
        typer.Option(
            "--subtitle-font",
            help=("ASS font family. The packaged application includes Noto Sans CJK SC."),
        ),
    ] = None,
    subtitle_font_size: Annotated[
        int | None,
        typer.Option("--subtitle-font-size", help="Chinese subtitle font size, from 12 to 120."),
    ] = None,
    subtitle_position_x: Annotated[
        int | None,
        typer.Option(
            "--subtitle-position-x", help="Subtitle horizontal position as a 2-98 percent."
        ),
    ] = None,
    subtitle_position_y: Annotated[
        int | None,
        typer.Option("--subtitle-position-y", help="Subtitle vertical position as a 2-98 percent."),
    ] = None,
    processing_profile: Annotated[
        ProcessingProfile | None,
        typer.Option(
            "--processing-profile",
            help="auto, fast, balanced, quality, or safe_cpu. Preserves translation and subtitle choices.",
        ),
    ] = None,
    output_quality: Annotated[
        OutputQuality | None,
        typer.Option("--output-quality", help="best, high, or standard final video quality."),
    ] = None,
    output_height: Annotated[
        int | None,
        typer.Option("--output-height", help="Cap rendered-video height without upscaling."),
    ] = None,
    output_fps: Annotated[
        int | None,
        typer.Option("--output-fps", help="Cap rendered-video FPS without adding frames."),
    ] = None,
    super_resolution: Annotated[
        str | None,
        typer.Option(
            "--super-resolution",
            help="off, general, or animation. Requires the optional AI super-resolution pack.",
        ),
    ] = None,
    rights_basis: Annotated[
        str | None,
        typer.Option("--rights-basis", help="Rights basis recorded with the project."),
    ] = None,
    rights_holder: Annotated[
        str | None, typer.Option("--rights-holder", help="Creator or rights holder name.")
    ] = None,
    license_url: Annotated[
        str | None, typer.Option("--license-url", help="Public license or evidence URL.")
    ] = None,
    permission_reference: Annotated[
        str | None,
        typer.Option("--permission-reference", help="Non-secret permission reference or note."),
    ] = None,
    attribution_text: Annotated[
        str | None, typer.Option("--attribution-text", help="Custom publication attribution.")
    ] = None,
    commercial_use: Annotated[
        bool | None,
        typer.Option("--commercial-use/--noncommercial-use"),
    ] = None,
    resume: Annotated[bool, typer.Option("--resume", help="Resume a matching project.")] = False,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Replace an existing matching project.")
    ] = False,
    force_step: Annotated[
        list[str] | None,
        typer.Option(
            "--force-step",
            help=(
                "Repeat one stage: acquire, english_subtitles, chinese_subtitles, "
                "transcribe, translate, enhance, or render."
            ),
        ),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Run acquisition, source subtitles/transcription, translation, and rendering."""
    config = _configured(
        config_path,
        output_dir=output_dir,
        subtitle_mode=subtitle_mode,
        translation_provider=translation_provider,
        translation_direction=translation_direction,
        subtitle_font=subtitle_font,
        subtitle_font_size=subtitle_font_size,
        subtitle_position_x=subtitle_position_x,
        subtitle_position_y=subtitle_position_y,
        processing_profile=processing_profile,
        output_quality=output_quality,
        output_height=output_height,
        output_fps=output_fps,
        super_resolution=super_resolution,
        rights_basis=rights_basis,
        rights_holder=rights_holder,
        license_url=license_url,
        permission_reference=permission_reference,
        attribution_text=attribution_text,
        commercial_use=commercial_use,
    )
    console.print(
        "[yellow]Legal notice:[/] process only content you own, public-domain/CC content, or "
        "content you have explicit permission to translate and redistribute."
    )
    requested_force_steps = set(force_step or [])
    unknown_force_steps = requested_force_steps - FORCE_STEPS
    if unknown_force_steps:
        raise LocalizerError(
            "Unknown --force-step value(s): "
            + ", ".join(sorted(unknown_force_steps))
            + ". Expected one of: "
            + ", ".join(sorted(FORCE_STEPS))
            + "."
        )
    result = process_pipeline(
        input_value,
        config,
        resume=resume,
        overwrite=overwrite,
        force_steps=requested_force_steps,
        verbose=verbose,
    )
    console.print(f"[bold green]Project:[/] {result.project.root}")
    if result.status == "downloaded":
        console.print(f"[bold green]Downloaded:[/] {render_source(result.project, config)}")
    elif result.status == "awaiting_manual_translation":
        source_code, target_code = _language_pair(config)
        console.print(
            f"[bold yellow]{source_code.upper()} source subtitles are ready.[/] Translate every "
            f"Markdown chunk to {target_code.upper()} in {result.project.translation_chunks}, "
            "then run `translate-import` for each response."
        )
        console.print(
            "ChatGPT Plus does not include OpenAI API credits; this manual workflow uses no API key."
        )
    else:
        console.print(f"[bold green]Completed:[/] {rendered_output(result.project, config)}")


@app.command("batch")
def batch_command(
    inputs_file: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, help="UTF-8 file with one input per line."),
    ],
    config_path: ConfigOption = None,
    processing_profile: Annotated[
        ProcessingProfile | None,
        typer.Option("--processing-profile", help="auto, fast, balanced, quality, or safe_cpu."),
    ] = None,
    resume: Annotated[bool, typer.Option("--resume")] = False,
    parallel_jobs: Annotated[
        int,
        typer.Option(
            "--parallel-jobs",
            min=1,
            max=2,
            help="Run up to two projects concurrently; GPU/AI/render stages are automatically queued.",
        ),
    ] = 2,
) -> None:
    """Process multiple URLs/local paths with safe shared-resource scheduling."""
    config = load_config(config_path)
    if processing_profile:
        config = apply_processing_profile(config, processing_profile)
    lines = [
        line.strip()
        for line in inputs_file.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        raise LocalizerError("The batch input file contains no usable inputs.")
    if parallel_jobs == 1:
        outcomes = [_run_batch_item(value, config, resume) for value in lines]
    else:
        outcomes_by_index: dict[int, BatchOutcome] = {}
        with ProcessPoolExecutor(max_workers=parallel_jobs) as executor:
            futures = {
                executor.submit(_run_batch_item, value, config, resume): index
                for index, value in enumerate(lines, start=1)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    outcomes_by_index[index] = future.result()
                except Exception as exc:  # pragma: no cover - worker crashes are platform-specific
                    outcomes_by_index[index] = BatchOutcome(
                        input_value=lines[index - 1], status="failed", error=str(exc)
                    )
        outcomes = [outcomes_by_index[index] for index in range(1, len(lines) + 1)]

    failures = 0
    for index, outcome in enumerate(outcomes, start=1):
        console.rule(f"[bold]Input {index}/{len(lines)}")
        if outcome.error:
            failures += 1
            error_console.print(f"[red]Failed:[/] {outcome.input_value}\n{outcome.error}")
        else:
            console.print(f"{outcome.status}: {outcome.project_path}")
    if failures:
        raise LocalizerError(f"{failures} of {len(lines)} batch inputs failed.")


@app.command("inspect")
def inspect_command(
    project_path: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
) -> None:
    """Show project metadata and resumable stage state."""
    project = _project(project_path)
    metadata = load_project_metadata(project)
    state = PipelineState(project.state_file)
    console.print(f"[bold]{metadata.title}[/]")
    console.print(f"Project: {project.root}")
    console.print(f"Source: {metadata.source_type} — {metadata.source_input}")
    console.print(f"Duration: {metadata.duration:.2f}s")
    console.print(f"Status: {state.data.project_status}")
    table = Table("Step", "Status", "Elapsed", "Retries", "Error")
    for name, record in state.data.steps.items():
        table.add_row(
            name,
            record.status,
            f"{record.elapsed_seconds:.3f}s" if record.elapsed_seconds is not None else "",
            str(record.retry_count),
            record.error_message or "",
        )
    console.print(table)


@app.command("transcribe")
def transcribe_command(
    project_path: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    config_path: ConfigOption = None,
) -> None:
    """Force faster-whisper transcription for an existing project."""
    project = _project(project_path)
    config = load_config(config_path) if config_path else load_project_config(project)
    source = find_source_video(project)
    configure_logging(project.logs / "pipeline.log")
    state = PipelineState(project.state_file)
    source_code, _ = _language_pair(config)
    source_subtitle = _source_subtitle(project, config)
    audio = project.audio / "transcription_audio.wav"
    raw_transcription = project.subtitles / "transcription.raw.json"
    subtitle_step = "chinese_subtitles" if source_code == "zh" else "english_subtitles"
    with state.step(
        subtitle_step,
        input_hash=hash_file(source),
        config_hash=stable_hash({"transcription": config.transcription, "language": source_code}),
    ) as outputs:
        extract_transcription_audio(source, audio)
        cleanup = transcribe_audio(
            audio,
            raw_transcription,
            source_subtitle,
            config.transcription,
            language=source_code,
        )
        outputs.extend([audio, raw_transcription, source_subtitle])
    for stale in (
        _target_subtitle(project, config),
        _target_ass(project, config),
        project.bilingual_srt,
        project.bilingual_ass,
        project.temp / "manual_translations.json",
        rendered_output(project, config),
    ):
        stale.unlink(missing_ok=True)
    export_manual_translation(project, config)
    state.mark_status("awaiting_manual_translation")
    console.print(
        f"[green]Transcribed {len(cleanup.cues)} cues:[/] {source_subtitle}\n"
        f"Manual translation chunks: {project.translation_chunks}"
    )


@app.command("translate-export")
def translate_export_command(
    project_path: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    config_path: ConfigOption = None,
) -> None:
    """Export numbered Markdown/JSONL chunks for manual ChatGPT translation."""
    project = _project(project_path)
    config = load_config(config_path) if config_path else load_project_config(project)
    state = PipelineState(project.state_file)
    with state.step(
        "translation_export",
        input_hash=hash_file(_source_subtitle(project, config)),
        config_hash=stable_hash(config.translation),
    ) as outputs:
        exported = export_manual_translation(project, config)
        outputs.extend(exported)
        outputs.append(project.translation_chunks / "manifest.json")
    state.mark_status("awaiting_manual_translation")
    console.print(
        f"[green]Exported {len(exported)} chunks to:[/] {project.translation_chunks}\n"
        "Upload each chunk to ChatGPT and save the JSONL response. ChatGPT Plus does not "
        "include OpenAI API credits; this workflow needs no API key."
    )


@app.command("translate-import")
def translate_import_command(
    project_path: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    translated_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    config_path: ConfigOption = None,
) -> None:
    """Validate and import a translated JSONL chunk without changing timestamps."""
    project = _project(project_path)
    config = load_config(config_path) if config_path else load_project_config(project)
    source_code, target_code = _language_pair(config)
    state = PipelineState(project.state_file)
    with state.step(
        "translation_import",
        input_hash=hash_file(translated_file),
        config_hash=stable_hash(config.translation),
    ) as outputs:
        imported, total, warnings = import_translation_file(
            project,
            translated_file,
            width=config.subtitles.max_chinese_chars_per_line,
            max_lines=config.subtitles.max_lines,
            subtitle_mode=config.subtitle_mode,
            subtitle_config=config.subtitles,
            source_code=source_code,
            target_code=target_code,
        )
        outputs.append(project.temp / "manual_translations.json")
        if _target_subtitle(project, config).is_file():
            english = parse_subtitle(project.english_srt)
            chinese = parse_subtitle(project.chinese_srt)
            localized, additional = _write_localized_subtitles(
                project,
                english,
                chinese,
                config,
                load_project_metadata(project),
            )
            outputs.extend(localized)
            warnings.extend(additional)
            state.mark_status("translation_ready")
    console.print(f"[green]Imported translations:[/] {imported}/{total} cues")
    if imported == total:
        console.print(
            f"[bold green]Target subtitles are complete:[/] "
            f"{_target_subtitle(project, config)}\n"
            f'Next: python main.py render "{project.root}"'
        )
    else:
        console.print(
            f"Import the remaining translated chunks ({total - imported} cues outstanding)."
        )
    for warning in warnings:
        console.print(f"[yellow]Warning:[/] {warning}")


@app.command("translate")
def translate_command(
    project_path: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    config_path: ConfigOption = None,
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="manual, offline, ollama, or openai-compatible."),
    ] = None,
) -> None:
    """Export manual chunks or run configured OpenAI-compatible translation."""
    project = _project(project_path)
    config = load_config(config_path) if config_path else load_project_config(project)
    if provider:
        if provider not in {"manual", "offline", "ollama", "openai-compatible"}:
            raise LocalizerError(
                "--provider must be manual, offline, ollama, or openai-compatible."
            )
        config = config.model_copy(
            update={"translation": config.translation.model_copy(update={"provider": provider})}
        )
    save_project_config(project, config)
    if config.translation.provider == "manual":
        translate_export_command(project.root, config_path=None)
        return
    state = PipelineState(project.state_file)
    with state.step(
        "translate",
        input_hash=hash_file(_source_subtitle(project, config)),
        config_hash=stable_hash(config.translation),
    ) as step_outputs:
        if config.translation.provider == "offline":
            outputs, warnings = translate_with_offline(project, config)
        elif config.translation.provider == "ollama":
            outputs, warnings = translate_with_local_ai(project, config)
        else:
            outputs, warnings = translate_with_api(project, config)
        step_outputs.extend(outputs)
    state.mark_status("translation_ready")
    console.print(f"[green]Translation complete:[/] {', '.join(str(path) for path in outputs)}")
    for warning in warnings:
        console.print(f"[yellow]Warning:[/] {warning}")


@app.command("render")
def render_command(
    project_path: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    config_path: ConfigOption = None,
) -> None:
    """Render and validate the final hard-subtitled MP4."""
    project = _project(project_path)
    config = load_config(config_path) if config_path else load_project_config(project)
    metadata = load_project_metadata(project)
    state = PipelineState(project.state_file)
    previous_warnings, previous_outputs = load_report_context(project.logs)
    report_warnings = list(dict.fromkeys([*previous_warnings, *state.data.warnings]))
    report_warnings = list(dict.fromkeys([*report_warnings, *rendering_media_warnings(metadata)]))
    softsub_warning = ""
    source = find_source_video(project)
    subtitle = (
        _target_ass(project, config) if config.subtitle_mode == "chinese" else project.bilingual_ass
    )
    with state.step(
        "render",
        input_hash=stable_hash(
            {
                "source": hash_file(source),
                "subtitle": hash_file(subtitle) if subtitle.is_file() else "",
            }
        ),
        config_hash=stable_hash(config.render),
    ) as outputs:
        output = render_project(project, config)
        outputs.append(output)
        if config.render.soft_subtitles:
            try:
                outputs.append(render_softsub_project(project, config))
            except LocalizerError as exc:
                softsub_warning = (
                    "Selectable subtitle MP4 was not created; the hard-subtitle video is still "
                    f"ready. Details: {exc}"
                )
                console.print(
                    "[yellow]Selectable subtitle version was not created; the hard-subtitle "
                    f"video is still ready. Details: {exc}[/]"
                )
    _, target_code = _language_pair(config)
    target_cues = parse_subtitle(_target_subtitle(project, config))
    source_path = _source_subtitle(project, config)
    source_quality_cues = parse_subtitle(source_path) if source_path.is_file() else []
    glossary_path = Path(config.translation.glossary_file)
    if not glossary_path.is_absolute():
        candidates = [project.root / glossary_path, Path.cwd() / glossary_path]
        glossary_path = next((path for path in candidates if path.is_file()), candidates[0])
    quality = audit_subtitles(
        target_cues,
        language=target_code,
        max_lines=config.subtitles.max_lines,
        preferred_line_length=config.subtitles.max_chinese_chars_per_line,
        source_cues=source_quality_cues,
        glossary=load_glossary(glossary_path),
    )
    atomic_write_json(project.logs / "subtitle_quality.json", quality)
    if softsub_warning:
        report_warnings.append(softsub_warning)
    report_warnings = list(dict.fromkeys(report_warnings))
    if state.data.warnings != report_warnings:
        state.data.warnings = report_warnings
        state.save()
    state.mark_status("completed")
    report = build_report(
        metadata,
        state.data,
        subtitle_source=metadata.subtitle_kind,
        whisper_model=(
            config.transcription.model
            if metadata.subtitle_kind.startswith("faster-whisper")
            else ""
        ),
        translation_provider=config.translation.provider,
        cue_count=len(target_cues),
        flagged_cues=quality["flagged_cue_ids"],
        render_parameters=config.render.model_dump(mode="json"),
        subtitle_quality=quality,
        output_paths=[*previous_outputs, *outputs],
        warnings=report_warnings,
    )
    write_report(project.logs, report)
    console.print(f"[bold green]Rendered and validated:[/] {output}")
    if len(outputs) > 1:
        console.print("[green]Selectable subtitle version:[/] " + str(outputs[-1]))
    if quality["flagged_cue_count"]:
        console.print(
            "[yellow]Subtitle review:[/] "
            f"{quality['flagged_cue_count']} cue(s) flagged in {project.logs / 'subtitle_quality.json'}"
        )


@app.command("preview")
def preview_command(
    project_path: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    start: Annotated[float, typer.Option("--start", min=0)] = 0,
    duration: Annotated[float, typer.Option("--duration", min=0.1)] = 15,
    config_path: ConfigOption = None,
) -> None:
    """Render a short styled subtitle preview clip."""
    project = _project(project_path)
    config = load_config(config_path) if config_path else load_project_config(project)
    metadata = load_project_metadata(project)
    subtitle = (
        _target_ass(project, config) if config.subtitle_mode == "chinese" else project.bilingual_ass
    )
    if not subtitle.is_file():
        raise LocalizerError("Styled subtitles are not ready. Import/translate subtitles first.")
    output = project.rendered / f"preview_{start:g}_{duration:g}.mp4"
    render_preview(
        find_source_video(project),
        subtitle,
        output,
        config.render,
        source_audio_codec=metadata.audio_codec,
        start=start,
        duration=duration,
    )
    console.print(f"[green]Preview created:[/] {output}")


@app.command("enhancement-preview")
def enhancement_preview_command(
    project_path: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    start: Annotated[float, typer.Option("--start", min=0)] = 0,
    duration: Annotated[float, typer.Option("--duration", min=1, max=30)] = 10,
    config_path: ConfigOption = None,
) -> None:
    """Create a short left-original/right-AI comparison before a full enhancement run."""
    project = _project(project_path)
    config = load_config(config_path) if config_path else load_project_config(project)
    metadata = load_project_metadata(project)
    if not metadata.width or not metadata.height or not metadata.frame_rate:
        raise LocalizerError("Video dimensions and frame rate are required for AI preview.")
    output = project.rendered / f"enhancement_comparison_{start:g}_{duration:g}.mp4"
    result = render_enhancement_comparison(
        find_source_video(project),
        output,
        source_width=metadata.width,
        source_height=metadata.height,
        frame_rate=metadata.frame_rate,
        source_duration=metadata.duration,
        source_audio_codec=metadata.audio_codec,
        render=config.render,
        enhancement=config.enhancement,
        start_seconds=start,
        duration_seconds=duration,
    )
    console.print(
        f"[green]Comparison created:[/] {result.output}\n"
        f"Left: original · Right: AI enhanced · Preview took {result.elapsed_seconds:.1f}s · "
        f"Estimated full-video enhancement: {result.estimated_full_seconds / 60:.1f} min"
    )


@app.command("metadata")
def metadata_command(
    project_path: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    config_path: ConfigOption = None,
) -> None:
    """Generate conservative publishing metadata drafts for human review."""
    project = _project(project_path)
    config = load_config(config_path) if config_path else load_project_config(project)
    outputs = generate_publishing_assets(
        load_project_metadata(project), project.publishing, config.publishing, config.rights
    )
    console.print(
        f"[green]Publishing drafts created:[/] {project.publishing}\n"
        "The title and permission/license fields are intentionally marked for human review."
    )
    for output in outputs:
        console.print(f"- {output.name}")


@app.command("validate")
def validate_command(
    project_path: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    decode: Annotated[bool, typer.Option("--decode/--no-decode")] = True,
) -> None:
    """Validate subtitle timing and any rendered MP4."""
    project = _project(project_path)
    metadata = load_project_metadata(project)
    checked = 0
    for subtitle in (
        project.english_srt,
        project.chinese_srt,
        project.bilingual_srt,
    ):
        if subtitle.is_file():
            errors = validate_cues(parse_subtitle(subtitle))
            if errors:
                raise LocalizerError(f"{subtitle.name} is invalid:\n" + "\n".join(errors))
            console.print(f"[green]Valid:[/] {subtitle}")
            checked += 1
    for rendered in (project.chinese_hardsub, project.english_hardsub):
        if rendered.is_file():
            validate_rendered_video(rendered, expected_duration=metadata.duration, decode=decode)
            console.print(f"[green]Valid and decodable:[/] {rendered}")
            checked += 1
    if not checked:
        raise LocalizerError("No subtitle or rendered output files exist yet.")


@app.command("clean")
def clean_command(
    project_path: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Remove only temporary/cache/partial files; preserve sources and final assets."""
    project = _project(project_path)
    if not yes and not typer.confirm(
        f"Remove temporary/cache files under {project.root}? Final outputs will be preserved."
    ):
        raise typer.Abort()
    temp = ensure_within(project.temp, project.root)
    if temp.is_dir():
        shutil.rmtree(temp)
    temp.mkdir(parents=True, exist_ok=True)
    removed_partials = 0
    for partial in project.root.rglob("*.partial.*"):
        ensure_within(partial, project.root).unlink(missing_ok=True)
        removed_partials += 1
    console.print(
        f"[green]Cleaned:[/] temporary cache and {removed_partials} partial file(s). "
        "Source and final assets were preserved."
    )


@app.command("doctor")
def doctor_command(
    config_path: ConfigOption = None,
) -> None:
    """Check Python, FFmpeg, yt-dlp, Whisper/CUDA, fonts, and output access."""
    config = load_config(config_path)
    checks = run_doctor(
        config.output_directory.expanduser(),
        offline_model_directory=config.translation.offline_model_directory,
        offline_zh_en_model_directory=config.translation.offline_zh_en_model_directory,
    )
    table = Table("Check", "Status", "Details")
    styles = {"ok": "green", "missing": "red", "warning": "yellow", "optional": "cyan"}
    for check in checks:
        table.add_row(check.name, f"[{styles[check.status]}]{check.status}[/]", check.detail)
    console.print(table)
    missing = [check for check in checks if check.required and check.status != "ok"]
    if missing:
        error_console.print(
            "[red]Required dependencies are missing.[/] See README.md for Windows setup."
        )
        raise typer.Exit(1)


@app.command("support-bundle")
def support_bundle_command(
    project_path: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Destination ZIP file or directory."),
    ] = None,
) -> None:
    """Create a redacted diagnostic ZIP without media or subtitle text."""
    bundle = create_support_bundle(_project(project_path), output)
    console.print(f"[green]Support bundle created:[/] {bundle}")


@app.command("version")
def version_command() -> None:
    """Print the application version."""
    console.print(__version__)


@app.command("gui")
def gui_command() -> None:
    """Open the local paste-a-link desktop interface."""
    from .gui import run_gui

    run_gui()


KNOWN_COMMANDS = {
    "preflight",
    "process",
    "batch",
    "inspect",
    "transcribe",
    "translate",
    "translate-export",
    "translate-import",
    "render",
    "preview",
    "enhancement-preview",
    "metadata",
    "validate",
    "clean",
    "doctor",
    "support-bundle",
    "gui",
    "version",
}


def normalize_argv(argv: list[str]) -> list[str]:
    if not argv:
        return argv
    if argv[0] == "--batch":
        return ["batch", *argv[1:]]
    if argv[0] not in KNOWN_COMMANDS and not argv[0].startswith("-"):
        return ["process", *argv]
    return argv


def run() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    configure_logging()
    argv = normalize_argv(sys.argv[1:])
    try:
        result = app(args=argv, prog_name=Path(sys.argv[0]).name, standalone_mode=False)
        if isinstance(result, int) and result:
            raise SystemExit(result)
    except LocalizerError as exc:
        error_console.print(f"[bold red]Error:[/] {exc}")
        raise SystemExit(1) from None
    except typer.Exit as exc:
        raise SystemExit(exc.exit_code) from None
    except Exception as exc:
        show = getattr(exc, "show", None)
        exit_code = getattr(exc, "exit_code", None)
        if callable(show) and isinstance(exit_code, int):
            show(file=error_console.file)
            raise SystemExit(exit_code) from None
        raise
