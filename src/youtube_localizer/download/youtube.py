from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from ..config import DownloadConfig
from ..errors import InputValidationError, LocalizerError
from ..models import SourceMetadata
from ..utils.files import atomic_write_json

LOGGER = logging.getLogger(__name__)
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
JAVASCRIPT_RUNTIME_EXECUTABLES = {
    "deno": ("deno.exe", "deno"),
    "node": ("node.exe", "node"),
    "quickjs": ("qjs.exe", "qjs", "quickjs.exe", "quickjs"),
    "bun": ("bun.exe", "bun"),
}


@dataclass(frozen=True)
class YouTubeDownloadResult:
    video: Path
    warnings: tuple[str, ...] = ()


def is_youtube_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and (parsed.hostname or "").lower() in YOUTUBE_HOSTS


def youtube_video_id(value: str) -> str | None:
    if not is_youtube_url(value):
        return None
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
    elif parsed.path == "/watch":
        candidate = parse_qs(parsed.query).get("v", [""])[0]
    elif parsed.path.startswith(("/shorts/", "/live/", "/embed/")):
        candidate = parsed.path.strip("/").split("/")[1]
    else:
        return None
    return candidate if VIDEO_ID_RE.fullmatch(candidate or "") else None


def _youtube_dl(options: dict[str, Any]):
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise LocalizerError("yt-dlp is not installed. Run: python -m pip install yt-dlp") from exc
    return YoutubeDL(options)


def discover_javascript_runtimes() -> dict[str, str]:
    """Find yt-dlp-compatible runtimes, including executables beside the active Python."""
    discovered: dict[str, str] = {}
    python_directory = Path(sys.executable).resolve().parent
    for runtime, executable_names in JAVASCRIPT_RUNTIME_EXECUTABLES.items():
        path: Path | None = None
        for executable_name in executable_names:
            for local in (
                python_directory / executable_name,
                python_directory / "Scripts" / executable_name,
            ):
                if local.is_file():
                    path = local
                    break
            if path:
                break
            if located := shutil.which(executable_name):
                path = Path(located)
                break
        if path:
            discovered[runtime] = str(path.resolve())
    return discovered


def _enable_javascript_runtime(options: dict[str, Any]) -> None:
    runtimes = discover_javascript_runtimes()
    if runtimes:
        options["js_runtimes"] = {runtime: {"path": path} for runtime, path in runtimes.items()}


def _validate_info(info: dict[str, Any]) -> None:
    availability = (info.get("availability") or "public").lower()
    if availability in {"private", "subscriber_only", "premium_only", "needs_auth"}:
        raise InputValidationError(
            f"This video is not publicly available ({availability}). "
            "The application does not bypass access controls."
        )
    if int(info.get("age_limit") or 0) >= 18:
        raise InputValidationError(
            "This video is age-restricted. Authenticated access is intentionally unsupported."
        )
    if info.get("is_drm") or info.get("_has_drm"):
        raise InputValidationError("DRM-protected videos are not supported.")
    if info.get("live_status") == "is_live":
        raise InputValidationError("Live streams must finish before they can be processed.")


def inspect_youtube(url: str) -> tuple[SourceMetadata, dict[str, Any]]:
    video_id = youtube_video_id(url)
    if not video_id:
        raise InputValidationError(
            "Invalid or unsupported YouTube URL. Use a public youtube.com/watch, shorts, live, "
            "embed, or youtu.be video URL."
        )
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
    }
    _enable_javascript_runtime(options)
    try:
        with _youtube_dl(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        if isinstance(exc, LocalizerError):
            raise
        raise InputValidationError(
            "yt-dlp could not inspect this video. Confirm that it is public, available in your "
            f"region, and not protected. Details: {exc}"
        ) from exc
    if not isinstance(info, dict):
        raise InputValidationError("yt-dlp did not return video metadata.")
    _validate_info(info)
    metadata = SourceMetadata(
        source_type="youtube",
        source_input=url,
        source_url=info.get("webpage_url") or url,
        video_id=str(info.get("id") or video_id),
        title=info.get("title") or f"YouTube video {video_id}",
        channel=info.get("channel") or info.get("uploader") or "",
        duration=float(info.get("duration") or 0),
        description=info.get("description") or "",
        upload_date=info.get("upload_date") or "",
        language=info.get("language") or "",
        thumbnail_url=info.get("thumbnail") or "",
        width=info.get("width"),
        height=info.get("height"),
        frame_rate=info.get("fps"),
        video_codec=info.get("vcodec") or "",
        audio_codec=info.get("acodec") or "",
    )
    return metadata, info


def _run_youtube_download(url: str, options: dict[str, Any]) -> Path:
    with _youtube_dl(options) as ydl:
        downloaded_info = ydl.extract_info(url, download=True)
        if not isinstance(downloaded_info, dict):
            raise LocalizerError("yt-dlp did not return downloaded video information.")
        return Path(ydl.prepare_filename(downloaded_info))


def _is_temporary_source_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "http error 429",
            "too many requests",
            "http error 500",
            "http error 502",
            "http error 503",
            "http error 504",
            "temporarily unavailable",
        )
    )


def download_media(
    url: str,
    destination_dir: Path,
    config: DownloadConfig,
    *,
    source_description: str,
) -> Path:
    """Download one public, non-DRM media source without requesting captions."""
    destination_dir.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {
        "quiet": False,
        "noplaylist": True,
        "continuedl": True,
        "overwrites": False,
        "format": config.format,
        "format_sort": list(config.format_sort),
        # DASH/HLS streams are the usual high-resolution YouTube formats. Fetching a bounded
        # number of fragments concurrently improves throughput without overwhelming the source.
        "concurrent_fragment_downloads": config.concurrent_fragment_downloads,
        "outtmpl": str(destination_dir / "download.%(ext)s"),
        # Source captions are deliberately disabled. The pipeline always uses its bundled
        # Whisper model so timing and recognition behavior are consistent and offline-capable.
        "writesubtitles": False,
        "writeautomaticsub": False,
        "subtitleslangs": [],
    }
    _enable_javascript_runtime(options)
    if config.prefer_mp4:
        options["merge_output_format"] = "mp4"
    prepared: Path | None = None
    last_error: Exception | None = None
    for attempt in range(1, config.retry_attempts + 1):
        try:
            prepared = _run_youtube_download(url, options)
            break
        except Exception as exc:
            if isinstance(exc, LocalizerError):
                raise
            last_error = exc
            if not _is_temporary_source_error(exc) or attempt >= config.retry_attempts:
                break
            delay = config.retry_delay_seconds * attempt
            LOGGER.warning(
                "%s temporarily rejected the download (attempt %s/%s). "
                "Keeping the partial file and retrying in %s seconds.",
                source_description,
                attempt,
                config.retry_attempts,
                delay,
            )
            time.sleep(delay)
    if prepared is None:
        assert last_error is not None
        if _is_temporary_source_error(last_error):
            raise LocalizerError(
                f"{source_description} temporarily limited or rejected the request. The partial "
                "download is retained; wait a few minutes and resume the project. The application "
                f"does not use cookies or bypass access controls. Details: {last_error}"
            ) from last_error
        raise LocalizerError(
            f"yt-dlp failed to download the {source_description}. The partial download is "
            f"retained so a later --resume can continue it. Details: {last_error}"
        ) from last_error

    candidates = [
        path
        for path in destination_dir.glob("download.*")
        if path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
        and ".part" not in path.name
    ]
    if prepared.is_file() and prepared not in candidates:
        candidates.append(prepared)
    if not candidates:
        raise LocalizerError("yt-dlp completed but no downloaded video file was found.")
    video = max(candidates, key=lambda item: item.stat().st_size)
    destination = destination_dir / f"source_video{video.suffix.lower()}"
    if video != destination:
        video.replace(destination)
    return destination


def download_youtube(
    url: str,
    _info: dict[str, Any],
    destination_dir: Path,
    config: DownloadConfig,
) -> YouTubeDownloadResult:
    """Download a public YouTube source at the configured best available quality."""
    return YouTubeDownloadResult(
        video=download_media(
            url,
            destination_dir,
            config,
            source_description="public YouTube video",
        )
    )


def save_thumbnail(url: str, destination: Path) -> None:
    if not url:
        return
    try:
        response = httpx.get(url, follow_redirects=True, timeout=30)
        response.raise_for_status()
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(response.content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, destination)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
    except (httpx.HTTPError, OSError) as exc:
        LOGGER.warning("Thumbnail download failed; processing will continue: %s", exc)


def save_raw_metadata(info: dict[str, Any], destination: Path) -> None:
    safe = json.loads(json.dumps(info, ensure_ascii=False, default=str))
    atomic_write_json(destination, safe)
