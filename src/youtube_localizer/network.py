"""One explicit media route, shared by HTTP, yt-dlp and isolated browsers.

No global proxy environment mutation: local Ollama and browser control stay local.
Settings contain no credentials, and a queue passes a frozen route to its workers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import getproxies

import httpx

from .errors import InputValidationError
from .onboarding import onboarding_state_directory
from .utils.files import atomic_write_json, load_json

ROUTE_ENV = "LOCALIZER_MEDIA_ROUTE"
_context: ContextVar[NetworkRoute | None] = ContextVar("media_route", default=None)
_active: NetworkRoute | None = None


def normalize_proxy(value: str) -> str:
    if not isinstance(value, str):
        raise InputValidationError("代理地址格式无效，请在“网络与连接”重新填写地址。")
    value = value.strip()
    if not value or any(c.isspace() for c in value):
        raise InputValidationError("请填写代理地址，例如 http://127.0.0.1:7890。")
    if "://" not in value:
        value = "http://" + value
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (
            parsed.scheme not in {"http", "https", "socks5"}
            or not parsed.hostname
            or not port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
    except ValueError:
        raise InputValidationError(
            "代理须为 HTTP、HTTPS 或 SOCKS5 的主机:端口，不含账号、密码、路径或参数。"
            "需要认证时请使用本地代理客户端提供的无认证监听端口。"
        ) from None
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"{parsed.scheme}://{host}:{port}"


@dataclass(frozen=True)
class NetworkSettings:
    mode: str = "system"
    proxy: str = ""

    def validated(self) -> NetworkSettings:
        if not isinstance(self.mode, str) or self.mode not in {"direct", "system", "manual"}:
            raise InputValidationError("未知网络模式。")
        if not isinstance(self.proxy, str):
            raise InputValidationError("代理设置格式无效，请在“网络与连接”重新保存。")
        return NetworkSettings(
            self.mode, normalize_proxy(self.proxy) if self.mode == "manual" else ""
        )


@dataclass(frozen=True)
class NetworkRoute:
    mode: str
    proxy: str = ""

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.proxy.encode()).hexdigest()[:20]

    @property
    def label(self) -> str:
        return ("代理：" + self.proxy) if self.proxy else "直接连接"

    def httpx_options(self) -> dict:
        return {"trust_env": False, "proxy": self.proxy or None}

    def browser_args(self) -> list[str]:
        # Disable QUIC so media cannot take a UDP path outside the chosen proxy.
        return (
            ["--disable-quic", f"--proxy-server={self.proxy}"]
            if self.proxy
            else ["--no-proxy-server"]
        )

    def environment(self, base: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ if base is None else base)
        env[ROUTE_ENV] = json.dumps(asdict(self))
        if self.proxy:
            # FFmpeg also consults no_proxy, even with an explicit proxy option.
            # Restrict only the worker's bypass list; leave local AI/CDP direct.
            env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"
        return env


def settings_path() -> Path:
    return onboarding_state_directory() / "network-settings.json"


def load_network_settings(path: Path | None = None) -> NetworkSettings:
    try:
        data = load_json(path or settings_path())
    except FileNotFoundError:
        return NetworkSettings()
    except (OSError, ValueError, TypeError):
        raise InputValidationError(
            "网络设置损坏或无法读取。请在“网络与连接”重新保存线路，未自动改走直连。"
        ) from None
    try:
        return NetworkSettings(**data).validated()
    except (TypeError, ValueError):
        raise InputValidationError("网络设置格式无效，请在“网络与连接”重新保存。") from None


def resolve_route(settings: NetworkSettings) -> NetworkRoute:
    settings = settings.validated()
    if settings.mode == "manual":
        return NetworkRoute(settings.mode, settings.proxy)
    if settings.mode == "direct":
        return NetworkRoute(settings.mode)
    # Snapshot ONE fixed proxy rather than letting each client interpret bypass/PAC
    # differently. Windows secure proxy entries still normally use http:// URLs.
    proxies = getproxies()
    proxy = proxies.get("https") or proxies.get("http") or proxies.get("all")
    if proxy:
        return NetworkRoute("system", normalize_proxy(proxy))
    if os.name == "nt":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            ) as key:
                pac = winreg.QueryValueEx(key, "AutoConfigURL")[0]
        except OSError:
            pac = ""
        if pac:
            raise InputValidationError(
                "系统使用 PAC 自动代理，无法保证各下载组件线路一致。请改选“手动代理”并填写本地代理客户端的固定端口。"
            )
    return NetworkRoute("system")


def current_route() -> NetworkRoute:
    route = _context.get()
    if route is not None:
        return route
    global _active
    if _active is None:
        snapshot = os.environ.get(ROUTE_ENV)
        if snapshot:
            try:
                payload = json.loads(snapshot)
                mode = payload["mode"]
                if not isinstance(mode, str) or mode not in {"system", "direct", "manual"}:
                    raise ValueError
                raw_proxy = payload["proxy"]
                if not isinstance(raw_proxy, str):
                    raise ValueError
                proxy = normalize_proxy(raw_proxy) if raw_proxy else ""
                if (mode == "direct" and proxy) or (mode == "manual" and not proxy):
                    raise ValueError
                _active = NetworkRoute(mode, proxy)
            except (ValueError, TypeError, KeyError):
                raise InputValidationError(
                    "任务网络快照无效，已停止连接，未自动切换线路。"
                ) from None
        else:
            _active = resolve_route(load_network_settings())
    return _active


def route_for_sources(sources: list[str]) -> NetworkRoute:
    """A wholly local job must not depend on proxy configuration being readable.

    Project directories bypass media networking only when a downloaded source is
    present. Missing remote assets still require the configured route, fail closed.
    """
    def local(source: str) -> bool:
        if source.lower().startswith(("http://", "https://")):
            return False
        try:
            path = Path(source).expanduser()
            return path.is_file() or (
                path.is_dir() and any(p.is_file() for p in (path / "source").glob("source_video.*"))
            )
        except (OSError, ValueError):
            return False

    if sources and all(local(source) for source in sources):
        return NetworkRoute("direct")
    return current_route()


def save_network_settings(settings: NetworkSettings, path: Path | None = None) -> NetworkRoute:
    settings = settings.validated()
    route = resolve_route(settings)
    atomic_write_json(path or settings_path(), asdict(settings))
    global _active
    _active = route
    return route


@contextmanager
def using_route(route: NetworkRoute):
    token = _context.set(route)
    try:
        yield
    finally:
        _context.reset(token)


@dataclass(frozen=True)
class NetworkFailure:
    code: str
    message: str
    retryable: bool = False


def classify_network_failure(
    error: BaseException | str, *, status: int | None = None
) -> NetworkFailure:
    """Conservative evidence-based diagnosis; never infer geoblocking from 403 alone."""
    chain: list[BaseException] = []
    exc = error if isinstance(error, BaseException) else None
    while exc is not None and exc not in chain:
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    detail = " ".join([str(error), *(str(e) for e in chain)]).casefold()
    if status is None:
        for item in chain:
            if isinstance(item, httpx.HTTPStatusError):
                status = item.response.status_code
                break
        if status is None and (
            match := re.search(
                r"(?:http(?: error)?[: ]+|status(?: code)?[: ]+)([45]\d\d)\b", detail
            )
        ):
            status = int(match[1])
    if status == 407 or "proxy authentication" in detail:
        return NetworkFailure(
            "proxy_auth", "代理需要认证。请使用本地代理客户端的无认证端口，再测试连接。"
        )
    if any(
        s in detail
        for s in ("cf-mitigated", "cloudflare", "captcha", "confirm you're not a bot", "浏览器验证")
    ):
        return NetworkFailure(
            "challenge",
            "网站要求浏览器验证。保持当前线路，在“浏览器抓取”窗口亲自完成验证并播放视频；不会绕过挑战。",
        )
    if status == 429 or "too many requests" in detail:
        return NetworkFailure(
            "rate_limit",
            "HTTP 429：当前请求被暂时限制（限流）。已保留下载片段；等待几分钟再继续，减少同时下载任务。不要连续重试或频繁切换 IP。",
            True,
        )
    if any(
        s in detail
        for s in (
            "not available in your country",
            "not available in your region",
            "geo-restricted",
            "geo restricted",
            "georestricted",
        )
    ):
        return NetworkFailure(
            "region",
            "平台明确提示地区限制。可在“网络与连接”选择你已有且允许访问的出口，随后重新解析；软件不提供出口 IP，也不绕过账号、付费或 DRM 权限。",
        )
    if status == 401 or any(
        s in detail for s in ("login required", "sign in to", "cookies are no longer valid")
    ):
        return NetworkFailure(
            "login",
            "需要登录或会话已失效。B站/抖音请在同一线路下刷新“平台账号”；其他网站请使用允许访问的公开资源。",
        )
    if status == 403:
        return NetworkFailure(
            "forbidden",
            "HTTP 403：服务器拒绝访问，不能仅凭此确定是 IP 问题。可能是签名直链过期、登录/权限、Referer 或出口不一致；保持同一线路重新复制或抓取完整直链，必要时刷新平台登录。",
        )
    if status in {404, 410}:
        return NetworkFailure(
            "expired", "链接不存在或已失效。请从原页面重新复制完整链接，再重新解析。"
        )
    if any(isinstance(e, socket.gaierror) for e in chain) or any(
        s in detail for s in ("getaddrinfo", "name resolution", "nodename nor servname", "dns")
    ):
        return NetworkFailure(
            "dns",
            "DNS 解析失败。检查网络与代理客户端；通用网页的公网地址安全校验仍需要本地 DNS，不能用代理绕过该校验。",
        )
    if (
        any(isinstance(e, httpx.ProxyError) for e in chain)
        or "proxyerror" in detail
        or "proxy connection" in detail
    ):
        return NetworkFailure(
            "proxy",
            "代理连接失败。确认代理客户端已启动、协议与端口正确，再点击“测试连接”；不会静默改走直连。",
        )
    if any(isinstance(e, (httpx.TimeoutException, TimeoutError)) for e in chain) or any(
        s in detail for s in ("timed out", "timeout", "超时")
    ):
        return NetworkFailure(
            "timeout",
            "连接超时。检查选定线路和代理端口，测试网站连接后再继续；超时本身不能证明 IP 被封。",
            True,
        )
    if any(s in detail for s in ("certificate_verify_failed", "certificate verify failed", "ssl")):
        return NetworkFailure(
            "tls",
            "TLS 连接或证书校验失败。检查系统时间、网络拦截与代理配置；程序不会关闭证书校验。",
        )
    if status in {500, 502, 503, 504}:
        return NetworkFailure("server", "网站服务暂时异常，已保留下载片段，请稍后继续。", True)
    if any(isinstance(e, (httpx.ConnectError, ConnectionError)) for e in chain) or any(
        s in detail for s in ("connection refused", "connection reset", "无法连接", "连接被拒绝")
    ):
        return NetworkFailure(
            "connection",
            "连接被拒绝或中断。检查网络和代理是否运行；这不一定是目标网站的 IP 限制。",
            True,
        )
    return NetworkFailure(
        "unknown",
        "暂时无法确定原因。请先测试当前线路；网络正常时再检查链接、平台账号与视频权限，必要时导出诊断包。",
    )


def test_connection(url: str, route: NetworkRoute) -> str:
    """Bounded header-only probe, not a download/readiness or external-IP test."""
    try:
        parsed = urlsplit(url.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError
    except ValueError:
        raise InputValidationError("测试地址须为不带账号密码的 HTTP(S) 地址。") from None
    started = time.monotonic()
    try:
        with (
            httpx.Client(**route.httpx_options(), timeout=8, follow_redirects=False) as client,
            client.stream("GET", url) as response,
        ):
            status = response.status_code
            if status >= 400:
                failure = classify_network_failure(
                    "cf-mitigated" if response.headers.get("cf-mitigated") else "", status=status
                )
                return f"{route.label}\n{parsed.hostname}：HTTP {status}\n{failure.message}"
            return (
                f"{route.label}\n{parsed.hostname}：HTTP {status}，耗时 {time.monotonic() - started:.1f} 秒。\n"
                "网站已响应；重定向、登录、具体视频权限及下载速度仍需实际解析确认。本测试不查询或暴露你的公网 IP。"
            )
    except httpx.HTTPError as exc:
        return f"{route.label}\n{classify_network_failure(exc).message}"
