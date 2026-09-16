"""Resolve explicitly declared media from an authorized public webpage.

This module intentionally does not execute JavaScript, inspect browser state, solve anti-bot
challenges, traverse embedded players, or copy cookies.  It only accepts media URLs published in
standard HTML5, Open Graph, Twitter Card, or VideoObject JSON-LD fields.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from ..errors import InputValidationError
from ..models import SourceMetadata
from ..utils.hashing import hash_text
from .direct import direct_media_id, inspect_direct_media, is_direct_media_candidate_url

MAX_WEBPAGE_BYTES = 2 * 1024 * 1024
MAX_WEBPAGE_REDIRECTS = 5
MAX_MEDIA_CANDIDATES = 4
MAX_URL_CHARACTERS = 8_192
MAX_TITLE_CHARACTERS = 500
MAX_DESCRIPTION_CHARACTERS = 20_000
MAX_JSON_LD_NODES = 10_000
WEBPAGE_USER_AGENT = "Localize-Studio/0.7 (authorized-public-media-page-resolver)"
HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
MEDIA_META_KEYS = frozenset(
    {
        "og:video",
        "og:video:url",
        "og:video:secure_url",
        "twitter:player:stream",
    }
)


@dataclass(frozen=True, slots=True)
class ResolvedWebpageMedia:
    page_url: str
    media_url: str
    page_title: str = ""
    description: str = ""
    thumbnail_url: str = ""
    declaration: str = ""


@dataclass(frozen=True, slots=True)
class _MediaDeclaration:
    value: str
    kind: str


class _PublicMediaHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base_href = ""
        self.page_title = ""
        self.description = ""
        self.thumbnail_url = ""
        self.media: list[_MediaDeclaration] = []
        self.iframe_count = 0
        self._video_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._in_json_ld = False
        self._json_ld_parts: list[str] = []
        self._json_ld_documents: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        values = {key.casefold(): value or "" for key, value in attrs}
        if name == "base" and not self.base_href:
            self.base_href = values.get("href", "").strip()
        elif name == "title":
            self._in_title = True
        elif name == "video":
            self._video_depth += 1
            self._append_media(values.get("src", ""), "HTML5 video")
        elif name == "source" and self._video_depth:
            self._append_media(values.get("src", ""), "HTML5 video source")
        elif name == "iframe":
            self.iframe_count += 1
        elif name == "meta":
            key = (values.get("property") or values.get("name") or "").casefold()
            content = values.get("content", "").strip()
            if key in MEDIA_META_KEYS:
                self._append_media(content, key)
            elif key == "og:title" and content:
                self.page_title = content[:MAX_TITLE_CHARACTERS]
            elif key in {"description", "og:description"} and content and not self.description:
                self.description = content[:MAX_DESCRIPTION_CHARACTERS]
            elif key in {"og:image", "twitter:image"} and content and not self.thumbnail_url:
                self.thumbnail_url = content[:MAX_URL_CHARACTERS]
        elif name == "script" and values.get("type", "").casefold() == "application/ld+json":
            self._in_json_ld = True
            self._json_ld_parts = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name == "title":
            self._in_title = False
            if not self.page_title:
                self.page_title = " ".join("".join(self._title_parts).split())
        elif name == "video" and self._video_depth:
            self._video_depth -= 1
        elif name == "script" and self._in_json_ld:
            self._in_json_ld = False
            value = "".join(self._json_ld_parts).strip()
            if value:
                self._json_ld_documents.append(value)
            self._json_ld_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._in_json_ld:
            self._json_ld_parts.append(data)

    def close(self) -> None:
        super().close()
        for document in self._json_ld_documents:
            try:
                payload = json.loads(document)
            except (TypeError, ValueError):
                continue
            self._collect_video_objects(payload)

    def _append_media(self, value: str, kind: str) -> None:
        cleaned = value.strip()
        if cleaned and len(cleaned) <= MAX_URL_CHARACTERS:
            self.media.append(_MediaDeclaration(cleaned, kind))

    def _collect_video_objects(self, value: object) -> None:
        pending = [value]
        visited = 0
        while pending and visited < MAX_JSON_LD_NODES:
            current = pending.pop()
            visited += 1
            if isinstance(current, list):
                pending.extend(current)
                continue
            if not isinstance(current, dict):
                continue
            raw_type = current.get("@type")
            types = raw_type if isinstance(raw_type, list) else [raw_type]
            is_video = any(str(item).casefold() == "videoobject" for item in types)
            if is_video:
                content_url = current.get("contentUrl")
                if isinstance(content_url, str):
                    self._append_media(content_url, "VideoObject contentUrl")
                if not self.page_title and isinstance(current.get("name"), str):
                    self.page_title = str(current["name"]).strip()[:MAX_TITLE_CHARACTERS]
                if not self.description and isinstance(current.get("description"), str):
                    self.description = str(current["description"]).strip()[
                        :MAX_DESCRIPTION_CHARACTERS
                    ]
                thumbnail = current.get("thumbnailUrl")
                if not self.thumbnail_url:
                    if isinstance(thumbnail, str):
                        self.thumbnail_url = thumbnail.strip()[:MAX_URL_CHARACTERS]
                    elif (
                        isinstance(thumbnail, list) and thumbnail and isinstance(thumbnail[0], str)
                    ):
                        self.thumbnail_url = thumbnail[0].strip()[:MAX_URL_CHARACTERS]
            pending.extend(child for child in current.values() if isinstance(child, (dict, list)))


def is_webpage_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and not parsed.username
        and not parsed.password
    )


def webpage_media_id(value: str) -> str:
    if not is_webpage_url(value):
        raise InputValidationError("网页地址必须是没有用户名或密码的公开 http(s) URL。")
    if is_direct_media_candidate_url(value):
        # Extensionless addresses are ambiguous until their content type is probed. Keep the
        # same identifier that the direct-media path used so a later classification can resume.
        return direct_media_id(value)
    parsed = urlparse(value.strip())
    canonical = urlunparse(
        (
            parsed.scheme.casefold(),
            (parsed.netloc or "").casefold(),
            parsed.path or "/",
            "",
            parsed.query,
            "",
        )
    )
    return hash_text(canonical)[:10]


def _assert_public_http_url(value: str) -> None:
    if not is_webpage_url(value):
        raise InputValidationError("网页和媒体地址必须是没有用户名或密码的公开 http(s) URL。")
    hostname = urlparse(value).hostname or ""
    try:
        addresses = {
            item[4][0] for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        }
    except OSError as exc:
        raise InputValidationError(f"无法解析网页主机名：{hostname}") from exc
    if not addresses:
        raise InputValidationError(f"网页主机名没有可用地址：{hostname}")
    for raw_address in addresses:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise InputValidationError(f"网页主机返回了无效地址：{hostname}") from exc
        if not address.is_global:
            raise InputValidationError(
                "为保护本机和局域网，网页解析只允许公网地址；localhost、局域网和保留地址不受支持。"
            )


def _read_bounded_html(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "").partition(";")[0].strip().casefold()
    if content_type and content_type not in HTML_CONTENT_TYPES:
        raise InputValidationError(
            "该 URL 没有返回 HTML 播放页。若它本身是视频，请粘贴实际 MP4/HLS/DASH 直链。"
        )
    content_length = response.headers.get("content-length", "")
    if content_length.isdigit() and int(content_length) > MAX_WEBPAGE_BYTES:
        raise InputValidationError("播放页超过 2 MiB 安全读取上限，无法解析。")
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > MAX_WEBPAGE_BYTES:
            raise InputValidationError("播放页超过 2 MiB 安全读取上限，无法解析。")
    encoding = response.encoding or "utf-8"
    return bytes(body).decode(encoding, errors="replace")


def _fetch_public_html(url: str) -> tuple[str, str]:
    current = url.strip()
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": WEBPAGE_USER_AGENT,
    }
    try:
        with httpx.Client(headers=headers, follow_redirects=False, timeout=15) as client:
            for redirect_count in range(MAX_WEBPAGE_REDIRECTS + 1):
                _assert_public_http_url(current)
                with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location", "").strip()
                        if not location:
                            raise InputValidationError("播放页返回了无目标地址的重定向。")
                        if redirect_count >= MAX_WEBPAGE_REDIRECTS:
                            raise InputValidationError("播放页重定向次数过多，已停止解析。")
                        current = urljoin(current, location)
                        continue
                    if response.status_code == 403 and response.headers.get("cf-mitigated"):
                        raise InputValidationError(
                            "该站点要求 Cloudflare 浏览器验证。请在桌面版点击“浏览器抓取”，"
                            "并在独立 Edge 窗口中亲自完成验证、开始播放；程序不会绕过挑战。"
                        )
                    if response.status_code in {401, 403}:
                        raise InputValidationError(
                            "该播放页需要登录、Cookie 或额外权限；这些访问限制不会被绕过。"
                        )
                    if response.status_code == 429:
                        raise InputValidationError("该站点暂时限流，请稍后重试。")
                    response.raise_for_status()
                    return current, _read_bounded_html(response)
    except InputValidationError:
        raise
    except httpx.HTTPError as exc:
        raise InputValidationError(
            "无法读取这个公开播放页。请确认地址可直接访问，且不需要登录或浏览器验证。"
        ) from exc
    raise InputValidationError("无法完成播放页解析。")  # pragma: no cover


def _resolved_public_candidate(value: str, base_url: str) -> str | None:
    candidate = urljoin(base_url, value.strip())
    if not is_direct_media_candidate_url(candidate):
        return None
    _assert_public_http_url(candidate)
    return candidate


def _resolve_webpage_media_candidates(url: str) -> list[ResolvedWebpageMedia]:
    page_url, html = _fetch_public_html(url)
    parser = _PublicMediaHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except (AssertionError, ValueError) as exc:
        raise InputValidationError("播放页 HTML 无法安全解析。") from exc
    base_url = urljoin(page_url, parser.base_href) if parser.base_href else page_url
    priorities = {
        "HTML5 video": 0,
        "HTML5 video source": 0,
        "VideoObject contentUrl": 1,
        "og:video:secure_url": 2,
        "og:video:url": 2,
        "og:video": 2,
        "twitter:player:stream": 3,
    }
    declarations = sorted(
        enumerate(parser.media),
        key=lambda item: (priorities.get(item[1].kind, 9), item[0]),
    )
    rejected = 0
    seen: set[str] = set()
    candidates: list[ResolvedWebpageMedia] = []
    for _, declaration in declarations:
        try:
            media_url = _resolved_public_candidate(declaration.value, base_url)
        except InputValidationError:
            rejected += 1
            continue
        if media_url and media_url not in seen:
            seen.add(media_url)
            thumbnail = urljoin(base_url, parser.thumbnail_url) if parser.thumbnail_url else ""
            if thumbnail:
                try:
                    _assert_public_http_url(thumbnail)
                except InputValidationError:
                    thumbnail = ""
            candidates.append(
                ResolvedWebpageMedia(
                    page_url=page_url,
                    media_url=media_url,
                    page_title=parser.page_title[:MAX_TITLE_CHARACTERS],
                    description=parser.description[:MAX_DESCRIPTION_CHARACTERS],
                    thumbnail_url=thumbnail,
                    declaration=declaration.kind,
                )
            )
            if len(candidates) >= MAX_MEDIA_CANDIDATES:
                break
        elif not media_url:
            rejected += 1
    if candidates:
        return candidates
    if parser.iframe_count:
        raise InputValidationError(
            "页面只嵌入了第三方播放器，没有公开声明媒体直链；程序不会递归抓取 iframe、"
            "执行混淆脚本或复制 Cookie。请粘贴内容方提供的媒体直链。"
        )
    detail = "；找到的候选地址均不是可接受的公网媒体 URL" if rejected else ""
    raise InputValidationError(
        "页面没有在 HTML5、Open Graph 或 VideoObject JSON-LD 中公开声明媒体地址"
        f"{detail}。动态脚本、登录、DRM 和反爬验证不会被绕过。"
    )


def resolve_webpage_media(url: str) -> ResolvedWebpageMedia:
    """Return the preferred explicitly declared public media URL on *url*."""
    return _resolve_webpage_media_candidates(url)[0]


def inspect_webpage_media(url: str) -> tuple[SourceMetadata, dict[str, Any]]:
    """Resolve and inspect an authorized media file declared by a public webpage."""
    candidates = _resolve_webpage_media_candidates(url)
    first_error: InputValidationError | None = None
    resolved = candidates[0]
    direct: SourceMetadata | None = None
    info: dict[str, Any] | None = None
    for candidate in candidates:
        try:
            direct, info = inspect_direct_media(candidate.media_url)
        except InputValidationError as exc:
            first_error = first_error or exc
            continue
        resolved = candidate
        break
    if direct is None or info is None:
        raise InputValidationError(
            "页面公开声明了媒体地址，但这些地址都已失效、不可公开访问或不是可下载媒体。"
        ) from first_error
    parsed = urlparse(resolved.page_url)
    thumbnail_url = resolved.thumbnail_url or direct.thumbnail_url
    if thumbnail_url:
        try:
            _assert_public_http_url(thumbnail_url)
        except InputValidationError:
            thumbnail_url = ""
    metadata = direct.model_copy(
        update={
            "source_type": "webpage_media",
            "source_input": url.strip(),
            "source_url": resolved.media_url,
            "video_id": webpage_media_id(url),
            "title": resolved.page_title or direct.title,
            "channel": direct.channel or (parsed.hostname or ""),
            "description": resolved.description or direct.description,
            "thumbnail_url": thumbnail_url,
        }
    )
    info = dict(info)
    info["_localizer_source_page"] = resolved.page_url
    info["_localizer_media_declaration"] = resolved.declaration
    return metadata, info
