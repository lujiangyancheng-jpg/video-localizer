"""Explicit platform sign-in in an isolated browser; secrets stay outside projects."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import tempfile
import threading
import time
from http.cookiejar import Cookie
from pathlib import Path
from typing import Any

from websockets.sync.client import connect

from ..errors import LocalizerError
from ..onboarding import onboarding_state_directory
from .browser_capture import (
    _close_browser,
    _reserve_loopback_port,
    _target_websockets,
    _wait_for_devtools,
    find_edge_executable,
)

PLATFORMS = {
    "bilibili": ("Bilibili", "https://www.bilibili.com/", ("bilibili.com", "bilibili.tv")),
    "douyin": ("抖音", "https://www.douyin.com/", ("douyin.com",)),
}


def _path(platform: str) -> Path:
    if platform not in PLATFORMS:
        raise ValueError("Unsupported account platform.")
    return onboarding_state_directory() / "accounts" / f"{platform}.dpapi"


def _protect(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise LocalizerError("平台登录数据保护目前仅支持 Windows。")

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        ok = crypt.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)
        )
    else:
        ok = crypt.CryptProtectData(
            ctypes.byref(source),
            "Video Localizer account",
            None,
            None,
            None,
            1,
            ctypes.byref(output),
        )
    if not ok:
        raise LocalizerError("Windows 无法保护或读取平台会话，请重新登录。")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def filter_platform_cookies(platform: str, cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    domains = PLATFORMS[platform][2]
    return [
        {
            key: item[key]
            for key in ("name", "value", "domain", "path", "secure", "expires")
            if key in item
        }
        for item in cookies
        if str(item.get("domain", "")).lstrip(".") in domains
        or any(str(item.get("domain", "")).lstrip(".").endswith("." + domain) for domain in domains)
    ]


def save_account(platform: str, cookies: list[dict[str, Any]]) -> None:
    filtered = filter_platform_cookies(platform, cookies)
    if not filtered:
        raise LocalizerError("没有获取到该平台的会话，请先在打开的官方网站完成登录。")
    if platform == "bilibili" and not any(
        item.get("name") == "SESSDATA" and item.get("value") for item in filtered
    ):
        raise LocalizerError("B站尚未登录，请完成扫码登录后再点击完成。")
    path = _path(platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    protected = _protect(json.dumps(filtered).encode("utf-8"))
    temporary = path.with_suffix(".partial")
    temporary.write_bytes(protected)
    temporary.replace(path)


def load_account(platform: str) -> list[dict[str, Any]]:
    path = _path(platform)
    if not path.is_file():
        return []
    try:
        payload = json.loads(_protect(path.read_bytes(), decrypt=True))
        if not isinstance(payload, list):
            raise ValueError("Invalid account data.")
        return filter_platform_cookies(
            platform, [item for item in payload if isinstance(item, dict)]
        )
    except (OSError, ValueError, LocalizerError) as exc:
        raise LocalizerError("本地平台会话无法读取，请在平台账号页清除后重新登录。") from exc


def account_status(platform: str) -> str:
    try:
        cookies = load_account(platform)
    except LocalizerError:
        return "需重新登录"
    if not cookies:
        return "未连接"
    relevant = (
        [item for item in cookies if item.get("name") == "SESSDATA"]
        if platform == "bilibili"
        else cookies
    )
    if relevant and all(0 < float(item.get("expires", -1)) < time.time() for item in relevant):
        return "会话已过期"
    return "会话已保存（访问时验证）"


def clear_account(platform: str) -> None:
    _path(platform).unlink(missing_ok=True)


def apply_account(ydl: Any, platform: str) -> None:
    for item in load_account(platform):
        expires = float(item.get("expires", -1))
        if 0 < expires < time.time():
            continue
        domain = str(item["domain"])
        ydl.cookiejar.set_cookie(
            Cookie(
                version=0,
                name=str(item["name"]),
                value=str(item["value"]),
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=domain.startswith("."),
                domain_initial_dot=domain.startswith("."),
                path=str(item.get("path") or "/"),
                path_specified=True,
                secure=bool(item.get("secure")),
                expires=int(expires) if expires > 0 else None,
                discard=expires <= 0,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            )
        )


def browser_sign_in(platform: str, finish: threading.Event, cancel: threading.Event) -> None:
    """User signs in on the real website, then explicitly requests saving its session."""
    edge = find_edge_executable()
    if edge is None:
        raise LocalizerError("请安装 Microsoft Edge 后使用平台登录。")
    port = _reserve_loopback_port()
    origin = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(
        prefix="localizer-sign-in-", ignore_cleanup_errors=True
    ) as profile:
        process = subprocess.Popen(
            [
                str(edge),
                f"--user-data-dir={profile}",
                f"--remote-debugging-port={port}",
                "--remote-debugging-address=127.0.0.1",
                f"--remote-allow-origins={origin}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-sync",
                "--disable-background-mode",
                "--new-window",
                PLATFORMS[platform][1],
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_devtools(port, process)
            deadline = time.monotonic() + 600
            while not finish.wait(0.2):
                if cancel.is_set():
                    return
                if time.monotonic() > deadline:
                    raise LocalizerError("平台登录等待超时，请重新打开登录窗口。")
            if cancel.is_set():
                return
            targets = _target_websockets(port)
            if not targets:
                raise LocalizerError("登录窗口已关闭，请重新登录。")
            with connect(
                next(iter(targets.values())),
                origin=origin,
                proxy=None,
                open_timeout=5,
                close_timeout=2,
                max_size=2 * 1024 * 1024,
            ) as connection:
                connection.send(
                    json.dumps(
                        {
                            "id": 1,
                            "method": "Network.getCookies",
                            "params": {
                                "urls": [PLATFORMS[platform][1], "https://api.bilibili.com/"]
                                if platform == "bilibili"
                                else [PLATFORMS[platform][1]]
                            },
                        }
                    )
                )
                while True:
                    message = json.loads(connection.recv(timeout=10))
                    if message.get("id") == 1:
                        save_account(platform, message.get("result", {}).get("cookies", []))
                        return
        finally:
            _close_browser(port, process)
