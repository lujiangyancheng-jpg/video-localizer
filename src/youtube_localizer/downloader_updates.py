"""Optional, verified yt-dlp updates isolated from the application and its pinned runtime."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import httpx
from filelock import FileLock
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from .errors import LocalizerError
from .onboarding import onboarding_state_directory
from .utils.files import atomic_write_json
from .utils.subprocesses import run_command


def _root() -> Path:
    return onboarding_state_directory() / "download-engine"


def activate_downloader_update() -> None:
    root = _root()
    try:
        version = json.loads((root / "active.json").read_text(encoding="utf-8"))["version"]
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
            return
        directory = root / version
        if (directory / "yt_dlp" / "version.py").is_file() and str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    except (OSError, ValueError, KeyError, TypeError):
        return


def rollback_downloader_update() -> None:
    (_root() / "active.json").unlink(missing_ok=True)


def update_downloader() -> str:
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(str(root / "update.lock"), timeout=1):
        response = httpx.get("https://pypi.org/pypi/yt-dlp/json", timeout=20)
        response.raise_for_status()
        metadata = response.json()
        version = str(metadata["info"]["version"])
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
            raise LocalizerError("下载组件版本格式不受支持。")
        if not SpecifierSet(metadata["info"].get("requires_python") or "").contains(
            ".".join(map(str, sys.version_info[:3]))
        ):
            raise LocalizerError("最新下载引擎需要较新的 Python，请更新整个应用。")
        asset = next(
            (
                item
                for item in metadata["urls"]
                if item.get("filename", "").endswith("-py3-none-any.whl")
            ),
            None,
        )
        if not asset or not str(asset["url"]).startswith("https://files.pythonhosted.org/"):
            raise LocalizerError("未找到官方通用下载组件。")
        with httpx.stream("GET", asset["url"], timeout=60) as download:
            download.raise_for_status()
            payload = bytearray()
            for chunk in download.iter_bytes(1024 * 1024):
                payload.extend(chunk)
                if len(payload) > 32 * 1024**2:
                    raise LocalizerError("下载组件大小异常。")
        if hashlib.sha256(payload).hexdigest() != asset["digests"]["sha256"]:
            raise LocalizerError("下载组件 SHA-256 校验失败。")
        directory = root / version
        with tempfile.TemporaryDirectory(prefix="stage-", dir=root) as staging:
            stage = Path(staging)
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                total = 0
                for item in archive.infolist():
                    path = PurePosixPath(item.filename)
                    if path.is_absolute() or ".." in path.parts or "\\" in item.filename:
                        raise LocalizerError("下载组件包含无效路径。")
                    if not path.parts or path.parts[0] != "yt_dlp" or item.is_dir():
                        continue
                    total += item.file_size
                    if total > 64 * 1024**2:
                        raise LocalizerError("下载组件解压大小异常。")
                    target = stage.joinpath(*path.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(item))
            result = run_command(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    "import sys; sys.path.insert(0,sys.argv[1]); import yt_dlp, yt_dlp.extractor; from yt_dlp.version import __version__; print(__version__)",
                    str(stage),
                ],
                timeout=60,
            )
            reported = result.stdout.strip()
            if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", reported) or Version(reported) != Version(version):
                raise LocalizerError("下载组件自检失败，继续使用当前组件。")
            if not directory.exists():
                shutil.copytree(stage, directory)
        atomic_write_json(root / "active.json", {"version": version})
    return version
