"""Authorized direct-media URL support without webpage scraping or authentication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from ..config import DownloadConfig
from ..errors import InputValidationError, LocalizerError
from ..models import SourceMetadata
from ..utils.hashing import hash_text
from .youtube import (
    _enable_javascript_runtime,
    _validate_info,
    _youtube_dl,
    download_media,
)

DIRECT_MEDIA_EXTENSIONS = frozenset(
    {".mp4", ".m4v", ".mkv", ".mov", ".webm", ".avi", ".m3u8", ".mpd"}
)
PLAYBACK_PAGE_EXTENSIONS = frozenset({".asp", ".aspx", ".htm", ".html", ".jsp", ".php"})
DIRECT_MEDIA_CONTENT_TYPES = frozenset(
    {
        "application/dash+xml",
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
        "audio/mpegurl",
    }
)


@dataclass(frozen=True)
class DirectMediaDownloadResult:
    video: Path
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DirectMediaUrlAssessment:
    """Local-only explanation shown before a direct URL is inspected."""

    media_kind: str
    signed: bool
    expires_at: datetime | None
    expired: bool


def assess_direct_media_url(value: str, *, now: datetime | None = None) -> DirectMediaUrlAssessment:
    """Describe an explicit media URL without contacting its server.

    Expiry detection is deliberately conservative: only common, unambiguous epoch query
    parameters are interpreted. The network probe remains authoritative.
    """
    parsed = _parsed_direct_media_url(value)
    if not parsed or not is_direct_media_candidate_url(value):
        raise InputValidationError("媒体直链必须是完整的 HTTP(S) 地址，且不能内嵌登录凭据。")
    if "…" in value or parsed.path.endswith("..."):
        raise InputValidationError(
            "这条媒体直链已被截断。请复制完整地址；以“…”结尾的显示文字无法下载。"
        )

    suffix = Path(unquote(parsed.path)).suffix.lower()
    if suffix == ".m3u8":
        media_kind = "HLS 流媒体清单"
    elif suffix == ".mpd":
        media_kind = "DASH 流媒体清单"
    elif suffix in DIRECT_MEDIA_EXTENSIONS:
        media_kind = f"{suffix.removeprefix('.').upper()} 视频直链"
    else:
        media_kind = "无扩展名 CDN 媒体直链"

    query = parse_qs(parsed.query, keep_blank_values=True)
    lower_query = {key.casefold(): values for key, values in query.items()}
    expires_at: datetime | None = None
    for key in ("expires", "expire", "expiry", "exp"):
        raw_values = lower_query.get(key, ())
        if not raw_values:
            continue
        raw = raw_values[0].strip()
        try:
            epoch = int(raw)
            if epoch > 10_000_000_000:
                epoch //= 1000
            candidate = datetime.fromtimestamp(epoch, tz=UTC)
        except (OverflowError, OSError, ValueError):
            continue
        if 946_684_800 <= epoch <= 4_102_444_800:
            expires_at = candidate
            break
    current = now or datetime.now(tz=UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return DirectMediaUrlAssessment(
        media_kind=media_kind,
        signed=bool(parsed.query),
        expires_at=expires_at,
        expired=bool(expires_at and expires_at <= current),
    )


def _parsed_direct_media_url(value: str):
    try:
        return urlparse(value.strip())
    except ValueError:
        return None


def is_direct_media_candidate_url(value: str) -> bool:
    """Return whether *value* can safely be checked as an explicit media address.

    A playback-page extension is rejected before any request. URLs without a file extension are
    allowed here because large CDNs commonly serve MP4 files from opaque paths.
    """
    parsed = _parsed_direct_media_url(value)
    if not parsed:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and Path(unquote(parsed.path)).suffix.lower() not in PLAYBACK_PAGE_EXTENSIONS
    )


def is_direct_media_url(value: str) -> bool:
    """Return whether *value* names a media URL through a known filename extension."""
    parsed = _parsed_direct_media_url(value)
    return bool(
        parsed
        and is_direct_media_candidate_url(value)
        and Path(unquote(parsed.path)).suffix.lower() in DIRECT_MEDIA_EXTENSIONS
    )


def _content_type_is_media(content_type: str) -> bool:
    normalized = content_type.partition(";")[0].strip().lower()
    return normalized.startswith(("video/", "audio/")) or normalized in DIRECT_MEDIA_CONTENT_TYPES


def _probe_direct_media_content_type(url: str) -> str:
    """Verify an extensionless URL from response headers, without fetching the video body."""
    headers = {
        "Accept": "video/*,audio/*,application/dash+xml,application/vnd.apple.mpegurl,*/*;q=0.5",
        "User-Agent": "Localize-Studio/0.7 (authorized-direct-media-client)",
    }
    head_detail = ""
    try:
        response = httpx.head(url, headers=headers, follow_redirects=True, timeout=20)
        if response.is_success:
            content_type = response.headers.get("content-type", "")
            if _content_type_is_media(content_type):
                return content_type
            head_detail = f"HEAD returned {content_type or 'no content type'}"
        else:
            head_detail = f"HEAD returned HTTP {response.status_code}"
        if response.status_code in {401, 404, 410, 429}:
            response.raise_for_status()
    except httpx.HTTPError as exc:
        response = getattr(exc, "response", None)
        status_code = response.status_code if response is not None else None
        if status_code in {401, 404, 410, 429}:
            raise InputValidationError(_direct_http_failure_message(status_code)) from exc
        head_detail = str(exc)

    # Many signed CDNs reject HEAD (sometimes with 403) but permit a byte-range GET. The stream
    # context never consumes the response body, even if a server ignores the Range header.
    try:
        with httpx.stream(
            "GET",
            url,
            headers={**headers, "Range": "bytes=0-0"},
            follow_redirects=True,
            timeout=20,
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
    except httpx.HTTPError as exc:
        response = getattr(exc, "response", None)
        status_code = response.status_code if response is not None else None
        raise InputValidationError(
            _direct_http_failure_message(status_code, detail=head_detail)
        ) from exc
    if not _content_type_is_media(content_type):
        raise InputValidationError(
            "The direct URL did not identify itself as video or an HLS/DASH playlist. "
            "Paste the actual media address, not a playback webpage."
        )
    return content_type


def _direct_http_failure_message(status_code: int | None, *, detail: str = "") -> str:
    if status_code in {401, 403}:
        return (
            f"媒体服务器拒绝了这条直链（HTTP {status_code}）。链接可能已过期，或依赖当前浏览器的"
            "登录、Cookie 或 Referer；本程序不会导入这些凭据。请从内容方播放器重新复制完整的公开媒体地址。"
        )
    if status_code in {404, 410}:
        return f"这条媒体直链已失效或被移除（HTTP {status_code}）。请复制新的完整地址。"
    if status_code == 429:
        return "媒体服务器暂时限流（HTTP 429）。请稍后重试或重新复制最新的直链。"
    suffix = f" 探测详情：{detail}" if detail else ""
    return (
        "无法验证这条无扩展名媒体直链。请确认地址完整、尚未过期、可公开访问，且不受 DRM 保护。"
        + suffix
    )


def direct_media_id(value: str) -> str:
    """Generate a stable project ID while allowing refreshed signed-query URLs to resume."""
    parsed = _parsed_direct_media_url(value)
    if not parsed or not is_direct_media_candidate_url(value):
        raise InputValidationError(
            "Direct media URLs must be an http(s) media address without login credentials."
        )
    canonical = f"{parsed.hostname.lower()}{unquote(parsed.path)}"
    return hash_text(canonical)[:10]


def _fallback_title(value: str) -> str:
    parsed = _parsed_direct_media_url(value)
    if not parsed:
        return "Direct media"
    title = Path(unquote(parsed.path)).stem.strip()
    return title or "Direct media"


def inspect_direct_media(url: str) -> tuple[SourceMetadata, dict[str, Any]]:
    """Inspect a supplied public media file or HLS/DASH manifest with yt-dlp."""
    assessment = assess_direct_media_url(url)
    if assessment.expired:
        expiry = assessment.expires_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        raise InputValidationError(f"这条签名媒体直链显示已于 {expiry} 过期。请复制新的完整地址。")
    if not is_direct_media_url(url):
        _probe_direct_media_content_type(url)
    options: dict[str, Any] = {
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
        detail = str(exc)
        for status_code in (401, 403, 404, 410, 429):
            if f"HTTP Error {status_code}" in detail or f"HTTP {status_code}" in detail:
                raise InputValidationError(_direct_http_failure_message(status_code)) from exc
        raise InputValidationError(
            "yt-dlp could not inspect this direct media URL. Confirm that the address is current, "
            "publicly accessible, not DRM-protected, and points to the media file or playlist. "
            f"Details: {detail}"
        ) from exc
    if not isinstance(info, dict):
        raise InputValidationError("yt-dlp did not return media metadata.")
    _validate_info(info)
    return (
        SourceMetadata(
            source_type="direct_media",
            source_input=url,
            source_url=url,
            video_id=direct_media_id(url),
            title=info.get("title") or _fallback_title(url),
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
        ),
        info,
    )


def download_direct_media(
    url: str,
    _info: dict[str, Any],
    destination_dir: Path,
    config: DownloadConfig,
) -> DirectMediaDownloadResult:
    """Download a user-supplied direct media URL at the configured best quality."""
    return DirectMediaDownloadResult(
        video=download_media(
            url,
            destination_dir,
            config,
            source_description="direct media URL",
        )
    )
