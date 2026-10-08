"""Bilibili and Douyin adapters using optional local account sessions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from ..config import DownloadConfig
from ..errors import InputValidationError, LocalizerError
from ..models import SourceMetadata
from ..utils.hashing import hash_text
from .accounts import apply_account
from .youtube import PlatformDownloadLogger, _youtube_dl, download_media

HOSTS = {
    "bilibili": {"bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv"},
    "douyin": {
        "douyin.com",
        "www.douyin.com",
        "v.douyin.com",
        "iesdouyin.com",
        "www.iesdouyin.com",
    },
}


def normalize_share_input(value: str) -> str:
    value = value.strip()
    matches = re.findall(r"https?://[^\s<>\"“”]+", value)
    if len(matches) == 1 and platform_for_url(matches[0].rstrip(".,，。!！;；)）]】")):
        return matches[0].rstrip(".,，。!！;；)）]】")
    return value


def platform_for_url(value: str) -> str | None:
    try:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return None
        return next(
            (name for name, hosts in HOSTS.items() if (parsed.hostname or "").lower() in hosts),
            None,
        )
    except ValueError:
        return None


def platform_source_id(value: str) -> str:
    platform = platform_for_url(value)
    parsed = urlparse(value)
    if platform == "bilibili" and (
        match := re.search(r"/(BV[0-9A-Za-z]+|av\d+)(?:/|$)", parsed.path)
    ):
        part = parse_qs(parsed.query).get("p", ["1"])[0]
        if not part.isdigit():
            raise InputValidationError("B站分P编号无效。")
        return f"bilibili_{match[1]}_p{int(part)}"
    if platform == "douyin" and (match := re.search(r"/(?:video|note)/(\d+)", parsed.path)):
        return f"douyin_{match[1]}"
    return f"{platform}_{hash_text(value)[:12]}"


def _extract(value: str, *, flat: bool = False) -> dict[str, Any]:
    platform = platform_for_url(value)
    if not platform:
        raise InputValidationError("请粘贴 Bilibili 或抖音的视频链接。")
    try:
        with _youtube_dl(
            {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": not flat,
                "extract_flat": flat,
                "logger": PlatformDownloadLogger(),
            }
        ) as ydl:
            apply_account(ydl, platform)
            info = ydl.extract_info(value, download=False)
    except Exception as exc:
        if isinstance(exc, LocalizerError):
            raise
        # Do not copy upstream HTTP diagnostics that may contain signed URLs or session data.
        raise InputValidationError(
            f"{platform} 视频解析失败。请在“平台账号”刷新登录，确认视频可在网页播放，再重试；平台接口变化或限流也可能导致失败。"
        ) from None
    if not isinstance(info, dict):
        raise InputValidationError("平台未返回有效视频信息。")
    return info


def inspect_platform(value: str) -> tuple[SourceMetadata, dict[str, Any]]:
    value = normalize_share_input(value)
    platform = platform_for_url(value)
    info = _extract(value)
    if info.get("_type") in {"playlist", "multi_video"} or info.get("entries") is not None:
        entries = list(info.get("entries") or [])
        if len(entries) != 1:
            raise InputValidationError("这是多集或分P链接，请从“平台视频”选择要下载的集数。")
        info = entries[0]
    if info.get("is_drm") or info.get("_has_drm"):
        raise InputValidationError("该视频使用 DRM，无法下载。")
    if info.get("is_live") or info.get("live_status") == "is_live":
        raise InputValidationError("请在直播结束后处理回放视频。")
    source_url = str(info.get("webpage_url") or value)
    if not platform_for_url(source_url):
        source_url = value
    if platform == "bilibili":
        part = parse_qs(urlparse(value).query).get("p")
        if part:
            parsed = urlparse(source_url)
            query = parse_qs(parsed.query)
            query["p"] = part
            source_url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    identifier = platform_source_id(source_url)
    if "_" + hash_text(source_url)[:12] in identifier:
        identifier = f"{platform}_{re.sub(r'[^A-Za-z0-9_-]', '', str(info.get('id') or hash_text(value)[:12]))}"
    return SourceMetadata(
        source_type=platform or "",
        source_input=value,
        source_url=source_url,
        video_id=identifier,
        title=str(info.get("title") or identifier),
        channel=str(info.get("uploader") or info.get("channel") or ""),
        duration=float(info.get("duration") or 0),
        description=str(info.get("description") or ""),
        upload_date=str(info.get("upload_date") or ""),
        language=str(info.get("language") or ""),
        thumbnail_url=str(info.get("thumbnail") or ""),
        width=info.get("width"),
        height=info.get("height"),
        frame_rate=info.get("fps"),
        video_codec=str(info.get("vcodec") or ""),
        audio_codec=str(info.get("acodec") or ""),
    ), info


def list_platform_entries(value: str) -> list[tuple[str, str]]:
    value = normalize_share_input(value)
    info = _extract(value, flat=True)
    entries = list(info.get("entries") or [])
    if not entries:
        return [(str(info.get("title") or "当前视频"), value)]
    result = []
    for index, entry in enumerate(entries[:500], 1):
        url = str(entry.get("webpage_url") or entry.get("url") or "")
        missing_part = (
            platform_for_url(value) == "bilibili"
            and info.get("_type") == "multi_video"
            and "p" not in parse_qs(urlparse(url).query)
        )
        if not platform_for_url(url) or missing_part:
            if platform_for_url(value) != "bilibili":
                continue
            parsed = urlparse(value)
            query = parse_qs(parsed.query)
            query["p"] = [str(index)]
            url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
        result.append((str(entry.get("title") or f"第 {index} 集"), url))
    return result


def download_platform(value: str, destination: Path, config: DownloadConfig) -> Path:
    return download_media(
        value,
        destination,
        config,
        source_description="platform video",
        account_platform=platform_for_url(value),
    )
