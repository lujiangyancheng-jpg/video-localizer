"""Local first-run state and public release links for the desktop application."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import __version__
from .utils.files import atomic_write_json, load_json

REPOSITORY_OWNER = "lujiangyancheng-jpg"
REPOSITORY_NAME = "video-localizer"
REPOSITORY_URL = f"https://github.com/{REPOSITORY_OWNER}/{REPOSITORY_NAME}"
REPOSITORY_API_URL = f"https://api.github.com/repos/{REPOSITORY_OWNER}/{REPOSITORY_NAME}"


@dataclass(frozen=True)
class SetupReadinessItem:
    title: str
    status: Literal["ready", "action", "optional"]
    detail: str


def release_page_url(version: str = __version__) -> str:
    return f"{REPOSITORY_URL}/releases/tag/v{version}"


def model_compatibility_version(version: str = __version__) -> str:
    """Return the three-part model ABI shared by four-part application iterations."""
    parts = version.split(".")
    return ".".join(parts[:3]) if len(parts) >= 3 else version


def model_release_page_url(version: str = __version__) -> str:
    return release_page_url(model_compatibility_version(version))


def user_guide_url() -> str:
    return f"{REPOSITORY_URL}/blob/main/docs/USER_GUIDE.zh-CN.md"


def release_asset_url(asset_name: str, version: str = __version__) -> str:
    return f"{REPOSITORY_URL}/releases/download/v{version}/{asset_name}"


def whisper_model_asset_names(model: str, version: str = __version__) -> tuple[str, str]:
    normalized = model.strip().title()
    if normalized not in {"Small", "Medium"}:
        raise ValueError("Whisper model must be Small or Medium.")
    compatible_version = model_compatibility_version(version)
    stem = f"YouTube-Chinese-Localizer-{compatible_version}-Whisper-{normalized}-Model-Setup"
    return f"{stem}.exe", f"{stem}-1.bin"


def onboarding_state_directory(environment: Mapping[str, str] | None = None) -> Path:
    environment = os.environ if environment is None else environment
    local_app_data = environment.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "YouTube Chinese Localizer"
    return Path.home() / ".youtube-chinese-localizer"


def onboarding_state_path(environment: Mapping[str, str] | None = None) -> Path:
    return onboarding_state_directory(environment) / "onboarding.json"


def onboarding_completed(
    *, environment: Mapping[str, str] | None = None, state_path: Path | None = None
) -> bool:
    path = state_path or onboarding_state_path(environment)
    try:
        data = load_json(path)
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    # v0.7.0.2 no longer reopens help after every application iteration.  Accept the
    # previous per-version marker so existing users are migrated without another popup.
    return data.get("completed") is True or isinstance(data.get("completed_version"), str)


def mark_onboarding_completed(
    *, environment: Mapping[str, str] | None = None, state_path: Path | None = None
) -> Path:
    path = state_path or onboarding_state_path(environment)
    atomic_write_json(path, {"completed": True, "completed_version": __version__})
    return path


def setup_status_message(package: str | None, whisper_models: tuple[str, ...]) -> str:
    """Produce a user-facing installation summary without exposing implementation details."""
    if package is None:
        return "当前为源码运行环境；可直接继续设置和处理。"
    package_name = "Complete" if package == "complete" else "Standard"
    if whisper_models:
        return f"{package_name} 基础包已安装；Whisper {' / '.join(item.title() for item in whisper_models)} 已就绪。"
    return f"{package_name} 基础包已安装；生成字幕前还需安装 Whisper Small 或 Medium 模型包。"


def setup_readiness_items(
    package: str | None,
    whisper_models: tuple[str, ...],
    *,
    local_ai_ready: bool,
    resource_mode: str,
    output_advice: str,
) -> tuple[SetupReadinessItem, ...]:
    """Build the small, user-facing status list shown in the persistent help center."""
    package_name = {"standard": "Standard", "complete": "Complete"}.get(package or "", "源码")
    whisper_detail = (
        f"Whisper {' / '.join(item.title() for item in whisper_models)} 已安装，可离线识别字幕。"
        if whisper_models
        else "尚未安装 Whisper；只下载视频仍可使用，制作字幕前需要安装 Small 或 Medium。"
    )
    performance_detail = (
        "高余量模式：AI 计算与视频编码可安全重叠。"
        if resource_mode == "split"
        else "安全模式：重负载步骤会自动排队，避免电脑卡死。"
    )
    storage_status: Literal["ready", "action", "optional"] = (
        "action" if "建议" in output_advice or "仅" in output_advice else "ready"
    )
    return (
        SetupReadinessItem("应用基础包", "ready", f"{package_name} 运行环境已就绪。"),
        SetupReadinessItem(
            "语音识别模型",
            "ready" if whisper_models else "action",
            whisper_detail,
        ),
        SetupReadinessItem(
            "翻译方式",
            "ready" if local_ai_ready else "optional",
            (
                "本地 AI 段落翻译已就绪，也可以使用快速离线翻译。"
                if local_ai_ready
                else "快速离线翻译已就绪；本地 AI 段落翻译是可选模型。"
            ),
        ),
        SetupReadinessItem("性能调度", "ready", performance_detail),
        SetupReadinessItem("输出存储", storage_status, output_advice),
    )


def quick_readiness_message(
    whisper_models: tuple[str, ...], *, local_ai_ready: bool, resource_mode: str
) -> str:
    whisper = (
        f"Whisper {' / '.join(item.title() for item in whisper_models)}"
        if whisper_models
        else "未安装 Whisper"
    )
    translator = "本地 AI" if local_ai_ready else "本地快速翻译"
    scheduler = "高性能调度" if resource_mode == "split" else "安全调度"
    return f"本机状态：{whisper} · {translator} · {scheduler}"
