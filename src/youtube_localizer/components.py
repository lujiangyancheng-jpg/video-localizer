"""Download verified, ABI-compatible model installers without replacing user models."""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Callable
from pathlib import Path

import httpx
from filelock import FileLock

from .errors import LocalizerError
from .onboarding import (
    REPOSITORY_API_URL,
    REPOSITORY_URL,
    model_compatibility_version,
    onboarding_state_directory,
)
from .utils.subprocesses import run_command


def component_asset_names(key: str) -> tuple[str, ...]:
    prefix = f"YouTube-Chinese-Localizer-{model_compatibility_version()}-"
    if key in {"whisper-small", "whisper-medium"}:
        stem = prefix + f"Whisper-{key.split('-')[1].title()}-Model-Setup"
        return stem + ".exe", stem + "-1.bin"
    if key == "local-ai":
        stem = prefix + "Local-AI-Model-Setup"
        return (stem + ".exe", *(stem + f"-{index}.bin" for index in (1, 2, 3)))
    if key == "super-resolution":
        return (prefix + "AI-Super-Resolution-Setup.exe",)
    raise ValueError("Unknown component.")


def install_component(
    key: str, root: Path, *, progress: Callable[[str], None], cancel: threading.Event
) -> None:
    names = component_asset_names(key)
    cache = onboarding_state_directory() / "component-downloads" / model_compatibility_version()
    cache.mkdir(parents=True, exist_ok=True)
    with FileLock(str(cache / "installation.lock"), timeout=1):
        response = httpx.get(
            f"{REPOSITORY_API_URL}/releases/tags/v{model_compatibility_version()}",
            timeout=20,
            follow_redirects=True,
        )
        response.raise_for_status()
        assets = {item["name"]: item for item in response.json().get("assets", [])}
        for name in names:
            if cancel.is_set():
                raise LocalizerError("已取消组件下载，可稍后重新开始。")
            asset = assets.get(name, {})
            digest = str(asset.get("digest", ""))
            expected_url = (
                f"{REPOSITORY_URL}/releases/download/v{model_compatibility_version()}/{name}"
            )
            if (
                not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)
                or asset.get("browser_download_url") != expected_url
            ):
                raise LocalizerError("发布页缺少可验证的组件或 SHA-256，请通过发布页检查。")
            expected = digest[7:].lower()
            path = cache / name
            if path.is_file():
                from .utils.hashing import hash_file

                if hash_file(path) == expected:
                    continue
            partial = path.with_suffix(path.suffix + ".partial")
            hasher = hashlib.sha256()
            received = 0
            with (
                httpx.stream("GET", expected_url, timeout=60, follow_redirects=True) as download,
                partial.open("wb") as handle,
            ):
                download.raise_for_status()
                for chunk in download.iter_bytes(1024 * 1024):
                    if cancel.is_set():
                        raise LocalizerError("已取消组件下载。")
                    handle.write(chunk)
                    hasher.update(chunk)
                    received += len(chunk)
                    progress(
                        f"正在下载 {name}：{received / 1024**2:.0f} / {int(asset.get('size') or 0) / 1024**2:.0f} MiB"
                    )
            if hasher.hexdigest() != expected:
                partial.unlink(missing_ok=True)
                raise LocalizerError("组件 SHA-256 校验失败，未执行安装。")
            partial.replace(path)
        if cancel.is_set():
            raise LocalizerError("已取消组件安装。")
        progress("下载校验完成，正在安装组件，请稍候…")
        run_command(
            [cache / names[0], "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={root}"],
            timeout=1800,
        )
        progress("组件安装完成。")
