"""Source previews work before a project is processed, in an isolated CLI worker."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..config import AppConfig
from ..download.metadata import metadata_from_probe, probe_media
from ..download.platforms import platform_for_url
from ..download.youtube import download_media
from ..errors import LocalizerError
from ..models import ProjectPaths
from .super_resolution import EnhancementPreviewResult, render_enhancement_comparison


def preview_source(
    value: str, output: Path, config: AppConfig, *, start: float = 0, duration: float = 10
) -> EnhancementPreviewResult:
    from ..download.platforms import normalize_share_input
    from ..pipeline import _inspect_input, find_source_video, load_project_metadata

    value = normalize_share_input(value)
    path = (
        Path(value).expanduser() if not value.lower().startswith(("http://", "https://")) else None
    )
    if path is not None and path.is_dir():
        project = ProjectPaths(path)
        metadata = load_project_metadata(project)
        source = find_source_video(project)
    else:
        metadata, _ = _inspect_input(value)
        source = path
    if metadata.duration <= 0 or start >= metadata.duration:
        raise LocalizerError("无法确定视频时长，或预览起点超过视频长度。")
    duration = min(duration, metadata.duration - start)
    with tempfile.TemporaryDirectory(prefix="localizer-source-preview-") as directory:
        preview_start = start
        if source is None:
            source = download_media(
                metadata.source_url or value,
                Path(directory),
                config.download,
                source_description="preview clip",
                account_platform=platform_for_url(value),
                section=(start, start + duration),
            )
            preview_start = 0
        actual = metadata_from_probe(source, probe_media(source), video_id=metadata.video_id)
        if not actual.width or not actual.height or not actual.frame_rate:
            raise LocalizerError("视频缺少预览所需的分辨率或帧率信息。")
        result = render_enhancement_comparison(
            source,
            output,
            source_width=actual.width,
            source_height=actual.height,
            frame_rate=actual.frame_rate,
            source_duration=actual.duration,
            source_audio_codec=actual.audio_codec,
            render=config.render,
            enhancement=config.enhancement,
            start_seconds=preview_start,
            duration_seconds=duration,
        )
        return EnhancementPreviewResult(
            result.output,
            result.elapsed_seconds,
            result.elapsed_seconds
            * metadata.duration
            / max(0.1, min(duration, actual.duration - preview_start)),
        )
