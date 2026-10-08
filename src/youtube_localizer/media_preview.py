"""Fast, read-only media inspection used by the desktop task centre."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .download.direct import (
    inspect_direct_media,
    is_direct_media_candidate_url,
    is_direct_media_url,
)
from .download.local import inspect_local
from .download.platforms import inspect_platform, normalize_share_input, platform_for_url
from .download.webpage import inspect_webpage_media, is_webpage_url
from .download.youtube import inspect_youtube, is_youtube_url
from .errors import InputValidationError
from .inspection_cache import load_cached_inspection
from .models import SourceMetadata


@dataclass(frozen=True, slots=True)
class MediaPreview:
    source: str
    source_type: str
    title: str
    channel: str
    duration_seconds: float
    width: int | None
    height: int | None
    frame_rate: float | None
    thumbnail_url: str
    estimated_bytes: int | None
    metadata: SourceMetadata | None = None
    available_formats: tuple[str, ...] = ()


def _format_size(size_bytes: int | None) -> str:
    if not size_bytes or size_bytes < 1:
        return "大小待下载时确定"
    value = float(size_bytes)
    units = ("B", "KB", "MB", "GB", "TB")
    unit = units[0]
    for candidate in units:
        unit = candidate
        if value < 1024 or candidate == units[-1]:
            break
        value /= 1024
    precision = 0 if unit in {"B", "KB"} else 1
    return f"约 {value:.{precision}f} {unit}"


def _format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:d}:{seconds:02d}"


def estimate_download_bytes(info: dict[str, Any]) -> int | None:
    """Estimate a best-video plus best-audio download from yt-dlp metadata."""

    def size_of(item: dict[str, Any]) -> int:
        value = item.get("filesize") or item.get("filesize_approx") or 0
        return int(value) if isinstance(value, (int, float)) and value > 0 else 0

    formats = [item for item in info.get("formats", []) if isinstance(item, dict)]
    combined = 0
    video_only = 0
    audio_only = 0
    for item in formats:
        size = size_of(item)
        has_video = item.get("vcodec") not in {None, "", "none"}
        has_audio = item.get("acodec") not in {None, "", "none"}
        if has_video and has_audio:
            combined = max(combined, size)
        elif has_video:
            video_only = max(video_only, size)
        elif has_audio:
            audio_only = max(audio_only, size)
    separate = video_only + audio_only if video_only else 0
    top_level = size_of(info)
    estimate = max(combined, separate, top_level)
    return estimate or None


def _from_metadata(
    source: str,
    metadata: SourceMetadata,
    *,
    estimated_bytes: int | None,
    formats: list[dict[str, Any]] | None = None,
) -> MediaPreview:
    return MediaPreview(
        source=source,
        source_type=metadata.source_type,
        title=metadata.title,
        channel=metadata.channel,
        duration_seconds=metadata.duration,
        width=metadata.width,
        height=metadata.height,
        frame_rate=metadata.frame_rate,
        thumbnail_url=metadata.thumbnail_url,
        estimated_bytes=estimated_bytes,
        metadata=metadata,
        available_formats=tuple(
            dict.fromkeys(
                f"{item.get('height')}p · {float(item.get('fps') or 0):g} FPS · {item.get('vcodec') or '视频'}"
                for item in sorted(
                    formats or [],
                    key=lambda item: (item.get("height") or 0, item.get("fps") or 0),
                    reverse=True,
                )
                if item.get("height") and item.get("vcodec") not in {None, "none"}
            )
        ),
    )


def inspect_media_preview(source: str) -> MediaPreview:
    """Inspect a supported source without downloading or modifying a project."""
    value = normalize_share_input(source)
    if platform_for_url(value):
        metadata, info = inspect_platform(value)
        return _from_metadata(
            value,
            metadata,
            estimated_bytes=estimate_download_bytes(info),
            formats=info.get("formats"),
        )
    if cached := load_cached_inspection(value):
        estimated_bytes = None
        if cached.source_type == "local":
            try:
                estimated_bytes = Path(value).expanduser().resolve().stat().st_size
            except OSError:
                estimated_bytes = None
        return _from_metadata(value, cached, estimated_bytes=estimated_bytes)
    if is_youtube_url(value):
        metadata, info = inspect_youtube(value)
        return _from_metadata(
            value,
            metadata,
            estimated_bytes=estimate_download_bytes(info),
            formats=info.get("formats"),
        )
    if is_direct_media_url(value):
        metadata, info = inspect_direct_media(value)
        return _from_metadata(value, metadata, estimated_bytes=estimate_download_bytes(info))
    if is_direct_media_candidate_url(value):
        try:
            metadata, info = inspect_direct_media(value)
        except InputValidationError as direct_error:
            try:
                metadata, info = inspect_webpage_media(value)
            except InputValidationError as page_error:
                if "没有返回 HTML 播放页" in str(page_error):
                    raise direct_error from page_error
                raise
        return _from_metadata(value, metadata, estimated_bytes=estimate_download_bytes(info))
    if is_webpage_url(value):
        metadata, info = inspect_webpage_media(value)
        return _from_metadata(value, metadata, estimated_bytes=estimate_download_bytes(info))
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        raise InputValidationError("网页地址无效，或包含了不允许使用的登录凭据。")
    path = Path(value).expanduser()
    metadata = inspect_local(path)
    return _from_metadata(value, metadata, estimated_bytes=path.resolve().stat().st_size)


def media_preview_summary(preview: MediaPreview) -> str:
    details = [_format_duration(preview.duration_seconds)]
    if preview.width and preview.height:
        details.append(f"{preview.width}×{preview.height}")
    elif preview.height:
        details.append(f"{preview.height}p")
    if preview.frame_rate:
        fps = round(preview.frame_rate, 2)
        details.append(f"{fps:g} FPS")
    details.append(_format_size(preview.estimated_bytes))
    return " · ".join(details)
