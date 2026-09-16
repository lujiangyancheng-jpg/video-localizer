"""Capture an explicitly played media URL from an isolated Microsoft Edge session.

This helper launches Edge with a fresh temporary profile and listens to the browser's local
DevTools endpoint.  It never attaches to the user's normal browser profile and deliberately
doesn't retain cookies, authorization headers, response bodies, or DRM data.  A user may complete
an ordinary browser challenge in the visible window; the application only receives the final
public HTTP(S) media URL that the page actually plays.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from ..errors import InputValidationError, LocalizerError
from .direct import is_direct_media_candidate_url
from .webpage import MAX_URL_CHARACTERS, _assert_public_http_url, is_webpage_url

BROWSER_CAPTURE_TIMEOUT_SECONDS = 180
BROWSER_START_TIMEOUT_SECONDS = 20
NETWORK_CANDIDATE_GRACE_SECONDS = 2.0
TARGET_REFRESH_SECONDS = 0.5
MEDIA_MIME_TYPES = frozenset(
    {
        "application/dash+xml",
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
        "audio/mpegurl",
    }
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BrowserMediaCandidate:
    """A media URL observed through a bounded, header-free CDP event."""

    url: str
    source: str
    mime_type: str = ""
    content_length: int = 0
    title: str = ""

    @property
    def priority(self) -> int:
        return {"player": 0, "response": 1, "request": 2}.get(self.source, 9)


@dataclass(frozen=True, slots=True)
class BrowserMediaCapture:
    page_url: str
    media_url: str
    source: str
    mime_type: str = ""
    page_title: str = ""


def find_edge_executable(candidates: Iterable[Path] = ()) -> Path | None:
    """Return Microsoft Edge without consulting or attaching to a browser profile."""
    paths = [Path(item) for item in candidates]
    program_files_x86 = os.getenv("PROGRAMFILES(X86)")
    program_files = os.getenv("PROGRAMFILES")
    local_app_data = os.getenv("LOCALAPPDATA")
    if program_files_x86:
        paths.append(Path(program_files_x86) / "Microsoft/Edge/Application/msedge.exe")
    if program_files:
        paths.append(Path(program_files) / "Microsoft/Edge/Application/msedge.exe")
    if local_app_data:
        paths.append(Path(local_app_data) / "Microsoft/Edge/Application/msedge.exe")
    for path in paths:
        if path.is_file():
            return path.resolve()
    return None


def validate_browser_capture_page(value: str) -> str:
    """Validate a user-supplied public page before launching a browser."""
    page_url = value.strip()
    if not is_webpage_url(page_url):
        raise InputValidationError("浏览器抓取需要一个完整的公开 HTTP(S) 播放页地址。")
    _assert_public_http_url(page_url)
    return page_url


def _media_mime_type(value: str) -> bool:
    normalized = value.partition(";")[0].strip().casefold()
    return normalized.startswith(("video/", "audio/")) or normalized in MEDIA_MIME_TYPES


def _safe_candidate_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or len(url) > MAX_URL_CHARACTERS or not is_direct_media_candidate_url(url):
        return None
    return url


def candidates_from_cdp_message(message: dict[str, Any]) -> tuple[BrowserMediaCandidate, ...]:
    """Extract media URLs without copying request headers, cookies, or response bodies."""
    method = message.get("method")
    params = message.get("params")
    if not isinstance(params, dict):
        return ()
    if method == "Network.responseReceived":
        response = params.get("response")
        if not isinstance(response, dict):
            return ()
        mime_type = str(response.get("mimeType") or "")
        resource_type = str(params.get("type") or "")
        if resource_type.casefold() != "media" and not _media_mime_type(mime_type):
            return ()
        url = _safe_candidate_url(response.get("url"))
        if not url:
            return ()
        headers = response.get("headers")
        content_length = 0
        if isinstance(headers, dict):
            raw_length = next(
                (
                    value
                    for key, value in headers.items()
                    if str(key).casefold() == "content-length"
                ),
                0,
            )
            try:
                content_length = max(0, int(str(raw_length)))
            except ValueError:
                content_length = 0
        return (BrowserMediaCandidate(url, "response", mime_type, content_length),)
    if method == "Network.requestWillBeSent":
        if str(params.get("type") or "").casefold() != "media":
            return ()
        request = params.get("request")
        if not isinstance(request, dict):
            return ()
        url = _safe_candidate_url(request.get("url"))
        return (BrowserMediaCandidate(url, "request"),) if url else ()
    return ()


def _player_candidates(message: dict[str, Any]) -> tuple[BrowserMediaCandidate, ...]:
    if "id" not in message:
        return ()
    result = message.get("result")
    if not isinstance(result, dict):
        return ()
    remote = result.get("result")
    if not isinstance(remote, dict):
        return ()
    value = remote.get("value")
    if not isinstance(value, dict):
        return ()
    title = str(value.get("title") or "")[:500]
    media = value.get("media")
    if not isinstance(media, list):
        return ()
    candidates: list[BrowserMediaCandidate] = []
    for raw_url in media:
        url = _safe_candidate_url(raw_url)
        if url:
            candidates.append(BrowserMediaCandidate(url, "player", title=title))
    return tuple(candidates)


def _send_command(
    connection: Any, command_id: int, method: str, params: dict[str, Any] | None = None
) -> None:
    payload: dict[str, Any] = {"id": command_id, "method": method}
    if params:
        payload["params"] = params
    connection.send(json.dumps(payload, separators=(",", ":")))


def _monitor_target(
    websocket_url: str,
    origin: str,
    output: queue.Queue[BrowserMediaCandidate],
    stop_event: threading.Event,
) -> None:
    expression = (
        "(() => ({title: document.title || '', media: Array.from("
        "document.querySelectorAll('video,audio')).flatMap(element => "
        "[element.currentSrc, element.src]).filter(Boolean)}))()"
    )
    command_id = 1
    try:
        with connect(
            websocket_url,
            origin=origin,
            proxy=None,
            open_timeout=5,
            close_timeout=2,
            max_size=2 * 1024 * 1024,
        ) as connection:
            _send_command(connection, command_id, "Network.enable")
            command_id += 1
            _send_command(connection, command_id, "Runtime.enable")
            command_id += 1
            next_player_check = 0.0
            while not stop_event.is_set():
                now = time.monotonic()
                if now >= next_player_check:
                    _send_command(
                        connection,
                        command_id,
                        "Runtime.evaluate",
                        {"expression": expression, "returnByValue": True, "awaitPromise": False},
                    )
                    command_id += 1
                    next_player_check = now + 0.75
                try:
                    raw_message = connection.recv(timeout=0.25)
                except TimeoutError:
                    continue
                if not isinstance(raw_message, str):
                    continue
                try:
                    message = json.loads(raw_message)
                except (TypeError, ValueError):
                    continue
                if not isinstance(message, dict):
                    continue
                for candidate in (
                    *candidates_from_cdp_message(message),
                    *_player_candidates(message),
                ):
                    output.put(candidate)
    except (ConnectionClosed, OSError, TimeoutError, ValueError):
        logger.debug("Browser capture target monitor ended", exc_info=True)
        return


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_devtools(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + BROWSER_START_TIMEOUT_SECONDS
    exited_at: float | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=1)
            response.raise_for_status()
            if isinstance(response.json(), dict):
                return
        except (httpx.HTTPError, TypeError, ValueError):
            if process.poll() is not None:
                exited_at = exited_at or time.monotonic()
                if time.monotonic() - exited_at >= 2:
                    raise LocalizerError("Microsoft Edge 在媒体抓取窗口准备完成前退出。") from None
            time.sleep(0.1)
            continue
    raise LocalizerError("Microsoft Edge 抓取窗口启动超时。请确认 Edge 可以正常打开。")


def _target_websockets(port: int) -> dict[str, str]:
    response = httpx.get(f"http://127.0.0.1:{port}/json/list", timeout=2)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        return {}
    targets: dict[str, str] = {}
    for item in payload:
        if not isinstance(item, dict) or str(item.get("type")) not in {"page", "iframe"}:
            continue
        target_id = str(item.get("id") or "")
        websocket_url = str(item.get("webSocketDebuggerUrl") or "")
        if target_id and websocket_url.startswith("ws://"):
            targets[target_id] = websocket_url
    return targets


def _close_browser(port: int, process: subprocess.Popen[bytes]) -> None:
    try:
        payload = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=1).json()
        websocket_url = str(payload.get("webSocketDebuggerUrl") or "")
        if websocket_url.startswith("ws://"):
            with connect(
                websocket_url,
                origin=f"http://127.0.0.1:{port}",
                proxy=None,
                open_timeout=2,
                close_timeout=1,
            ) as connection:
                _send_command(connection, 1, "Browser.close")
    except (ConnectionClosed, httpx.HTTPError, OSError, TimeoutError, TypeError, ValueError):
        pass
    shutdown_deadline = time.monotonic() + 5
    while time.monotonic() < shutdown_deadline:
        try:
            httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=0.5).raise_for_status()
        except httpx.HTTPError:
            break
        time.sleep(0.1)
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def capture_browser_media(
    page_url: str,
    *,
    timeout_seconds: float = BROWSER_CAPTURE_TIMEOUT_SECONDS,
    cancel_event: threading.Event | None = None,
    progress: Callable[[str], None] | None = None,
    edge_executable: Path | None = None,
) -> BrowserMediaCapture:
    """Open *page_url* visibly and return the media URL actually selected by its player."""
    page_url = validate_browser_capture_page(page_url)
    edge = edge_executable or find_edge_executable()
    if edge is None or not edge.is_file():
        raise LocalizerError("没有找到 Microsoft Edge，无法启动浏览器辅助抓取。")
    cancel = cancel_event or threading.Event()
    report = progress or (lambda _message: None)
    if timeout_seconds <= 0:
        raise ValueError("Browser capture timeout must be positive.")

    with tempfile.TemporaryDirectory(
        prefix="LocalizeStudio-BrowserCapture-", ignore_cleanup_errors=True
    ) as profile_name:
        profile_directory = Path(profile_name)
        port = _reserve_loopback_port()
        command = [
            str(edge),
            f"--user-data-dir={profile_directory}",
            f"--remote-debugging-port={port}",
            f"--remote-allow-origins=http://127.0.0.1:{port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--disable-background-mode",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-features=msEdgeFirstRunExperience",
            "--new-window",
            page_url,
        ]
        report("正在启动独立的 Edge 抓取窗口……")
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        monitor_stop = threading.Event()
        monitors: dict[str, threading.Thread] = {}
        candidates: queue.Queue[BrowserMediaCandidate] = queue.Queue()
        try:
            _wait_for_devtools(port, process)
            report("请在 Edge 中让目标视频开始播放；需要验证时请亲自完成。")
            deadline = time.monotonic() + timeout_seconds
            first_network_candidate_at: float | None = None
            observed: dict[str, BrowserMediaCandidate] = {}
            while time.monotonic() < deadline:
                if cancel.is_set():
                    raise InputValidationError("浏览器媒体抓取已取消。")
                try:
                    targets = _target_websockets(port)
                except (httpx.HTTPError, TypeError, ValueError):
                    targets = {}
                for target_id, websocket_url in targets.items():
                    thread = monitors.get(target_id)
                    if thread is not None and thread.is_alive():
                        continue
                    thread = threading.Thread(
                        target=_monitor_target,
                        args=(
                            websocket_url,
                            f"http://127.0.0.1:{port}",
                            candidates,
                            monitor_stop,
                        ),
                        daemon=True,
                        name=f"localizer-browser-capture-{target_id[:8]}",
                    )
                    monitors[target_id] = thread
                    thread.start()

                while True:
                    try:
                        candidate = candidates.get_nowait()
                    except queue.Empty:
                        break
                    if candidate.url == page_url:
                        continue
                    previous = observed.get(candidate.url)
                    if previous is None or candidate.priority < previous.priority:
                        observed[candidate.url] = candidate
                    if candidate.priority == 0:
                        _assert_public_http_url(candidate.url)
                        report("已从播放器捕获完整媒体地址。")
                        return BrowserMediaCapture(
                            page_url,
                            candidate.url,
                            candidate.source,
                            candidate.mime_type,
                            candidate.title,
                        )
                    if first_network_candidate_at is None:
                        first_network_candidate_at = time.monotonic()

                if (
                    observed
                    and first_network_candidate_at is not None
                    and time.monotonic() - first_network_candidate_at
                    >= NETWORK_CANDIDATE_GRACE_SECONDS
                ):
                    for candidate in sorted(
                        observed.values(),
                        key=lambda item: (item.priority, -item.content_length, item.url),
                    ):
                        try:
                            _assert_public_http_url(candidate.url)
                        except InputValidationError:
                            continue
                        report("已从媒体网络响应捕获完整地址。")
                        return BrowserMediaCapture(
                            page_url,
                            candidate.url,
                            candidate.source,
                            candidate.mime_type,
                            candidate.title,
                        )
                time.sleep(TARGET_REFRESH_SECONDS)
            raise LocalizerError(
                "等待媒体播放超时。请确认视频已经在抓取窗口中开始播放，且页面未使用 DRM。"
            )
        finally:
            monitor_stop.set()
            _close_browser(port, process)
            for thread in monitors.values():
                thread.join(timeout=0.5)
