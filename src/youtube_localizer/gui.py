from __future__ import annotations

import ctypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import suppress
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from .config import (
    AppConfig,
    RightsConfig,
    default_output_directory,
    output_directory_advice,
    requires_local_ai_or_api,
)
from .desktop_queue import (
    DesktopQueueSnapshot,
    DesktopTaskSnapshot,
    clear_desktop_queue,
    load_desktop_queue,
    save_desktop_queue,
)
from .desktop_settings import DesktopSettings, load_desktop_settings, save_desktop_settings
from .download.browser_capture import (
    BrowserMediaCapture,
    capture_browser_media,
    validate_browser_capture_page,
)
from .download.direct import assess_direct_media_url, is_direct_media_candidate_url
from .download.webpage import is_webpage_url
from .download.youtube import is_youtube_url
from .errors import LocalizerError
from .hardware import SystemResources, detect_system_resources
from .inspection_cache import save_cached_inspection
from .media_preview import MediaPreview, inspect_media_preview, media_preview_summary
from .models import ProjectPaths
from .onboarding import (
    model_release_page_url,
    quick_readiness_message,
    release_page_url,
    setup_readiness_items,
    setup_status_message,
    user_guide_url,
)
from .publishing.rights import RIGHTS_BASIS_LABELS, validate_rights
from .resource_gate import detected_resource_schedule
from .resources import (
    application_icon_path,
    installed_whisper_models,
    ollama_executable,
    package_tier,
    super_resolution_runtime,
)
from .review import (
    SubtitleReviewSession,
    load_subtitle_review_session,
    render_subtitle_review_preview,
    save_reviewed_subtitles,
)
from .state import find_recoverable_projects
from .support import create_support_bundle
from .updates import ReleaseCheck, check_for_update
from .utils.text import ms_to_srt

PROJECT_ROOT = Path(__file__).resolve().parents[2]

APP_BACKGROUND = "#F2F4F7"
SURFACE = "#FFFFFF"
HEADER = "#FCFCFD"
PRIMARY = "#20A864"
PRIMARY_HOVER = "#198A52"
PRIMARY_SOFT = "#EAF7F0"
TEXT = "#171A1F"
MUTED = "#69717D"
BORDER = "#DEE3E8"
SUCCESS = "#20A864"
DANGER = "#C94754"
LOG_BACKGROUND = "#15191F"
LOG_TEXT = "#D7DCE3"
UI_FONT = "Microsoft YaHei UI"

SUBTITLE_MODES = {
    "仅下载原视频（无字幕）": "download_only",
    "仅目标语言字幕": "chinese",
    "英文在上，中文在下": "bilingual_en_zh",
    "中文在上，英文在下": "bilingual_zh_en",
}
TRANSLATION_DIRECTIONS = {
    "英文 → 简体中文": "en-to-zh",
    "简体中文 → 英文": "zh-to-en",
    "英文 → 日语": "en-to-ja",
    "英文 → 韩语": "en-to-ko",
    "英文 → 西班牙语": "en-to-es",
    "英文 → 法语": "en-to-fr",
    "英文 → 德语": "en-to-de",
    "英文 → 葡萄牙语": "en-to-pt",
    "英文 → 俄语": "en-to-ru",
    "英文 → 阿拉伯语": "en-to-ar",
    "简体中文 → 日语": "zh-to-ja",
    "简体中文 → 韩语": "zh-to-ko",
    "简体中文 → 西班牙语": "zh-to-es",
    "简体中文 → 法语": "zh-to-fr",
    "简体中文 → 德语": "zh-to-de",
    "简体中文 → 葡萄牙语": "zh-to-pt",
    "简体中文 → 俄语": "zh-to-ru",
    "简体中文 → 阿拉伯语": "zh-to-ar",
}
TRANSLATION_MODES = {
    "免费模式（处理到人工翻译）": "manual",
    "本地快速翻译并压制（无需 API）": "offline",
    "本地 AI 段落翻译并压制（高质量，无 API Key）": "ollama",
    "自动翻译并压制字幕（需要 API）": "openai-compatible",
}
UPDATE_CHANNELS = {"稳定": "stable", "开发": "development"}
SUPER_RESOLUTION_MODES = {
    "关闭（保持原始像素）": "off",
    "AI 通用画面增强（NCNN/Vulkan）": "general",
    "AI 动画画面增强（NCNN/Vulkan）": "animation",
}
RIGHTS_BASES = {
    "本人／本团队原创": "owned",
    "已获权利人书面授权": "written_permission",
    "CC BY": "cc_by",
    "CC BY-SA": "cc_by_sa",
    "CC BY-NC（非商业）": "cc_by_nc",
    "公共领域": "public_domain",
    "其他许可／版权例外": "other",
}
TASK_STATE_LABELS = {
    "pending": "待分析",
    "analyzing": "分析中",
    "ready": "可开始",
    "queued": "排队中",
    "running": "处理中",
    "paused": "已暂停，可继续",
    "failed": "未完成",
    "completed": "已完成",
}


def label_for_value(mapping: Mapping[str, object], value: object, fallback: str) -> str:
    """Return the stable display label for a stored internal setting value."""
    return next((label for label, candidate in mapping.items() if candidate == value), fallback)


def output_directory_status_hint(path: Path) -> str:
    advice = output_directory_advice(path)
    recoverable = find_recoverable_projects(path)
    if not recoverable:
        return advice
    count = len(recoverable)
    return (
        f"{advice} 发现 {count} 个未完成项目；重新粘贴原链接并保留“继续上次处理”即可恢复。"
    )
LANGUAGE_DISPLAY_NAMES = {
    "zh": "简体中文",
    "en": "英文",
    "ja": "日语",
    "ko": "韩语",
    "es": "西班牙语",
    "fr": "法语",
    "de": "德语",
    "pt": "葡萄牙语",
    "ru": "俄语",
    "ar": "阿拉伯语",
}
DEFAULT_SUBTITLE_FONT = "Noto Sans CJK SC"
SUBTITLE_FONTS = {"Noto Sans CJK SC（默认）": DEFAULT_SUBTITLE_FONT}
SUBTITLE_FONT_SIZES = {
    "小号（40）": 40,
    "标准（48，推荐）": 48,
    "大号（56）": 56,
    "超大（64）": 64,
}
SUBTITLE_POSITION_MIN = 2
SUBTITLE_POSITION_MAX = 98
SUBTITLE_PREVIEW_WIDTH = 16
SUBTITLE_PREVIEW_HEIGHT = 9


PROCESSING_PROFILES = frozenset({"auto", "fast", "balanced", "quality", "safe_cpu"})

OUTPUT_QUALITIES = {
    "最高质量（推荐）": "best",
    "高质量（文件更小）": "high",
    "标准质量（节省空间）": "standard",
}
OUTPUT_FRAME_RATES = {
    "保持原视频帧率（推荐）": None,
    "60 FPS": 60,
    "30 FPS": 30,
}
OUTPUT_HEIGHTS = {
    "保持原视频分辨率（推荐）": None,
    "4K / 2160p": 2160,
    "2K / 1440p": 1440,
    "1080p": 1080,
    "720p": 720,
}

_DOWNLOAD_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
_DOWNLOAD_SPEED_RE = re.compile(r"\bat\s+([^\s]+/s)")
_DOWNLOAD_ETA_RE = re.compile(r"\bETA\s+([0-9:]+)")
_LOCAL_AI_PROGRESS_RE = re.compile(r"Local AI translating paragraph\s+(\d+)/(\d+)")
_OFFLINE_PROGRESS_RE = re.compile(r"Offline translating contextual subtitle batch\s+(\d+)/(\d+)")
_RENDER_PROGRESS_RE = re.compile(r"Rendering subtitles:\s+(\d+(?:\.\d+)?)%")
_PROJECT_WORKSPACE_PREFIX = "Project workspace: "


def queue_input_values(value: str) -> list[str]:
    """Return one input per non-empty line while retaining local paths with spaces."""
    values = [line.strip() for line in value.splitlines() if line.strip()]
    if not values and value.strip():
        values = [value.strip()]
    return list(dict.fromkeys(values))


def task_tree_visible_rows(task_count: int) -> int:
    """Keep short queues compact while allowing larger batches to remain scannable."""
    return max(2, min(5, task_count))


def validate_direct_media_links(value: str) -> list[str]:
    """Validate direct-media dialog input without making a network request."""
    links = queue_input_values(value)
    if not links:
        raise ValueError("请粘贴完整的 MP4、M3U8、MPD 或 CDN 媒体直链。")
    for index, link in enumerate(links, start=1):
        if not is_direct_media_candidate_url(link):
            raise ValueError(
                f"第 {index} 行不是可识别的媒体直链。如果这是播放页地址，请使用主界面的“粘贴链接”。"
            )
        try:
            assessment = assess_direct_media_url(link)
        except LocalizerError as exc:
            raise ValueError(f"第 {index} 行：{exc}") from exc
        if assessment.expired:
            expiry = assessment.expires_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            raise ValueError(f"第 {index} 行的签名直链显示已于 {expiry} 过期，请复制新地址。")
    return links


def validate_browser_capture_input(value: str) -> str:
    """Return the one playback page accepted by the browser-assisted capture dialog."""
    sources = queue_input_values(value)
    if len(sources) != 1:
        raise ValueError("浏览器抓取每次需要一个播放页地址；请先只保留当前要抓取的页面。")
    page_url = sources[0]
    if not is_webpage_url(page_url):
        raise ValueError("请粘贴完整的 HTTP(S) 播放页地址。")
    if is_youtube_url(page_url):
        raise ValueError("YouTube 请直接使用“粘贴链接”，不需要浏览器抓取。")
    return page_url


_BROWSER_CAPTURE_ERROR_MARKERS = (
    "cloudflare",
    "浏览器验证",
    "iframe",
    "第三方播放器",
    "混淆脚本",
    "动态脚本",
)


def browser_capture_required(message: str) -> bool:
    """Return whether a media-inspection failure needs the visible browser flow."""
    normalized = message.casefold()
    return any(marker in normalized for marker in _BROWSER_CAPTURE_ERROR_MARKERS)


def browser_capture_required_source(
    sources: list[str], preview_errors: Mapping[int, str]
) -> str | None:
    """Return the first queued playback page that must be resolved before processing."""
    for index, source in enumerate(sources, start=1):
        if not browser_capture_required(preview_errors.get(index, "")):
            continue
        if is_webpage_url(source) and not is_youtube_url(source):
            return source
    return None


def continued_start_sources_after_capture(
    pending_sources: tuple[str, ...] | None,
    *,
    page_url: str,
    updated_sources: list[str],
) -> tuple[str, ...] | None:
    """Retarget one explicit start request only when its captured page was expected."""
    if pending_sources is None or page_url not in pending_sources:
        return None
    return tuple(updated_sources)


def retain_pending_start_sources(
    pending_sources: tuple[str, ...] | None, current_sources: list[str]
) -> tuple[str, ...] | None:
    """Keep a queued start request only while the user-visible input is unchanged."""
    if pending_sources is None or pending_sources != tuple(current_sources):
        return None
    return pending_sources


def gui_parallel_job_limit(
    input_count: int, resources: SystemResources | None = None
) -> int:
    """Scale lightweight queue concurrency while heavy-work locks protect responsiveness."""
    if input_count < 1:
        return 1
    resources = resources or detect_system_resources()
    cpu_count = resources.logical_cpu_count
    memory_mib = resources.memory_mib
    if cpu_count < 6 or (memory_mib is not None and memory_mib < 12 * 1024):
        limit = 1
    elif cpu_count >= 16 and memory_mib is not None and memory_mib >= 48 * 1024:
        limit = 4
    elif cpu_count >= 8 and memory_mib is not None and memory_mib >= 24 * 1024:
        limit = 3
    else:
        limit = 2
    return min(limit, input_count)


def retry_queue_commands(
    commands: list[list[str]], retry_indices: tuple[int, ...]
) -> list[list[str]]:
    """Return just the unfinished commands, always using safe stage resume.

    Queue indexes are one-based because they are shown that way in the desktop log.  Ignore a
    stale index rather than allowing an old UI event to restart an unrelated command.
    """
    retry_commands: list[list[str]] = []
    for index in dict.fromkeys(retry_indices):
        if not 1 <= index <= len(commands):
            continue
        command = list(commands[index - 1])
        if "--resume" not in command:
            command.append("--resume")
        retry_commands.append(command)
    return retry_commands


def project_workspace_from_output(line: str, output_directory: str | Path) -> Path | None:
    """Read the pipeline workspace line without accepting paths outside the chosen output root."""
    text = line.strip()
    if _PROJECT_WORKSPACE_PREFIX not in text:
        return None
    raw_path = text.split(_PROJECT_WORKSPACE_PREFIX, maxsplit=1)[1].strip()
    if not raw_path:
        return None
    try:
        root = Path(output_directory).expanduser().resolve()
        project = Path(raw_path).expanduser().resolve()
        project.relative_to(root)
    except (OSError, ValueError):
        return None
    return project


def progress_update_from_output(
    line: str, *, provider: str, enhancement: bool = False,
) -> tuple[float, str] | None:
    """Map the pipeline's real progress messages to a single GUI progress percentage."""
    if "Preflight ready:" in line:
        return 2.0, "正在检查硬件、离线模型和磁盘空间…"
    if "Preflight warning:" in line:
        return 2.0, "发现兼容性提示，正在采用安全方案…"
    if download := _DOWNLOAD_PROGRESS_RE.search(line):
        percent = min(100.0, float(download.group(1)))
        details: list[str] = []
        if speed := _DOWNLOAD_SPEED_RE.search(line):
            details.append(speed.group(1))
        if eta := _DOWNLOAD_ETA_RE.search(line):
            details.append(f"剩余 {eta.group(1)}")
        suffix = f" · {' · '.join(details)}" if details else ""
        if provider == "download_only" and not enhancement:
            return percent, f"正在下载原视频：{percent:.1f}%{suffix}"
        return percent * (0.10 if enhancement else 0.22), f"正在下载原视频：{percent:.1f}%{suffix}"

    if "AI super resolution:" in line:
        match = re.search(r"\(([\d.]+)%\)", line)
        percent = min(100.0, float(match.group(1))) if match else 0.0
        details = ""
        if speed := re.search(r"([\d.]+) fps; ETA (\d+)s", line):
            details = f" · {speed.group(1)} 帧/秒 · 预计剩余 {speed.group(2)} 秒"
        if "restored checkpoint" in line:
            details = " · 已恢复完成片段"
        span = 88.0 if provider == "download_only" else 12.0
        return 10.0 + span * percent / 100, f"AI 超分辨率：{percent:.1f}%{details}"

    if "Processing audio with duration" in line:
        return 24.0, "正在识别原语言语音…"
    if "Local AI paragraph translation ready" in line:
        return 47.0, "正在准备本地 AI 段落翻译…"
    if local_ai := _LOCAL_AI_PROGRESS_RE.search(line):
        current, total = (int(value) for value in local_ai.groups())
        if total:
            return 47.0 + 28.0 * (current - 1) / total, f"本地 AI 翻译：{current}/{total} 段"
    if offline := _OFFLINE_PROGRESS_RE.search(line):
        current, total = (int(value) for value in offline.groups())
        if total:
            return 47.0 + 28.0 * (current - 1) / total, f"本地快速翻译：{current}/{total} 批"
    if render := _RENDER_PROGRESS_RE.search(line):
        percent = min(100.0, float(render.group(1)))
        return 77.0 + 22.0 * percent / 100, f"正在压制字幕：{percent:.1f}%"
    return None


def gui_process_creationflags() -> int:
    """Hide the CLI helper console while retaining a stoppable process group on Windows."""
    if os.name == "nt":
        return subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    return 0


def notify_window_attention(root: tk.Misc) -> None:
    """Ring the app bell and flash its Windows taskbar button after a queue attempt."""
    with suppress(tk.TclError):
        root.bell()
    if os.name != "nt":
        return
    try:
        hwnd = int(root.winfo_id())

        class FlashWindowInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("hwnd", ctypes.c_void_p),
                ("dwFlags", ctypes.c_uint),
                ("uCount", ctypes.c_uint),
                ("dwTimeout", ctypes.c_uint),
            ]

        info = FlashWindowInfo(
            ctypes.sizeof(FlashWindowInfo),
            hwnd,
            0x00000003 | 0x0000000C,
            4,
            0,
        )
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except (AttributeError, OSError, TypeError, ValueError):
        return


def local_ai_available() -> bool:
    return ollama_executable() is not None


def whisper_model_installation_message() -> str | None:
    """Explain the required model-pack action before a packaged app starts a subtitle job."""
    if package_tier() is None or installed_whisper_models():
        return None
    return (
        "尚未安装 Whisper 语音识别模型。请安装 Whisper Small（大多数电脑推荐）或 "
        "Whisper Medium（更高质量、需要更多内存/显存）模型包后再开始字幕任务。"
        "可点击主界面的“帮助中心”，在“环境与模型”中打开兼容模型下载页。"
    )


def super_resolution_installation_message(mode: str) -> str | None:
    if mode == "off" or super_resolution_runtime() is not None:
        return None
    return (
        "尚未安装 AI 超分辨率组件。请重新运行 Standard 安装器并勾选 "
        "“AI 超分辨率组件”，或从当前版本的 Releases 安装对应组件包。"
    )


def friendly_failure_summary(log_text: str) -> str:
    """Translate common terminal failures into one short recovery action."""
    normalized = log_text.casefold()
    if "媒体服务器拒绝" in normalized and any(
        marker in normalized for marker in ("401", "403")
    ):
        return (
            "这条媒体直链已过期，或依赖浏览器凭据。"
            "请从内容方播放器重新复制完整的公开地址后继续任务。"
        )
    if "媒体服务器暂时限流" in normalized:
        return "媒体服务器暂时限流。请稍后继续任务，或重新复制最新的完整直链。"
    if "429" in normalized or "too many requests" in normalized:
        return "YouTube 暂时限制了请求。请等待几分钟后点击“重试未完成任务”；已经完成的阶段不会重做。"
    if any(
        marker in normalized
        for marker in ("out of memory", "cuda error", "cublas", "显存不足")
    ):
        return "显存或 CUDA 运行环境不足。请关闭占用显卡的软件后重试；仍失败时改用 Whisper Small。"
    if any(
        marker in normalized
        for marker in ("no space left", "not enough space", "磁盘空间不足", "errno 28")
    ):
        return "输出磁盘空间不足。请至少预留 20 GiB，或在“处理设置”中更换输出位置。"
    if any(marker in normalized for marker in ("private video", "sign in", "video unavailable")):
        return "视频不可公开访问、需要登录或已失效。请确认链接权限，或改用你有权处理的本地文件。"
    if any(
        marker in normalized
        for marker in ("cloudflare", "浏览器验证", "iframe", "混淆脚本")
    ):
        return (
            "该动态播放页需要浏览器环境。请回到主界面点击“浏览器抓取”，"
            "在独立 Edge 窗口中让视频开始播放；程序会自动接收完整媒体地址。"
        )
    if "ffmpeg hard-subtitle rendering failed" in normalized or "字幕压制失败" in normalized:
        return "字幕压制未完成，但视频和字幕中间文件会保留。请点击重试；程序会重新检测可用编码器。"
    return "可先点击“重试未完成任务”。若仍失败，请导出诊断包；诊断包不会包含视频和字幕正文。"


def mode_description(subtitle_mode: str, translation_provider: str) -> str:
    """Return the short explanation shown under the selected workflow."""
    if subtitle_mode == "download_only":
        return "只下载并合并最高画质视频与最高质量音频，不生成字幕。"
    descriptions = {
        "manual": "准备原语言字幕和翻译文件，等待人工导入译文。",
        "offline": "使用轻量本地模型自动翻译，速度优先，无需 API。",
        "ollama": "使用本地 Qwen 理解完整段落后自然翻译，推荐。",
        "openai-compatible": "使用 OpenAI-compatible 接口自动翻译；API Key 仅用于当前任务。",
    }
    return descriptions.get(translation_provider, "选择处理方式后即可开始。")


def language_name(direction: str, *, target: bool) -> str:
    """Return the Chinese display name for either side of a direction identifier."""
    source_code, target_code = direction.split("-to-", maxsplit=1)
    return LANGUAGE_DISPLAY_NAMES[target_code if target else source_code]


def clamp_subtitle_preview_values(
    x_percent: int, y_percent: int, font_size: int
) -> tuple[int, int, int]:
    """Keep interactive preview values valid for the ASS renderer and CLI."""
    return (
        max(SUBTITLE_POSITION_MIN, min(SUBTITLE_POSITION_MAX, round(x_percent))),
        max(SUBTITLE_POSITION_MIN, min(SUBTITLE_POSITION_MAX, round(y_percent))),
        max(12, min(120, round(font_size))),
    )


def build_process_command(
    input_value: str,
    *,
    subtitle_mode: str,
    translation_provider: str,
    translation_direction: str = "en-to-zh",
    subtitle_font: str = DEFAULT_SUBTITLE_FONT,
    subtitle_font_size: int | None = None,
    subtitle_position_x: int | None = None,
    subtitle_position_y: int | None = None,
    processing_profile: str | None = None,
    output_quality: str | None = None,
    output_fps: int | None = None,
    output_height: int | None = None,
    super_resolution: str = "off",
    rights_basis: str = "unspecified",
    rights_holder: str = "",
    license_url: str = "",
    permission_reference: str = "",
    attribution_text: str = "",
    commercial_use: bool = False,
    output_directory: str | Path | None = None,
    resume: bool = True,
    python_executable: str | None = None,
    main_script: Path | None = None,
) -> list[str]:
    """Build the argument-array command used by the desktop launcher."""
    value = input_value.strip()
    if not value:
        raise ValueError("请粘贴 YouTube、公开播放页或媒体直链，或选择本地视频。")
    if subtitle_mode not in SUBTITLE_MODES.values():
        raise ValueError("未知的字幕模式。")
    if translation_provider not in TRANSLATION_MODES.values():
        raise ValueError("未知的翻译模式。")
    if translation_direction not in TRANSLATION_DIRECTIONS.values():
        raise ValueError("未知的翻译方向。")
    if requires_local_ai_or_api(translation_direction) and translation_provider not in {
        "ollama",
        "openai-compatible",
    }:
        raise ValueError("其他语种仅支持本地 AI 翻译或 API 自动翻译。")
    if (
        requires_local_ai_or_api(translation_direction)
        and subtitle_mode in {"bilingual_en_zh", "bilingual_zh_en"}
    ):
        raise ValueError("其他语种仅支持“仅目标语言字幕”，不能使用中英双语排版。")
    if subtitle_font not in SUBTITLE_FONTS.values():
        raise ValueError("未知的字幕字体。")
    if subtitle_font_size is not None and not 12 <= subtitle_font_size <= 120:
        raise ValueError("字幕字号必须在 12 到 120 之间。")
    for position_value, label in (
        (subtitle_position_x, "水平"),
        (subtitle_position_y, "垂直"),
    ):
        if (
            position_value is not None
            and not SUBTITLE_POSITION_MIN <= position_value <= SUBTITLE_POSITION_MAX
        ):
            raise ValueError(f"字幕{label}位置必须在 2% 到 98% 之间。")
    if processing_profile is not None and processing_profile not in PROCESSING_PROFILES:
        raise ValueError("Unknown processing profile.")
    if output_quality is not None and output_quality not in OUTPUT_QUALITIES.values():
        raise ValueError("Unknown output quality.")
    if output_fps is not None and not 1 <= output_fps <= 240:
        raise ValueError("Output FPS must be between 1 and 240.")
    if output_height is not None and not 144 <= output_height <= 4320:
        raise ValueError("Output height must be between 144 and 4320 pixels.")
    if super_resolution not in SUPER_RESOLUTION_MODES.values():
        raise ValueError("未知的 AI 画质增强模式。")
    if rights_basis not in {"unspecified", *RIGHTS_BASES.values()}:
        raise ValueError("未知的版权依据。")
    output_path: Path | None = None
    if output_directory is not None:
        raw_output_directory = str(output_directory).strip()
        if not raw_output_directory:
            raise ValueError("请选择项目输出文件夹。")
        output_path = Path(raw_output_directory).expanduser()
    command = [
        python_executable or sys.executable,
        str(main_script or PROJECT_ROOT / "main.py"),
        "process",
        value,
        "--subtitle-mode",
        subtitle_mode,
        "--translation-provider",
        translation_provider,
        "--translation-direction",
        translation_direction,
        "--subtitle-font",
        subtitle_font,
    ]
    if subtitle_font_size is not None:
        command.extend(["--subtitle-font-size", str(subtitle_font_size)])
    if subtitle_position_x is not None:
        command.extend(["--subtitle-position-x", str(subtitle_position_x)])
    if subtitle_position_y is not None:
        command.extend(["--subtitle-position-y", str(subtitle_position_y)])
    if output_path is not None:
        command.extend(["--output-dir", str(output_path)])
    if processing_profile:
        command.extend(["--processing-profile", processing_profile])
    if output_quality:
        command.extend(["--output-quality", output_quality])
    if output_fps:
        command.extend(["--output-fps", str(output_fps)])
    if output_height:
        command.extend(["--output-height", str(output_height)])
    command.extend(["--super-resolution", super_resolution])
    command.extend(["--rights-basis", rights_basis])
    if rights_holder.strip():
        command.extend(["--rights-holder", rights_holder.strip()])
    if license_url.strip():
        command.extend(["--license-url", license_url.strip()])
    if permission_reference.strip():
        command.extend(["--permission-reference", permission_reference.strip()])
    if attribution_text.strip():
        command.extend(["--attribution-text", attribution_text.strip()])
    command.append("--commercial-use" if commercial_use else "--noncommercial-use")
    if resume:
        command.append("--resume")
    return command


def api_configuration(environment: Mapping[str, str]) -> tuple[str, str, str]:
    """Return the configured endpoint, model and key without persisting them."""
    return (
        environment.get("OPENAI_COMPATIBLE_ENDPOINT", ""),
        environment.get("OPENAI_COMPATIBLE_MODEL", ""),
        environment.get("OPENAI_COMPATIBLE_API_KEY", ""),
    )


def terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Stop only the launcher-owned process tree."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        process.terminate()


class SubtitlePreviewDialog:
    """Interactive layout preview that stores an ASS-compatible subtitle position."""

    def __init__(
        self,
        parent: tk.Tk,
        *,
        subtitle_mode: str,
        x_percent: int,
        y_percent: int,
        font_size: int,
        on_apply: Callable[[int, int, int], None],
    ) -> None:
        self.on_apply = on_apply
        self.subtitle_mode = subtitle_mode
        self.x_percent, self.y_percent, self.font_size = clamp_subtitle_preview_values(
            x_percent, y_percent, font_size
        )
        self._drag_mode: str | None = None
        self._drag_start = (0, 0)
        self._drag_initial = (self.x_percent, self.y_percent, self.font_size)
        self._preview_box = (0.0, 0.0, 1.0, 1.0)
        self._subtitle_bounds: tuple[float, float, float, float] | None = None

        self.window = tk.Toplevel(parent)
        self.window.title("字幕样式预览")
        self.window.configure(background=APP_BACKGROUND)
        self.window.geometry("920x650")
        self.window.minsize(700, 520)
        self.window.transient(parent)
        self.window.grab_set()

        body = ttk.Frame(self.window, style="App.TFrame", padding=(20, 18, 20, 16))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="拖动字幕位置与大小", style="SectionTitle.TLabel").pack(anchor="w")
        ttk.Label(
            body,
            text="拖动字幕本体调整位置；拖动右下角绿色控制点调整字号。此设置会用于最终压制。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(3, 12))

        self.canvas = tk.Canvas(
            body,
            background="#E9EDF1",
            highlightthickness=0,
            borderwidth=0,
            cursor="fleur",
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self._draw())
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._stop_drag)

        self.status = tk.StringVar()
        ttk.Label(body, textvariable=self.status, style="Muted.TLabel").pack(
            anchor="w", pady=(10, 8)
        )
        buttons = ttk.Frame(body, style="App.TFrame")
        buttons.pack(fill="x")
        ttk.Button(buttons, text="恢复默认", style="Secondary.TButton", command=self._reset).pack(
            side="left"
        )
        ttk.Button(buttons, text="取消", style="Secondary.TButton", command=self.window.destroy).pack(
            side="right"
        )
        ttk.Button(buttons, text="应用到任务", style="Primary.TButton", command=self._apply).pack(
            side="right", padx=(0, 8)
        )
        self.window.bind("<Escape>", lambda _event: self.window.destroy())
        self.window.after_idle(self._draw)

    def _preview_sample(self) -> str:
        if self.subtitle_mode == "bilingual_en_zh":
            return "Natural subtitles begin with the complete idea.\n先理解完整段落，再呈现自然字幕。"
        if self.subtitle_mode == "bilingual_zh_en":
            return "先理解完整段落，再呈现自然字幕。\nNatural subtitles begin with the complete idea."
        return "先理解完整段落，再呈现自然字幕。"

    def _canvas_preview_box(self) -> tuple[float, float, float, float]:
        canvas_width = max(1, self.canvas.winfo_width())
        canvas_height = max(1, self.canvas.winfo_height())
        unit = min((canvas_width - 24) / SUBTITLE_PREVIEW_WIDTH, (canvas_height - 24) / SUBTITLE_PREVIEW_HEIGHT)
        width = max(320.0, unit * SUBTITLE_PREVIEW_WIDTH)
        height = max(180.0, unit * SUBTITLE_PREVIEW_HEIGHT)
        left = (canvas_width - width) / 2
        top = (canvas_height - height) / 2
        return left, top, left + width, top + height

    def _draw(self) -> None:
        if not self.window.winfo_exists():
            return
        self.canvas.delete("all")
        left, top, right, bottom = self._preview_box = self._canvas_preview_box()
        self.canvas.create_rectangle(left, top, right, bottom, fill="#151B26", outline="#273244", width=1)
        self.canvas.create_rectangle(left, top, right, top + 48, fill="#1E2939", outline="")
        self.canvas.create_text(
            left + 18,
            top + 24,
            text="字幕布局预览 · 16:9",
            anchor="w",
            fill="#C7D0DC",
            font=(UI_FONT, 10),
        )
        self.canvas.create_text(
            (left + right) / 2,
            (top + bottom) / 2 - 26,
            text="VIDEO PREVIEW",
            fill="#39485E",
            font=("Segoe UI", 18, "bold"),
        )
        self.canvas.create_text(
            (left + right) / 2,
            (top + bottom) / 2 + 7,
            text="预览仅用于调整字幕布局，不会下载视频",
            fill="#5D6C82",
            font=(UI_FONT, 10),
        )

        preview_width = right - left
        preview_height = bottom - top
        x = left + preview_width * self.x_percent / 100
        y = top + preview_height * self.y_percent / 100
        size = max(14, round(self.font_size * preview_width / 1920))
        text = self._preview_sample()
        for offset_x, offset_y in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            self.canvas.create_text(
                x + offset_x,
                y + offset_y,
                text=text,
                anchor="s",
                width=preview_width - 56,
                justify="center",
                fill="#101010",
                font=(DEFAULT_SUBTITLE_FONT, size),
                tags=("subtitle",),
            )
        subtitle = self.canvas.create_text(
            x,
            y,
            text=text,
            anchor="s",
            width=preview_width - 56,
            justify="center",
            fill="#FFFFFF",
            font=(DEFAULT_SUBTITLE_FONT, size),
            tags=("subtitle", "subtitle_foreground"),
        )
        bbox = self.canvas.bbox(subtitle)
        if bbox is not None:
            bound_left, bound_top, bound_right, bound_bottom = (value - 7 if index < 2 else value + 7 for index, value in enumerate(bbox))
            self._subtitle_bounds = (bound_left, bound_top, bound_right, bound_bottom)
            self.canvas.create_rectangle(
                bound_left,
                bound_top,
                bound_right,
                bound_bottom,
                outline=PRIMARY,
                dash=(4, 3),
                width=1,
                tags=("subtitle_bounds",),
            )
            self.canvas.create_rectangle(
                bound_right - 6,
                bound_bottom - 6,
                bound_right + 6,
                bound_bottom + 6,
                fill=PRIMARY,
                outline="#FFFFFF",
                width=1,
                tags=("subtitle_handle",),
            )
        self.status.set(
            f"位置：水平 {self.x_percent}% · 垂直 {self.y_percent}%　|　字号：{self.font_size}"
        )

    def _start_drag(self, event: tk.Event[tk.Misc]) -> None:
        item_ids = self.canvas.find_overlapping(event.x, event.y, event.x, event.y)
        tags = {tag for item in item_ids for tag in self.canvas.gettags(item)}
        if "subtitle_handle" in tags:
            self._drag_mode = "resize"
        elif "subtitle" in tags or "subtitle_bounds" in tags:
            self._drag_mode = "move"
        else:
            self._drag_mode = None
            return
        self._drag_start = (event.x, event.y)
        self._drag_initial = (self.x_percent, self.y_percent, self.font_size)

    def _drag(self, event: tk.Event[tk.Misc]) -> None:
        if self._drag_mode is None:
            return
        left, top, right, bottom = self._preview_box
        preview_width = max(1.0, right - left)
        preview_height = max(1.0, bottom - top)
        initial_x, initial_y, initial_size = self._drag_initial
        if self._drag_mode == "move":
            x = round((event.x - left) * 100 / preview_width)
            y = round((event.y - top) * 100 / preview_height)
            self.x_percent, self.y_percent, self.font_size = clamp_subtitle_preview_values(
                x, y, initial_size
            )
        else:
            delta = max(event.x - self._drag_start[0], event.y - self._drag_start[1])
            size_change = round(delta * 1920 / preview_width)
            self.x_percent, self.y_percent, self.font_size = clamp_subtitle_preview_values(
                initial_x, initial_y, initial_size + size_change
            )
        self._draw()

    def _stop_drag(self, _event: tk.Event[tk.Misc]) -> None:
        self._drag_mode = None

    def _reset(self) -> None:
        self.x_percent, self.y_percent, self.font_size = 50, 96, 48
        self._draw()

    def _apply(self) -> None:
        self.on_apply(self.x_percent, self.y_percent, self.font_size)
        self.window.destroy()


class DirectMediaLinkDialog:
    """Focused entry point for complete user-supplied media addresses."""

    def __init__(self, parent: tk.Misc, *, on_add: Callable[[list[str]], None]) -> None:
        self.parent = parent
        self.on_add = on_add
        self.window = tk.Toplevel(parent)
        self.window.title("添加媒体直链｜Localize Studio")
        self.window.geometry("700x430")
        self.window.minsize(620, 390)
        self.window.configure(background=SURFACE)
        self.window.transient(parent)

        body = ttk.Frame(self.window, style="Card.TFrame", padding=(28, 24, 28, 20))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="粘贴播放器显示的完整媒体地址", style="SectionTitle.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            body,
            text=(
                "支持 MP4、WebM、M3U8、MPD，以及 groupvideo.photo.qq.com 这类无扩展名 CDN 地址。"
                "可一行粘贴一条。"
            ),
            style="Muted.TLabel",
            wraplength=630,
            justify="left",
        ).pack(anchor="w", pady=(6, 14))

        self.text = scrolledtext.ScrolledText(
            body,
            height=7,
            wrap="char",
            font=(UI_FONT, 10),
            background="#FBFCFD",
            foreground=TEXT,
            insertbackground=TEXT,
            relief="solid",
            borderwidth=1,
        )
        self.text.pack(fill="both", expand=True)
        self.text.focus_set()

        ttk.Label(
            body,
            text=(
                "必须是完整地址：截图中以 “…” 结尾的显示文字不能下载。"
                "加入后会在后台验证类型和有效性；不会读取浏览器 Cookie 或绕过 DRM。"
            ),
            style="Muted.TLabel",
            wraplength=630,
            justify="left",
        ).pack(anchor="w", pady=(12, 0))

        footer = ttk.Frame(body, style="Card.TFrame")
        footer.pack(fill="x", pady=(18, 0))
        ttk.Button(
            footer,
            text="从剪贴板粘贴",
            style="Secondary.TButton",
            command=self._paste,
        ).pack(side="left")
        ttk.Button(
            footer,
            text="取消",
            style="Secondary.TButton",
            command=self.window.destroy,
        ).pack(side="right")
        ttk.Button(
            footer,
            text="验证并加入任务",
            style="Primary.TButton",
            command=self._add,
        ).pack(side="right", padx=(0, 8))
        self.window.bind("<Control-v>", self._paste_shortcut)

    def _paste_shortcut(self, _event: object) -> str:
        self._paste()
        return "break"

    def _paste(self) -> None:
        try:
            value = self.parent.clipboard_get().strip()
        except tk.TclError:
            messagebox.showinfo("剪贴板为空", "请先复制完整的媒体直链。", parent=self.window)
            return
        self.text.delete("1.0", "end")
        self.text.insert("1.0", value)
        self.text.mark_set("insert", "end-1c")

    def _add(self) -> None:
        try:
            links = validate_direct_media_links(self.text.get("1.0", "end"))
        except ValueError as exc:
            messagebox.showwarning("直链还不能使用", str(exc), parent=self.window)
            return
        self.on_add(links)
        self.window.destroy()


class BrowserCaptureDialog:
    """Visible, cancellable browser-assisted media capture for dynamic playback pages."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        initial_url: str,
        on_capture: Callable[[BrowserMediaCapture], None],
        on_cancel: Callable[[], None] | None = None,
        auto_start: bool = False,
    ) -> None:
        self.parent = parent
        self.on_capture = on_capture
        self.on_cancel = on_cancel
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.closed = False

        self.window = tk.Toplevel(parent)
        self.window.title("浏览器抓取播放地址｜Localize Studio")
        self.window.geometry("720x440")
        self.window.minsize(640, 410)
        self.window.configure(background=SURFACE)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)

        body = ttk.Frame(self.window, style="Card.TFrame", padding=(28, 24, 28, 20))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="从动态播放页捕获完整媒体地址", style="SectionTitle.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            body,
            text=(
                "软件会打开一个使用全新临时配置的 Edge 窗口。让视频开始播放后，"
                "检测到的完整媒体 URL 会自动替换当前播放页并进入任务队列。"
            ),
            style="Muted.TLabel",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(6, 16))

        ttk.Label(body, text="播放页地址", style="Field.TLabel").pack(anchor="w")
        self.page_url = tk.StringVar(value=initial_url.strip())
        self.entry = ttk.Entry(body, textvariable=self.page_url, style="Modern.TEntry")
        self.entry.pack(fill="x", pady=(6, 14), ipady=6)
        self.entry.focus_set()

        self.status = tk.StringVar(value="准备就绪。点击开始后，请在 Edge 中让视频开始播放。")
        ttk.Label(
            body,
            textvariable=self.status,
            style="Muted.TLabel",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(2, 8))
        self.progress = ttk.Progressbar(body, mode="indeterminate", style="Modern.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(0, 14))

        ttk.Label(
            body,
            text=(
                "隐私与权限：不会读取你日常 Edge 的 Cookie、登录状态或密码，也不会保存请求头。"
                "需要站点验证时由你亲自完成；DRM、付费和权限限制不会被绕过。"
            ),
            style="Muted.TLabel",
            wraplength=650,
            justify="left",
        ).pack(anchor="w")

        footer = ttk.Frame(body, style="Card.TFrame")
        footer.pack(fill="x", pady=(20, 0))
        self.cancel_button = ttk.Button(
            footer,
            text="取消",
            style="Secondary.TButton",
            command=self._cancel,
        )
        self.cancel_button.pack(side="right")
        self.start_button = ttk.Button(
            footer,
            text="打开 Edge 并开始抓取",
            style="Primary.TButton",
            command=self._start,
        )
        self.start_button.pack(side="right", padx=(0, 8))
        if auto_start:
            self.window.after(200, self._start)

    def _post(self, callback: Callable[[], None]) -> None:
        with suppress(tk.TclError):
            self.parent.after(0, callback)

    def _set_progress_message(self, message: str) -> None:
        self._post(lambda: self.status.set(message) if not self.closed else None)

    def _start(self) -> None:
        try:
            page_url = validate_browser_capture_input(self.page_url.get())
        except ValueError as exc:
            messagebox.showwarning("播放页地址有误", str(exc), parent=self.window)
            return
        self.cancel_event.clear()
        self.entry.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.progress.start(10)
        self.status.set("正在检查播放页并准备独立 Edge 窗口……")

        def worker() -> None:
            try:
                validate_browser_capture_page(page_url)
                capture = capture_browser_media(
                    page_url,
                    cancel_event=self.cancel_event,
                    progress=self._set_progress_message,
                )
            except (OSError, ValueError, LocalizerError) as exc:
                message = str(exc)
                self._post(lambda error=message: self._failed(error))
            else:
                self._post(lambda: self._finished(capture))

        self.worker = threading.Thread(
            target=worker,
            daemon=True,
            name="localizer-browser-capture",
        )
        self.worker.start()

    def _failed(self, message: str) -> None:
        if self.closed:
            return
        self.progress.stop()
        self.entry.configure(state="normal")
        self.start_button.configure(state="normal")
        self.status.set(message)
        messagebox.showerror("没有捕获到媒体地址", message, parent=self.window)

    def _finished(self, capture: BrowserMediaCapture) -> None:
        if self.closed:
            return
        self.progress.stop()
        self.status.set("已捕获完整媒体地址，正在加入任务队列……")
        self.on_capture(capture)
        self.closed = True
        self.window.destroy()

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.closed = True
        with suppress(tk.TclError):
            self.window.destroy()
        if self.on_cancel is not None:
            self.on_cancel()


class HelpCenterDialog:
    """Persistent tutorial, readiness summary, and recovery guidance."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_select_workflow: Callable[[str], None],
        output_directory: Path,
    ) -> None:
        self.on_select_workflow = on_select_workflow
        self.window = tk.Toplevel(parent)
        self.window.title("帮助中心｜Localize Studio")
        self.window.geometry("760x590")
        self.window.minsize(680, 520)
        self.window.configure(background=SURFACE)
        self.window.transient(parent)

        body = ttk.Frame(self.window, style="Card.TFrame", padding=(28, 24, 28, 20))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Localize Studio 帮助中心", style="SectionTitle.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            body,
            text="教程、模型检查和常见问题都集中在这里；启动软件时不会自动弹出。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 16))

        notebook = ttk.Notebook(body)
        notebook.pack(fill="both", expand=True)
        quick_start = ttk.Frame(notebook, style="Card.TFrame", padding=(22, 20))
        readiness = ttk.Frame(notebook, style="Card.TFrame", padding=(22, 20))
        troubleshooting = ttk.Frame(notebook, style="Card.TFrame", padding=(22, 20))
        notebook.add(quick_start, text="快速开始")
        notebook.add(readiness, text="环境与模型")
        notebook.add(troubleshooting, text="常见问题")
        self._build_quick_start(quick_start)
        self._build_readiness(readiness, output_directory)
        self._build_troubleshooting(troubleshooting)

        footer = ttk.Frame(body, style="Card.TFrame")
        footer.pack(fill="x", pady=(16, 0))
        ttk.Button(
            footer,
            text="打开完整使用说明",
            style="Secondary.TButton",
            command=lambda: webbrowser.open(user_guide_url()),
        ).pack(side="left")
        ttk.Button(
            footer,
            text="关闭",
            style="Primary.TButton",
            command=self.window.destroy,
        ).pack(side="right")

    def _build_quick_start(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="三步拿到成片", style="Field.TLabel").pack(anchor="w")
        instructions = (
            "1. 复制视频链接，或从电脑选择本地视频。\n\n"
            "2. 选择“只下载”或“识别、翻译并压制字幕”。默认会保留源画质、源帧率并自动选择最快的安全硬件方案。\n\n"
            "3. 确认你拥有处理权限后开始。停止或失败的任务可以继续，不需要从头重做。"
        )
        ttk.Label(
            frame,
            text=instructions,
            style="Card.TLabel",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(12, 20))
        actions = ttk.Frame(frame, style="Card.TFrame")
        actions.pack(fill="x")
        ttk.Button(
            actions,
            text="使用“只下载最高画质”模式",
            style="Secondary.TButton",
            command=lambda: self._select_workflow("download"),
        ).pack(side="left")
        ttk.Button(
            actions,
            text="使用“翻译并压制字幕”模式",
            style="Primary.TButton",
            command=lambda: self._select_workflow("subtitles"),
        ).pack(side="left", padx=(10, 0))
        ttk.Label(
            frame,
            text="提示：YouTube、公开 HTML5 播放页、媒体直链和本地视频都从同一个入口处理；一次粘贴多行即可建立队列。",
            style="Muted.TLabel",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(22, 0))

    def _build_readiness(self, frame: ttk.Frame, output_directory: Path) -> None:
        package = package_tier()
        models = installed_whisper_models()
        schedule = detected_resource_schedule()
        ttk.Label(
            frame,
            text=setup_status_message(package, models),
            style="Card.TLabel",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))
        items = setup_readiness_items(
            package,
            models,
            local_ai_ready=local_ai_available(),
            resource_mode=schedule.mode,
            output_advice=output_directory_advice(output_directory),
        )
        visuals = {
            "ready": ("✓", SUCCESS),
            "action": ("!", DANGER),
            "optional": ("○", MUTED),
        }
        for item in items:
            row = ttk.Frame(frame, style="Card.TFrame")
            row.pack(fill="x", pady=5)
            symbol, color = visuals[item.status]
            ttk.Label(row, text=symbol, foreground=color, style="Card.TLabel", width=2).pack(
                side="left", anchor="n"
            )
            copy = ttk.Frame(row, style="Card.TFrame")
            copy.pack(side="left", fill="x", expand=True)
            ttk.Label(copy, text=item.title, style="Field.TLabel").pack(anchor="w")
            ttk.Label(
                copy,
                text=item.detail,
                style="Muted.TLabel",
                wraplength=600,
                justify="left",
            ).pack(anchor="w", pady=(2, 0))
        if not models:
            ttk.Button(
                frame,
                text="打开兼容的 Whisper Small / Medium 下载页",
                style="Primary.TButton",
                command=lambda: webbrowser.open(model_release_page_url()),
            ).pack(anchor="w", pady=(14, 0))

    def _build_troubleshooting(self, frame: ttk.Frame) -> None:
        questions = (
            "下载提示 429：YouTube 暂时限流，等待几分钟后重试即可，已完成阶段会复用。\n\n"
            "电脑变卡或显存不足：关闭占用显卡的软件后重试；低配置机器会自动串行处理，也可以改用 Whisper Small。\n\n"
            "字幕压制失败：视频与字幕中间文件不会丢失，点击“重试未完成任务”会重新检测编码器。\n\n"
            "找不到结果：点击主界面的“输出文件夹”；最终成片在项目的 rendered 文件夹中。\n\n"
            "仍无法解决：点击“导出诊断包”。诊断包会隐藏链接和凭证，也不会包含视频或字幕正文。"
        )
        ttk.Label(frame, text="常见问题与恢复方法", style="Field.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text=questions,
            style="Card.TLabel",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(12, 16))
        ttk.Button(
            frame,
            text="打开当前版本发布页",
            style="Secondary.TButton",
            command=lambda: webbrowser.open(release_page_url()),
        ).pack(anchor="w")

    def _select_workflow(self, workflow: str) -> None:
        self.on_select_workflow(workflow)
        self.window.destroy()


class SubtitleReviewDialog:
    """Edit an existing target subtitle track without changing its time axis."""

    def __init__(
        self,
        parent: tk.Misc,
        session: SubtitleReviewSession,
        config: AppConfig,
        *,
        on_preview: Callable[
            [SubtitleReviewSession, AppConfig, float, Callable[[Path | None, str | None], None]], None
        ],
    ) -> None:
        self.session = session
        self.config = config
        self.on_preview = on_preview
        self.cues = list(session.cues)
        self.selected_index: int | None = None
        self.dirty = False
        self.window = tk.Toplevel(parent)
        self.window.title("字幕审核与快速预览｜Localize Studio")
        self.window.geometry("980x680")
        self.window.minsize(820, 560)
        self.window.configure(background=SURFACE)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self._close)

        body = ttk.Frame(self.window, style="Card.TFrame", padding=(22, 18))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(2, weight=1)
        ttk.Label(body, text="字幕审核", style="SectionTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(
            body,
            text="修改只会更新目标字幕与样式文件，不会重新下载或识别。保存后可从当前段落生成 12 秒预览；最终 MP4 需要你确认后再完整压制。",
            style="Muted.TLabel",
            wraplength=900,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 12))

        table_frame = ttk.Frame(body, style="Card.TFrame")
        table_frame.grid(row=2, column=0, sticky="nsew", padx=(0, 14))
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table_frame,
            columns=("cue", "time", "text"),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("cue", text="#")
        self.tree.heading("time", text="时间")
        self.tree.heading("text", text="字幕内容")
        self.tree.column("cue", width=46, stretch=False, anchor="center")
        self.tree.column("time", width=180, stretch=False)
        self.tree.column("text", width=430, stretch=True)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._select_cue)

        editor = ttk.Frame(body, style="Card.TFrame")
        editor.grid(row=2, column=1, sticky="nsew")
        editor.rowconfigure(2, weight=1)
        editor.columnconfigure(0, weight=1)
        self.cue_label = ttk.Label(editor, text="选择左侧字幕段落", style="Field.TLabel")
        self.cue_label.grid(row=0, column=0, sticky="w")
        ttk.Label(editor, text="可以编辑文字和换行；时间轴保持不变。", style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 8)
        )
        self.editor = scrolledtext.ScrolledText(
            editor,
            height=10,
            wrap="word",
            font=(UI_FONT, 11),
            background="#FAFAFB",
            foreground=TEXT,
            insertbackground=TEXT,
            borderwidth=1,
            relief="solid",
            padx=10,
            pady=8,
        )
        self.editor.grid(row=2, column=0, sticky="nsew")
        self.status = tk.StringVar(value=f"已载入 {len(self.cues)} 条目标字幕")
        ttk.Label(editor, textvariable=self.status, style="Muted.TLabel", wraplength=330).grid(
            row=3, column=0, sticky="w", pady=(10, 0)
        )

        footer = ttk.Frame(body, style="Card.TFrame")
        footer.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        ttk.Button(footer, text="关闭", style="Secondary.TButton", command=self._close).pack(side="left")
        self.preview_button = ttk.Button(
            footer, text="从此处预览 12 秒", style="Secondary.TButton", command=self._preview
        )
        self.preview_button.pack(side="right")
        ttk.Button(footer, text="保存修改", style="Primary.TButton", command=self._save).pack(
            side="right", padx=(0, 8)
        )
        self._populate()

    def _populate(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, cue in enumerate(self.cues):
            timing = f"{ms_to_srt(cue.start_ms)} → {ms_to_srt(cue.end_ms)}"
            self.tree.insert("", "end", iid=str(index), values=(cue.id, timing, cue.text.replace("\n", " / ")))
        if self.cues:
            self.tree.selection_set("0")
            self.tree.focus("0")
            self._load_selected(0)

    def _commit_editor(self) -> bool:
        if self.selected_index is None:
            return True
        text = self.editor.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showwarning("字幕不能为空", "请保留当前段落的文字，或取消编辑。", parent=self.window)
            return False
        current = self.cues[self.selected_index]
        if text != current.text:
            self.cues[self.selected_index] = current.model_copy(update={"text": text})
            self.tree.set(str(self.selected_index), "text", text.replace("\n", " / "))
            self.dirty = True
        return True

    def _load_selected(self, index: int) -> None:
        cue = self.cues[index]
        self.selected_index = index
        self.cue_label.configure(text=f"第 {cue.id} 条　{ms_to_srt(cue.start_ms)} → {ms_to_srt(cue.end_ms)}")
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", cue.text)

    def _select_cue(self, _event: object) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        next_index = int(selection[0])
        if next_index == self.selected_index:
            return
        if not self._commit_editor():
            if self.selected_index is not None:
                self.tree.selection_set(str(self.selected_index))
            return
        self._load_selected(next_index)

    def _save(self, *, quiet: bool = False) -> bool:
        if not self._commit_editor():
            return False
        styled_subtitle = (
            self.session.ass_path
            if self.config.subtitle_mode == "chinese"
            else self.session.project.bilingual_ass
        )
        if not self.dirty and styled_subtitle.is_file():
            return True
        try:
            outputs = save_reviewed_subtitles(self.session, self.config, self.cues)
        except (OSError, ValueError, LocalizerError) as exc:
            messagebox.showerror("无法保存字幕", str(exc), parent=self.window)
            return False
        self.session = SubtitleReviewSession(
            self.session.project, self.session.subtitle_path, self.session.ass_path, list(self.cues)
        )
        self.dirty = False
        self.status.set("已保存字幕与样式文件；最终 MP4 尚未重新压制。")
        if not quiet:
            messagebox.showinfo(
                "字幕已保存",
                "已更新：\n" + "\n".join(str(path.name) for path in outputs) + "\n\n可先生成短预览，确认后再完整压制最终视频。",
                parent=self.window,
            )
        return True

    def _preview(self) -> None:
        if not self._save(quiet=True):
            return
        if self.selected_index is None:
            return
        start = self.cues[self.selected_index].start_ms / 1000
        self.preview_button.configure(state="disabled", text="正在生成预览…")
        self.status.set("正在压制所选位置附近的 12 秒预览…")
        self.on_preview(self.session, self.config, start, self._preview_finished)

    def _preview_finished(self, output: Path | None, error: str | None) -> None:
        if not self.window.winfo_exists():
            return
        self.preview_button.configure(state="normal", text="从此处预览 12 秒")
        if error:
            self.status.set("预览生成失败。")
            messagebox.showerror("预览失败", error, parent=self.window)
            return
        assert output is not None
        self.status.set(f"预览已生成：{output.name}")
        messagebox.showinfo("预览已生成", f"短预览已保存到：\n{output}", parent=self.window)

    def _close(self) -> None:
        if self.dirty and not messagebox.askyesno(
            "未保存的字幕修改", "有尚未保存的字幕修改，确定关闭吗？", parent=self.window
        ):
            return
        self.window.destroy()


class RightsDetailsDialog:
    """Collect non-secret rights metadata without turning the main screen into a legal form."""

    def __init__(
        self,
        parent: tk.Tk,
        *,
        basis: str,
        values: Mapping[str, object],
        on_apply: Callable[[dict[str, object]], None],
    ) -> None:
        self.on_apply = on_apply
        self.window = tk.Toplevel(parent)
        self.window.title("版权依据与署名记录")
        self.window.geometry("690x470")
        self.window.minsize(620, 430)
        self.window.transient(parent)
        self.window.grab_set()
        self.window.configure(background=APP_BACKGROUND)

        self.rights_holder = tk.StringVar(value=str(values.get("rights_holder", "")))
        self.license_url = tk.StringVar(value=str(values.get("license_url", "")))
        self.permission_reference = tk.StringVar(
            value=str(values.get("permission_reference", ""))
        )
        self.attribution_text = tk.StringVar(value=str(values.get("attribution_text", "")))
        self.allow_translation = tk.BooleanVar(
            value=bool(values.get("allow_translation", True))
        )
        self.allow_redistribution = tk.BooleanVar(
            value=bool(values.get("allow_redistribution", True))
        )
        self.commercial_use = tk.BooleanVar(value=bool(values.get("commercial_use", False)))

        card = ttk.Frame(self.window, style="Card.TFrame", padding=(24, 20))
        card.pack(fill="both", expand=True, padx=18, pady=18)
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text="版权记录", style="SectionTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            card,
            text=(
                f"当前依据：{RIGHTS_BASIS_LABELS.get(basis, basis)}。"
                "这里只记录核对信息，不上传授权文件，也不会自动判断法律效力。"
            ),
            style="Muted.TLabel",
            wraplength=620,
        ).grid(row=1, column=0, sticky="w", pady=(5, 14))

        fields = (
            ("权利人／原作者", self.rights_holder),
            ("许可或权利状态链接", self.license_url),
            ("授权证明索引／日期／说明（不要填写密码）", self.permission_reference),
            ("自定义署名文字（留空则自动生成）", self.attribution_text),
        )
        for index, (label, variable) in enumerate(fields, start=2):
            ttk.Label(card, text=label, style="Field.TLabel").grid(
                row=index * 2 - 2, column=0, sticky="w", pady=(5, 4)
            )
            ttk.Entry(card, textvariable=variable, style="Modern.TEntry").grid(
                row=index * 2 - 1, column=0, sticky="ew"
            )

        checks = ttk.Frame(card, style="Card.TFrame")
        checks.grid(row=10, column=0, sticky="ew", pady=(14, 0))
        ttk.Checkbutton(
            checks,
            text="许可包含翻译／改编",
            variable=self.allow_translation,
            style="Card.TCheckbutton",
        ).pack(side="left")
        ttk.Checkbutton(
            checks,
            text="许可包含重新发布／分发",
            variable=self.allow_redistribution,
            style="Card.TCheckbutton",
        ).pack(side="left", padx=(14, 0))
        ttk.Checkbutton(
            checks,
            text="计划商业使用",
            variable=self.commercial_use,
            style="Card.TCheckbutton",
        ).pack(side="left", padx=(14, 0))

        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=11, column=0, sticky="e", pady=(18, 0))
        ttk.Button(
            actions,
            text="取消",
            style="Secondary.TButton",
            command=self.window.destroy,
        ).pack(side="left")
        ttk.Button(
            actions,
            text="保存记录",
            style="Primary.TButton",
            command=self._apply,
        ).pack(side="left", padx=(8, 0))

    def _apply(self) -> None:
        self.on_apply(
            {
                "rights_holder": self.rights_holder.get().strip(),
                "license_url": self.license_url.get().strip(),
                "permission_reference": self.permission_reference.get().strip(),
                "attribution_text": self.attribution_text.get().strip(),
                "allow_translation": self.allow_translation.get(),
                "allow_redistribution": self.allow_redistribution.get(),
                "commercial_use": self.commercial_use.get(),
            }
        )
        self.window.destroy()


class LocalizerWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Localize Studio｜视频本地化")
        self._application_icon: tk.PhotoImage | None = None
        if icon := application_icon_path():
            try:
                self._application_icon = tk.PhotoImage(file=str(icon))
                self.root.iconphoto(True, self._application_icon)
            except tk.TclError:
                # A missing/unsupported icon must never prevent the application from starting.
                self._application_icon = None
        self.root.geometry("1180x820")
        self.root.minsize(960, 700)
        self.root.configure(background=APP_BACKGROUND)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self._process_lock = threading.Lock()
        self._active_processes: set[subprocess.Popen[str]] = set()
        self._processes_by_queue_index: dict[int, subprocess.Popen[str]] = {}
        self._pause_requested_indices: set[int] = set()
        self.worker: threading.Thread | None = None
        self._analysis_worker: threading.Thread | None = None
        self._analysis_generation = 0
        self._analysis_after_id: str | None = None
        self._queue_save_after_id: str | None = None
        self._task_sources: list[str] = []
        self._task_states: dict[int, str] = {}
        self._task_errors: dict[int, str] = {}
        self._media_previews: dict[int, MediaPreview] = {}
        self._media_preview_errors: dict[int, str] = {}
        self._browser_capture_prompted_sources: set[str] = set()
        self._pending_start_sources: tuple[str, ...] | None = None
        self.stop_requested = False
        self.active_direction = "en-to-zh"
        self.active_provider = ""
        self._progress_value = 0.0
        self.active_queue_index = 1
        self.active_queue_total = 1
        self._task_progress: dict[int, float] = {}
        self._parallel_queue = False
        self._queue_parallel_limit = 1
        self._current_queue_commands: list[list[str]] = []
        self._commands_by_task_index: dict[int, list[str]] = {}
        self._retry_task_indices: tuple[int, ...] = ()
        self._current_queue_environment: dict[str, str] = {}
        self._current_output_directory = default_output_directory()
        self._retry_commands: list[list[str]] = []
        self._project_paths_by_queue_index: dict[int, Path] = {}
        self._diagnostic_projects: set[Path] = set()
        self.help_center: HelpCenterDialog | None = None
        self.direct_media_dialog: DirectMediaLinkDialog | None = None
        self.browser_capture_dialog: BrowserCaptureDialog | None = None

        endpoint, model, api_key = api_configuration(os.environ)
        saved_settings = load_desktop_settings()
        saved_queue = load_desktop_queue()
        self._restored_task_snapshots = {task.source: task for task in saved_queue.tasks}
        automatic_translation = (
            "本地 AI 段落翻译并压制（高质量，无 API Key）"
            if local_ai_available()
            else "本地快速翻译并压制（无需 API）"
        )
        saved_provider = saved_settings.translation_provider
        if saved_provider == "ollama" and not local_ai_available():
            saved_provider = "offline"
        saved_font_size_label = label_for_value(
            SUBTITLE_FONT_SIZES,
            saved_settings.font_size,
            f"自定义（{saved_settings.font_size}）",
        )

        self.input_value = tk.StringVar(
            value="\n".join(task.source for task in saved_queue.tasks)
        )
        self.direction_label = tk.StringVar(
            value=label_for_value(
                TRANSLATION_DIRECTIONS, saved_settings.direction, "英文 → 简体中文"
            )
        )
        self.subtitle_label = tk.StringVar(
            value=label_for_value(SUBTITLE_MODES, saved_settings.subtitle_mode, "仅目标语言字幕")
        )
        self.translation_label = tk.StringVar(
            value=label_for_value(TRANSLATION_MODES, saved_provider, automatic_translation)
        )
        self.font_size_label = tk.StringVar(value=saved_font_size_label)
        self.subtitle_font_size_value = tk.IntVar(value=saved_settings.font_size)
        self.subtitle_position_x = tk.IntVar(value=saved_settings.subtitle_x_percent)
        self.subtitle_position_y = tk.IntVar(value=saved_settings.subtitle_y_percent)
        self.output_quality_label = tk.StringVar(
            value=label_for_value(
                OUTPUT_QUALITIES, saved_settings.output_quality, "最高质量（推荐）"
            )
        )
        self.output_fps_label = tk.StringVar(
            value=label_for_value(
                OUTPUT_FRAME_RATES, saved_settings.output_fps, "保持原视频帧率（推荐）"
            )
        )
        self.output_height_label = tk.StringVar(
            value=label_for_value(
                OUTPUT_HEIGHTS,
                saved_settings.output_height,
                "保持原视频分辨率（推荐）",
            )
        )
        self.enhancement_label = tk.StringVar(
            value=label_for_value(
                SUPER_RESOLUTION_MODES,
                saved_settings.enhancement_mode,
                "关闭（保持原始像素）",
            )
        )
        self.output_directory = tk.StringVar(value=saved_settings.output_directory)
        self.endpoint = tk.StringVar(value=endpoint or "https://api.openai.com/v1")
        self.model = tk.StringVar(value=model)
        self.api_key = tk.StringVar(value=api_key)
        self.update_channel_label = tk.StringVar(
            value=label_for_value(UPDATE_CHANNELS, saved_settings.update_channel, "稳定")
        )
        self.authorized = tk.BooleanVar(value=False)
        self.rights_basis_label = tk.StringVar(value="本人／本团队原创")
        self.rights_holder = tk.StringVar(value="")
        self.rights_license_url = tk.StringVar(value="")
        self.rights_permission_reference = tk.StringVar(value="")
        self.rights_attribution_text = tk.StringVar(value="")
        self.rights_allow_translation = tk.BooleanVar(value=True)
        self.rights_allow_redistribution = tk.BooleanVar(value=True)
        self.rights_commercial_use = tk.BooleanVar(value=False)
        self.rights_summary = tk.StringVar(value="版权记录：本人／本团队原创")
        self.resume = tk.BooleanVar(value=saved_settings.resume)
        self.status = tk.StringVar(value="等待粘贴链接")
        self.mode_hint = tk.StringVar()
        self.output_hint = tk.StringVar()
        self.output_directory_hint = tk.StringVar()
        self.workflow_summary = tk.StringVar()
        schedule = detected_resource_schedule()
        self.readiness_hint = tk.StringVar(
            value=quick_readiness_message(
                installed_whisper_models(),
                local_ai_ready=local_ai_available(),
                resource_mode=schedule.mode,
            )
        )
        self.settings_visible = False
        self.log_visible = False

        self._configure_style()
        self._build_layout()
        self._update_translation_fields()
        self._update_output_settings()
        self._update_rights_summary()
        self.input_value.trace_add("write", self._update_input_state)
        self.output_directory.trace_add("write", self._update_output_directory_hint)
        self._update_input_state()
        if saved_queue.tasks:
            self._set_status(
                f"已恢复上次的 {len(saved_queue.tasks)} 个任务；中断项目可直接继续",
                "active",
            )
            if len(saved_queue.tasks) == 1:
                restored = saved_queue.tasks[0]
                restored_detail = f"{restored.media_summary} {restored.error}".casefold()
                if browser_capture_required(restored_detail):
                    self._browser_capture_prompted_sources.add(restored.source)
                    self.root.after(
                        650,
                        lambda page=restored.source: self._open_browser_capture_dialog(
                            page, auto_start=True
                        ),
                    )
        self.root.after(100, self._poll_events)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure("App.TFrame", background=APP_BACKGROUND)
        style.configure("Toolbar.TFrame", background=HEADER)
        style.configure(
            "Card.TFrame",
            background=SURFACE,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            borderwidth=1,
            relief="solid",
        )
        style.configure("CardBody.TFrame", background=SURFACE, relief="flat")
        style.configure("Soft.TFrame", background=PRIMARY_SOFT, relief="flat")
        style.configure("Toolbar.TLabel", background=HEADER, foreground=TEXT, font=(UI_FONT, 10))
        style.configure("Card.TLabel", background=SURFACE, foreground=TEXT, font=(UI_FONT, 10))
        style.configure(
            "Field.TLabel", background=SURFACE, foreground=TEXT, font=(UI_FONT, 9, "bold")
        )
        style.configure("Muted.TLabel", background=SURFACE, foreground=MUTED, font=(UI_FONT, 9))
        style.configure(
            "AppMuted.TLabel", background=APP_BACKGROUND, foreground=MUTED, font=(UI_FONT, 9)
        )
        style.configure(
            "SectionTitle.TLabel",
            background=SURFACE,
            foreground=TEXT,
            font=(UI_FONT, 13, "bold"),
        )
        style.configure(
            "HeroTitle.TLabel",
            background=SURFACE,
            foreground=TEXT,
            font=(UI_FONT, 19, "bold"),
        )
        style.configure(
            "HeroMuted.TLabel",
            background=SURFACE,
            foreground=MUTED,
            font=(UI_FONT, 10),
        )
        style.configure(
            "Soft.TLabel",
            background=PRIMARY_SOFT,
            foreground="#26724A",
            font=(UI_FONT, 9),
        )
        style.configure(
            "Eyebrow.TLabel",
            background=SURFACE,
            foreground=PRIMARY_HOVER,
            font=(UI_FONT, 9, "bold"),
        )
        style.configure(
            "Primary.TButton",
            background=PRIMARY,
            foreground="#FFFFFF",
            bordercolor=PRIMARY,
            font=(UI_FONT, 10, "bold"),
            padding=(18, 10),
        )
        style.map(
            "Primary.TButton",
            background=[
                ("pressed", PRIMARY_HOVER),
                ("active", PRIMARY_HOVER),
                ("disabled", "#A9D8BD"),
            ],
            foreground=[("disabled", "#EFF8F2")],
        )
        style.configure(
            "Secondary.TButton",
            background=SURFACE,
            foreground=TEXT,
            bordercolor=BORDER,
            font=(UI_FONT, 9),
            padding=(13, 8),
        )
        style.map(
            "Secondary.TButton",
            background=[("active", "#F4F6F8"), ("pressed", "#ECEFF2")],
        )
        style.configure(
            "Source.TButton",
            background=HEADER,
            foreground=TEXT,
            bordercolor=BORDER,
            font=(UI_FONT, 9),
            padding=(13, 8),
        )
        style.map(
            "Source.TButton",
            background=[("active", PRIMARY_SOFT), ("pressed", "#DDF1E6")],
            foreground=[("active", PRIMARY_HOVER)],
        )
        style.configure(
            "Toolbar.TButton",
            background=HEADER,
            foreground=MUTED,
            bordercolor=HEADER,
            font=(UI_FONT, 9),
            padding=(10, 7),
        )
        style.map(
            "Toolbar.TButton",
            background=[("active", "#F0F2F4")],
            foreground=[("active", TEXT)],
        )
        style.configure(
            "CardToolbar.TButton",
            background=SURFACE,
            foreground=MUTED,
            bordercolor=SURFACE,
            font=(UI_FONT, 9),
            padding=(9, 6),
        )
        style.map(
            "CardToolbar.TButton",
            background=[("active", "#F4F6F8")],
            foreground=[("active", TEXT)],
        )
        style.configure(
            "SoftLink.TButton",
            background=PRIMARY_SOFT,
            foreground=PRIMARY_HOVER,
            bordercolor=PRIMARY_SOFT,
            font=(UI_FONT, 9, "bold"),
            padding=(8, 5),
        )
        style.map("SoftLink.TButton", background=[("active", "#DDF1E6")])
        style.configure(
            "Danger.TButton",
            background="#FBEAEC",
            foreground=DANGER,
            bordercolor="#F2CDD1",
            font=(UI_FONT, 9, "bold"),
            padding=(14, 8),
        )
        style.map("Danger.TButton", background=[("active", "#F6D9DC")])
        style.configure(
            "Modern.TEntry",
            fieldbackground="#FAFAFB",
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=8,
        )
        style.configure(
            "Modern.TCombobox",
            fieldbackground="#FAFAFB",
            background="#FAFAFB",
            foreground=TEXT,
            bordercolor=BORDER,
            arrowsize=16,
            padding=7,
        )
        style.map(
            "Modern.TCombobox",
            fieldbackground=[("readonly", "#FAFAFB"), ("disabled", "#ECEEF1")],
            foreground=[("readonly", TEXT), ("disabled", "#98A2B3")],
        )
        style.configure("Card.TCheckbutton", background=SURFACE, foreground=TEXT, font=(UI_FONT, 9))
        style.map("Card.TCheckbutton", background=[("active", SURFACE)])
        style.configure(
            "Accent.Horizontal.TProgressbar",
            background=PRIMARY,
            troughcolor="#E8ECE9",
            bordercolor="#E8ECE9",
            lightcolor=PRIMARY,
            darkcolor=PRIMARY,
            thickness=7,
        )
        style.configure(
            "Task.Treeview",
            background=SURFACE,
            fieldbackground=SURFACE,
            foreground=TEXT,
            bordercolor=BORDER,
            rowheight=38,
            font=(UI_FONT, 9),
        )
        style.configure(
            "Task.Treeview.Heading",
            background="#F4F6F7",
            foreground=MUTED,
            bordercolor=BORDER,
            font=(UI_FONT, 9, "bold"),
            padding=(8, 8),
        )
        style.map(
            "Task.Treeview",
            background=[("selected", PRIMARY_SOFT)],
            foreground=[("selected", TEXT)],
        )

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, style="App.TFrame")
        outer.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(7, weight=1)

        header_shell = tk.Frame(outer, background=HEADER)
        header_shell.grid(row=0, column=0, sticky="ew")
        header_shell.columnconfigure(0, weight=1)

        hero = tk.Frame(header_shell, background=HEADER, padx=24, pady=13)
        hero.grid(row=0, column=0, sticky="ew")
        hero.columnconfigure(1, weight=1)
        brand = tk.Frame(hero, background=HEADER)
        brand.grid(row=0, column=0, sticky="w")
        if self._application_icon is not None:
            icon_scale = max(1, self._application_icon.width() // 38)
            self._header_icon = self._application_icon.subsample(icon_scale, icon_scale)
            tk.Label(
                brand,
                image=self._header_icon,
                background=HEADER,
                borderwidth=0,
            ).pack(side="left")
        else:
            self._header_icon = None
            tk.Label(
                brand,
                text="L",
                width=2,
                background=PRIMARY,
                foreground="#FFFFFF",
                font=(UI_FONT, 13, "bold"),
            ).pack(side="left")
        brand_copy = tk.Frame(brand, background=HEADER)
        brand_copy.pack(side="left", padx=(10, 0))
        tk.Label(
            brand_copy,
            text="Localize Studio",
            background=HEADER,
            foreground=TEXT,
            font=(UI_FONT, 12, "bold"),
        ).pack(anchor="w")
        tk.Label(
            brand_copy,
            text="Video Localizer · 本地 AI 字幕工具",
            background=HEADER,
            foreground=MUTED,
            font=(UI_FONT, 8),
        ).pack(anchor="w", pady=(1, 0))

        secondary_tools = tk.Frame(hero, background=HEADER)
        secondary_tools.grid(row=0, column=2, sticky="e")
        self.settings_button = ttk.Button(
            secondary_tools,
            text="处理设置",
            style="Toolbar.TButton",
            command=self._toggle_settings,
        )
        self.settings_button.pack(side="left")
        ttk.Button(
            secondary_tools,
            text="字幕审核",
            style="Toolbar.TButton",
            command=self._open_subtitle_review,
        ).pack(side="left", padx=(2, 0))
        ttk.Button(
            secondary_tools,
            text="输出文件夹",
            style="Toolbar.TButton",
            command=self._open_output,
        ).pack(side="left", padx=(2, 0))
        ttk.Button(
            secondary_tools,
            text="导出诊断",
            style="Toolbar.TButton",
            command=self._export_support_bundle,
        ).pack(side="left", padx=(2, 0))
        ttk.Button(
            secondary_tools,
            text="帮助中心",
            style="Toolbar.TButton",
            command=self._open_help_center,
        ).pack(side="left", padx=(2, 0))

        sourcebar = tk.Frame(header_shell, background=HEADER, padx=24, pady=10)
        sourcebar.grid(row=1, column=0, sticky="ew")
        sourcebar.columnconfigure(5, weight=1)
        self.paste_button = ttk.Button(
            sourcebar, text="＋  粘贴链接", style="Primary.TButton", command=self._paste
        )
        self.paste_button.grid(row=0, column=0, padx=(0, 8))
        self.direct_media_button = ttk.Button(
            sourcebar,
            text="媒体直链",
            style="Source.TButton",
            command=self._open_direct_media_dialog,
        )
        self.direct_media_button.grid(row=0, column=1, padx=(0, 8))
        self.browser_capture_button = ttk.Button(
            sourcebar,
            text="从浏览器获取",
            style="Source.TButton",
            command=self._open_browser_capture_dialog,
        )
        self.browser_capture_button.grid(row=0, column=2, padx=(0, 8))
        self.local_file_button = ttk.Button(
            sourcebar,
            text="选择本地视频",
            style="Source.TButton",
            command=self._choose_local_file,
        )
        self.local_file_button.grid(row=0, column=3)
        update_tools = tk.Frame(secondary_tools, background=HEADER)
        update_tools.pack(side="right", padx=(10, 0))
        self.update_channel_combo = ttk.Combobox(
            update_tools,
            textvariable=self.update_channel_label,
            values=tuple(UPDATE_CHANNELS),
            state="readonly",
            width=4,
            style="Modern.TCombobox",
        )
        self.update_channel_combo.pack(side="left", padx=(0, 4))
        self.update_button = ttk.Button(
            update_tools,
            text="检查更新",
            style="Toolbar.TButton",
            command=self._begin_update_check,
        )
        self.update_button.pack(side="left")
        ttk.Label(
            sourcebar,
            textvariable=self.readiness_hint,
            style="Toolbar.TLabel",
        ).grid(row=0, column=5, sticky="e", padx=(18, 0))
        ttk.Separator(header_shell, orient="horizontal").grid(row=2, column=0, sticky="ew")

        self.empty_state = ttk.Frame(outer, style="App.TFrame")
        self.empty_state.grid(row=1, column=0, rowspan=7, sticky="nsew")
        empty_content = ttk.Frame(
            self.empty_state,
            style="Card.TFrame",
            padding=(54, 40, 54, 38),
        )
        empty_content.pack(expand=True)
        ttk.Label(
            empty_content,
            text="VIDEO LOCALIZER",
            style="Eyebrow.TLabel",
        ).pack(pady=(0, 9))
        ttk.Label(
            empty_content,
            text="从一个视频开始",
            style="HeroTitle.TLabel",
        ).pack()
        ttk.Label(
            empty_content,
            text="粘贴授权链接或选择本地视频，程序会先分析画质、帧率和大小，再开始处理。",
            style="HeroMuted.TLabel",
        ).pack(pady=(9, 0))

        empty_actions = ttk.Frame(empty_content, style="CardBody.TFrame")
        empty_actions.pack(pady=(22, 20))
        ttk.Button(
            empty_actions,
            text="＋  粘贴视频链接",
            style="Primary.TButton",
            command=self._paste,
        ).pack(side="left")
        ttk.Button(
            empty_actions,
            text="选择本地视频",
            style="Secondary.TButton",
            command=self._choose_local_file,
        ).pack(side="left", padx=(10, 0))

        feature_row = ttk.Frame(empty_content, style="CardBody.TFrame")
        feature_row.pack()
        for index, feature in enumerate(("最高画质", "本地 Whisper", "离线翻译", "断点续跑")):
            tk.Label(
                feature_row,
                text=f"✓  {feature}",
                background="#F4F6F8",
                foreground="#4B5563",
                font=(UI_FONT, 9),
                padx=11,
                pady=6,
            ).grid(row=0, column=index, padx=(0 if index == 0 else 7, 0))

        readiness = ttk.Frame(empty_content, style="Soft.TFrame", padding=(16, 9))
        readiness.pack(fill="x", pady=(22, 0))
        ttk.Label(
            readiness,
            textvariable=self.readiness_hint,
            style="Soft.TLabel",
        ).pack(side="left")
        ttk.Button(
            readiness,
            text="查看环境与教程  →",
            style="SoftLink.TButton",
            command=self._open_help_center,
        ).pack(side="right", padx=(22, 0))

        self.input_frame = ttk.Frame(outer, style="Card.TFrame", padding=(24, 18, 24, 20))
        self.input_frame.grid(row=1, column=0, sticky="ew", padx=24, pady=(20, 0))
        self.input_frame.columnconfigure(0, weight=1)
        ttk.Label(self.input_frame, text="任务队列", style="SectionTitle.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 3)
        )
        ttk.Label(
            self.input_frame,
            text="链接分析在后台进行，确认媒体信息后再开始处理",
            style="Muted.TLabel",
        ).grid(
            row=0, column=1, sticky="e", pady=(0, 3)
        )
        self.input_entry = ttk.Entry(
            self.input_frame,
            textvariable=self.input_value,
            style="Modern.TEntry",
            font=(UI_FONT, 10),
        )
        self.input_entry.grid(row=1, column=0, sticky="ew", ipady=2)
        ttk.Button(
            self.input_frame,
            text="移除",
            style="Secondary.TButton",
            command=self._clear_input,
        ).grid(row=1, column=1, padx=(8, 0))
        ttk.Label(self.input_frame, textvariable=self.workflow_summary, style="Muted.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(9, 0)
        )
        task_toolbar = ttk.Frame(self.input_frame, style="CardBody.TFrame")
        task_toolbar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 7))
        task_toolbar.columnconfigure(0, weight=1)
        ttk.Label(task_toolbar, text="视频任务", style="Field.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.remove_selected_button = ttk.Button(
            task_toolbar,
            text="移除所选",
            style="CardToolbar.TButton",
            command=self._remove_selected_task,
            state="disabled",
        )
        self.remove_selected_button.grid(row=0, column=1, padx=(4, 0))
        self.pause_selected_button = ttk.Button(
            task_toolbar,
            text="暂停所选",
            style="CardToolbar.TButton",
            command=self._pause_selected_task,
            state="disabled",
        )
        self.pause_selected_button.grid(row=0, column=2, padx=(4, 0))
        self.retry_selected_button = ttk.Button(
            task_toolbar,
            text="继续所选",
            style="CardToolbar.TButton",
            command=self._retry_selected_task,
            state="disabled",
        )
        self.retry_selected_button.grid(row=0, column=3, padx=(4, 0))
        self.open_rendered_button = ttk.Button(
            task_toolbar,
            text="打开成片",
            style="CardToolbar.TButton",
            command=self._open_selected_rendered,
            state="disabled",
        )
        self.open_rendered_button.grid(row=0, column=4, padx=(4, 0))
        self.open_task_button = ttk.Button(
            task_toolbar,
            text="打开项目",
            style="CardToolbar.TButton",
            command=self._open_selected_task_project,
            state="disabled",
        )
        self.open_task_button.grid(row=0, column=5, padx=(4, 0))

        task_list = ttk.Frame(self.input_frame, style="CardBody.TFrame")
        task_list.grid(row=4, column=0, columnspan=2, sticky="ew")
        task_list.columnconfigure(0, weight=1)
        self.task_tree = ttk.Treeview(
            task_list,
            columns=("video", "media", "status", "progress"),
            show="headings",
            height=5,
            selectmode="browse",
            style="Task.Treeview",
        )
        self.task_tree.heading("video", text="视频")
        self.task_tree.heading("media", text="媒体信息（下载前预分析）")
        self.task_tree.heading("status", text="状态")
        self.task_tree.heading("progress", text="进度")
        self.task_tree.column("video", width=280, minwidth=180, stretch=True)
        self.task_tree.column("media", width=355, minwidth=245, stretch=True)
        self.task_tree.column("status", width=105, minwidth=90, stretch=False)
        self.task_tree.column("progress", width=70, minwidth=60, stretch=False, anchor="center")
        self.task_tree.grid(row=0, column=0, sticky="ew")
        task_scrollbar = ttk.Scrollbar(
            task_list,
            orient="vertical",
            command=self.task_tree.yview,
        )
        task_scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        self.task_tree.configure(yscrollcommand=task_scrollbar.set)
        self.task_tree.bind("<<TreeviewSelect>>", self._update_task_action_states)

        options = self.settings_panel = ttk.Frame(
            outer, style="Card.TFrame", padding=(22, 14, 22, 14)
        )
        options.grid(row=2, column=0, sticky="ew", padx=24, pady=(12, 0))
        for column in range(4):
            options.columnconfigure(column, weight=1, uniform="settings")
        ttk.Label(options, text="处理设置", style="SectionTitle.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 3)
        )
        ttk.Label(
            options,
            text="只显示会影响最终结果的选项；性能方案由程序按硬件自动选择",
            style="Muted.TLabel",
        ).grid(row=0, column=1, columnspan=3, sticky="e", pady=(0, 3))
        ttk.Label(options, text="翻译方向", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", pady=(11, 5), padx=(0, 6)
        )
        ttk.Label(options, text="字幕 / 下载模式", style="Field.TLabel").grid(
            row=1, column=1, sticky="w", pady=(11, 5), padx=6
        )
        ttk.Label(options, text="翻译方式", style="Field.TLabel").grid(
            row=1, column=2, sticky="w", pady=(11, 5), padx=6
        )
        ttk.Label(options, text="字幕字号", style="Field.TLabel").grid(
            row=1, column=3, sticky="w", pady=(11, 5), padx=(6, 0)
        )
        self.direction_combo = ttk.Combobox(
            options,
            textvariable=self.direction_label,
            values=list(TRANSLATION_DIRECTIONS),
            state="readonly",
            style="Modern.TCombobox",
        )
        self.direction_combo.grid(row=2, column=0, sticky="ew", padx=(0, 6))
        self.direction_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._update_translation_fields()
        )
        self.subtitle_combo = ttk.Combobox(
            options,
            textvariable=self.subtitle_label,
            values=list(SUBTITLE_MODES),
            state="readonly",
            style="Modern.TCombobox",
        )
        self.subtitle_combo.grid(row=2, column=1, sticky="ew", padx=6)
        self.subtitle_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._update_translation_fields()
        )
        self.font_size_combo = ttk.Combobox(
            options,
            textvariable=self.font_size_label,
            values=[
                *SUBTITLE_FONT_SIZES,
                *(
                    [self.font_size_label.get()]
                    if self.font_size_label.get() not in SUBTITLE_FONT_SIZES
                    else []
                ),
            ],
            state="readonly",
            style="Modern.TCombobox",
        )
        self.font_size_combo.grid(row=2, column=3, sticky="ew", padx=(6, 0))
        self.font_size_combo.bind("<<ComboboxSelected>>", self._select_subtitle_font_size)
        self.translation_combo = ttk.Combobox(
            options,
            textvariable=self.translation_label,
            values=list(TRANSLATION_MODES),
            state="readonly",
            style="Modern.TCombobox",
        )
        self.translation_combo.grid(row=2, column=2, sticky="ew", padx=6)
        self.translation_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._update_translation_fields()
        )
        ttk.Label(options, textvariable=self.mode_hint, style="Muted.TLabel").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(9, 0)
        )
        self.preview_button = ttk.Button(
            options,
            text="预览并调整字幕",
            style="Secondary.TButton",
            command=self._show_subtitle_preview,
        )
        self.preview_button.grid(row=3, column=3, sticky="e", pady=(8, 0))

        ttk.Label(options, text="AI 画质增强", style="Field.TLabel").grid(
            row=4, column=0, sticky="w", pady=(12, 5), padx=(0, 6)
        )
        ttk.Label(options, text="输出画质", style="Field.TLabel").grid(
            row=4, column=1, sticky="w", pady=(12, 5), padx=6
        )
        ttk.Label(options, text="输出帧率", style="Field.TLabel").grid(
            row=4, column=2, sticky="w", pady=(12, 5), padx=6
        )
        ttk.Label(options, text="输出分辨率", style="Field.TLabel").grid(
            row=4, column=3, sticky="w", pady=(12, 5), padx=(6, 0)
        )
        self.enhancement_combo = ttk.Combobox(
            options,
            textvariable=self.enhancement_label,
            values=list(SUPER_RESOLUTION_MODES),
            state="readonly",
            style="Modern.TCombobox",
        )
        self.enhancement_combo.grid(row=5, column=0, sticky="ew", padx=(0, 6))
        self.enhancement_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._update_output_settings()
        )
        self.output_quality_combo = ttk.Combobox(
            options,
            textvariable=self.output_quality_label,
            values=list(OUTPUT_QUALITIES),
            state="readonly",
            style="Modern.TCombobox",
        )
        self.output_quality_combo.grid(row=5, column=1, sticky="ew", padx=6)
        self.output_quality_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._update_output_settings()
        )
        self.output_fps_combo = ttk.Combobox(
            options,
            textvariable=self.output_fps_label,
            values=list(OUTPUT_FRAME_RATES),
            state="readonly",
            style="Modern.TCombobox",
        )
        self.output_fps_combo.grid(row=5, column=2, sticky="ew", padx=6)
        self.output_fps_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._update_output_settings()
        )
        self.output_height_combo = ttk.Combobox(
            options,
            textvariable=self.output_height_label,
            values=list(OUTPUT_HEIGHTS),
            state="readonly",
            style="Modern.TCombobox",
        )
        self.output_height_combo.grid(row=5, column=3, sticky="ew", padx=(6, 0))
        self.output_height_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._update_output_settings()
        )
        ttk.Label(options, textvariable=self.output_hint, style="Muted.TLabel").grid(
            row=6, column=0, columnspan=4, sticky="w", pady=(7, 0)
        )
        ttk.Label(options, text="项目输出文件夹", style="Field.TLabel").grid(
            row=7, column=0, sticky="w", pady=(14, 5), padx=(0, 6)
        )
        self.output_directory_entry = ttk.Entry(
            options,
            textvariable=self.output_directory,
            style="Modern.TEntry",
        )
        self.output_directory_entry.grid(row=8, column=0, columnspan=3, sticky="ew", padx=(0, 6))
        ttk.Button(
            options,
            text="选择位置",
            style="Secondary.TButton",
            command=self._choose_output_directory,
        ).grid(row=8, column=3, sticky="ew", padx=(6, 0))
        ttk.Label(options, textvariable=self.output_directory_hint, style="Muted.TLabel").grid(
            row=9, column=0, columnspan=4, sticky="w", pady=(7, 0)
        )

        self.api_frame = ttk.Frame(
            outer,
            style="Card.TFrame",
            padding=(22, 12, 22, 14),
        )
        self.api_frame.grid(row=3, column=0, sticky="ew", padx=24, pady=(8, 0))
        for column in range(3):
            self.api_frame.columnconfigure(column, weight=1, uniform="api")
        ttk.Label(self.api_frame, text="接口地址", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        ttk.Label(self.api_frame, text="模型名称", style="Field.TLabel").grid(
            row=0, column=1, sticky="w", padx=6
        )
        ttk.Label(self.api_frame, text="API Key（不会保存）", style="Field.TLabel").grid(
            row=0, column=2, sticky="w", padx=(6, 0)
        )
        self.endpoint_entry = ttk.Entry(
            self.api_frame, textvariable=self.endpoint, style="Modern.TEntry"
        )
        self.endpoint_entry.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(5, 0))
        self.model_entry = ttk.Entry(self.api_frame, textvariable=self.model, style="Modern.TEntry")
        self.model_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=(5, 0))
        self.key_entry = ttk.Entry(
            self.api_frame, textvariable=self.api_key, show="●", style="Modern.TEntry"
        )
        self.key_entry.grid(row=1, column=2, sticky="ew", padx=(6, 0), pady=(5, 0))

        confirmation = self.confirmation_frame = ttk.Frame(
            outer, style="Card.TFrame", padding=(22, 10, 22, 12)
        )
        confirmation.grid(row=4, column=0, sticky="ew", padx=24, pady=(10, 0))
        confirmation.columnconfigure(1, weight=1)
        ttk.Label(confirmation, text="版权依据", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.rights_basis_combo = ttk.Combobox(
            confirmation,
            textvariable=self.rights_basis_label,
            values=list(RIGHTS_BASES),
            state="readonly",
            style="Modern.TCombobox",
            width=25,
        )
        self.rights_basis_combo.grid(row=0, column=1, sticky="w")
        self.rights_basis_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._update_rights_summary()
        )
        ttk.Button(
            confirmation,
            text="填写版权详情",
            style="Secondary.TButton",
            command=self._show_rights_details,
        ).grid(row=0, column=2, sticky="e", padx=(8, 0))
        ttk.Label(
            confirmation,
            textvariable=self.rights_summary,
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(5, 3))
        ttk.Checkbutton(
            confirmation,
            text="我确认拥有该视频，或已取得下载、翻译和再发布所需的授权/许可。",
            variable=self.authorized,
            style="Card.TCheckbutton",
        ).grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(
            confirmation,
            text="若项目已存在，继续上次未完成的处理",
            variable=self.resume,
            style="Card.TCheckbutton",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))

        actions = self.actions_frame = ttk.Frame(
            outer,
            style="Card.TFrame",
            padding=(18, 13),
        )
        actions.grid(row=5, column=0, sticky="ew", padx=24, pady=(12, 10))
        self.start_button = ttk.Button(
            actions,
            text="开始本地化",
            style="Primary.TButton",
            command=self._start,
        )
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            actions,
            text="停止任务",
            style="Danger.TButton",
            command=self._stop,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=(8, 0))
        self.retry_failed_button = ttk.Button(
            actions,
            text="重试未完成任务",
            style="Secondary.TButton",
            command=self._retry_unfinished,
            state="disabled",
        )
        self.retry_failed_button.pack(side="left", padx=(8, 0))
        ttk.Button(
            actions,
            text="调整处理设置",
            style="Secondary.TButton",
            command=self._show_settings,
        ).pack(side="right")

        status_card = ttk.Frame(outer, style="Card.TFrame", padding=(14, 10))
        status_card.grid(row=6, column=0, sticky="ew", padx=24, pady=(0, 10))
        self.status_card = status_card
        status_card.columnconfigure(1, weight=1)
        self.status_dot = tk.Label(
            status_card,
            text="●",
            background=SURFACE,
            foreground=MUTED,
            font=("Segoe UI Symbol", 10),
        )
        self.status_dot.grid(row=0, column=0, sticky="w", padx=(0, 7))
        ttk.Label(status_card, textvariable=self.status, style="Card.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        self.log_button = ttk.Button(
            status_card,
            text="显示运行记录",
            style="CardToolbar.TButton",
            command=self._toggle_log,
        )
        self.log_button.grid(row=0, column=2, sticky="e")
        self.progress = ttk.Progressbar(
            status_card, style="Accent.Horizontal.TProgressbar", mode="determinate", maximum=100
        )
        self.progress.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        log_card = self.log_card = ttk.Frame(outer, style="Card.TFrame", padding=(18, 10, 18, 14))
        log_card.grid(row=7, column=0, sticky="nsew", padx=24, pady=(0, 16))
        log_card.rowconfigure(1, weight=1)
        log_card.columnconfigure(0, weight=1)
        ttk.Label(log_card, text="运行记录", style="SectionTitle.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        ttk.Button(log_card, text="清空", style="Secondary.TButton", command=self._clear_log).grid(
            row=0, column=1, sticky="e", pady=(0, 8)
        )
        self.log = scrolledtext.ScrolledText(
            log_card,
            height=7,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
            background=LOG_BACKGROUND,
            foreground=LOG_TEXT,
            insertbackground=TEXT,
            selectbackground=PRIMARY,
            borderwidth=1,
            highlightthickness=0,
            relief="flat",
            padx=12,
            pady=10,
        )
        self.log.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.settings_panel.grid_remove()
        self.api_frame.grid_remove()
        self.log_card.grid_remove()
        self.paste_button.focus_set()

    def _update_input_state(self, *_args: object) -> None:
        has_input = bool(self.input_value.get().strip())
        current_sources = queue_input_values(self.input_value.get())
        self._pending_start_sources = retain_pending_start_sources(
            self._pending_start_sources, current_sources
        )
        self._sync_task_sources()
        task_frames = (
            self.input_frame,
            self.confirmation_frame,
            self.actions_frame,
            self.status_card,
        )
        if has_input:
            self.empty_state.grid_remove()
            for frame in task_frames:
                frame.grid()
            if self.settings_visible:
                self.input_frame.grid_remove()
                self.settings_panel.grid()
            else:
                self.input_frame.grid()
                self.settings_panel.grid_remove()
            if self.log_visible:
                self.log_card.grid()
            else:
                self.log_card.grid_remove()
            self.input_entry.focus_set()
        else:
            for frame in task_frames:
                frame.grid_remove()
            self.log_card.grid_remove()
            if self.settings_visible:
                self.empty_state.grid_remove()
                self.settings_panel.grid()
            else:
                self.settings_panel.grid_remove()
                self.empty_state.grid()

        provider = TRANSLATION_MODES[self.translation_label.get()]
        automatic = provider == "openai-compatible"
        if self.settings_visible and automatic:
            self.api_frame.grid()
        else:
            self.api_frame.grid_remove()
        if has_input:
            self._update_translation_fields()

    @staticmethod
    def _task_source_label(source: str) -> str:
        path = Path(source)
        label = path.name if path.suffix and "://" not in source else source
        return label if len(label) <= 74 else f"{label[:71]}…"

    def _replace_task_sources(self, sources: list[str], *, schedule_analysis: bool) -> None:
        previous_snapshots: dict[str, DesktopTaskSnapshot] = {}
        for index, source in enumerate(self._task_sources, start=1):
            iid = str(index)
            if not self.task_tree.exists(iid):
                continue
            values = list(self.task_tree.item(iid, "values"))
            progress_text = str(values[3]).rstrip("%") if len(values) > 3 else "0"
            try:
                progress = float(progress_text)
            except ValueError:
                progress = self._task_progress.get(index, 0.0)
            project = self._project_paths_by_queue_index.get(index)
            previous_snapshots[source] = DesktopTaskSnapshot(
                source=source,
                title=str(values[0]) if values else "",
                media_summary=str(values[1]) if len(values) > 1 else "",
                state=self._task_states.get(index, "pending"),
                progress=progress,
                project_path=str(project) if project else "",
                error=self._task_errors.get(index, ""),
            )
        previous_previews = {
            source: self._media_previews[index]
            for index, source in enumerate(self._task_sources, start=1)
            if index in self._media_previews
        }
        previous_errors = {
            source: self._media_preview_errors[index]
            for index, source in enumerate(self._task_sources, start=1)
            if index in self._media_preview_errors
        }
        self._analysis_generation += 1
        if self._analysis_after_id is not None:
            self.root.after_cancel(self._analysis_after_id)
            self._analysis_after_id = None
        self._task_sources = list(sources)
        self._task_states = {}
        self._task_errors = {}
        self._media_previews = {}
        self._media_preview_errors = {}
        self._project_paths_by_queue_index = {}
        for item in self.task_tree.get_children():
            self.task_tree.delete(item)
        self.task_tree.configure(height=task_tree_visible_rows(len(sources)))
        for index, source in enumerate(sources, start=1):
            preview = previous_previews.get(source)
            error = previous_errors.get(source)
            snapshot = previous_snapshots.get(source) or self._restored_task_snapshots.get(source)
            self._restored_task_snapshots.pop(source, None)
            if preview is not None:
                self._media_previews[index] = preview
                title = preview.title
                media = media_preview_summary(preview)
                state_code = snapshot.state if snapshot else "ready"
            elif error is not None:
                self._media_preview_errors[index] = error
                title = self._task_source_label(source)
                media = error
                state_code = snapshot.state if snapshot else "ready"
            elif snapshot is not None and (snapshot.title or snapshot.media_summary):
                title = snapshot.title or self._task_source_label(source)
                media = snapshot.media_summary or "媒体信息将在继续时刷新"
                state_code = snapshot.state
            else:
                title = self._task_source_label(source)
                media = "等待读取媒体信息"
                state_code = "pending"
            progress = snapshot.progress if snapshot else 0.0
            if snapshot and snapshot.error:
                self._task_errors[index] = snapshot.error
            if snapshot and snapshot.project_path:
                self._project_paths_by_queue_index[index] = Path(snapshot.project_path)
            self._task_states[index] = state_code
            self.task_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(title, media, TASK_STATE_LABELS[state_code], f"{progress:.0f}%"),
            )
        self._update_task_action_states()
        self._schedule_queue_save()
        if sources and schedule_analysis:
            generation = self._analysis_generation
            self._analysis_after_id = self.root.after(
                450,
                lambda: self._begin_media_analysis(generation, list(sources)),
            )

    def _sync_task_sources(self) -> None:
        sources = queue_input_values(self.input_value.get())
        if sources == self._task_sources:
            return
        processing = self._has_active_processes() or (self.worker and self.worker.is_alive())
        self._replace_task_sources(sources, schedule_analysis=not processing)

    def _begin_media_analysis(self, generation: int, sources: list[str]) -> None:
        self._analysis_after_id = None
        if generation != self._analysis_generation or not sources:
            return

        def worker() -> None:
            for index, source in enumerate(sources, start=1):
                if generation != self._analysis_generation:
                    return
                state = self._task_states.get(index)
                if state in {"paused", "failed", "completed"}:
                    continue
                self.events.put(("media_analysis_started", (generation, index, source)))
                try:
                    preview = inspect_media_preview(source)
                except (OSError, ValueError, LocalizerError) as exc:
                    message = " ".join(str(exc).split())
                    if len(message) > 150:
                        message = f"{message[:147]}…"
                    self.events.put(
                        ("media_analysis_result", (generation, index, None, message))
                    )
                else:
                    self.events.put(
                        ("media_analysis_result", (generation, index, preview, None))
                    )
            self.events.put(("media_analysis_done", (generation, len(sources))))

        self._analysis_worker = threading.Thread(
            target=worker,
            daemon=True,
            name=f"localizer-media-analysis-{generation}",
        )
        self._analysis_worker.start()

    def _update_task_row(
        self,
        index: int,
        *,
        title: str | None = None,
        media: str | None = None,
        status: str | None = None,
        progress: str | None = None,
        state_code: str | None = None,
        error: str | None = None,
    ) -> None:
        iid = str(index)
        if not self.task_tree.exists(iid):
            return
        values = list(self.task_tree.item(iid, "values"))
        if state_code is not None:
            if state_code not in TASK_STATE_LABELS:
                raise ValueError(f"Unknown desktop task state: {state_code}")
            self._task_states[index] = state_code
            if status is None:
                status = TASK_STATE_LABELS[state_code]
        if error is not None:
            if error:
                self._task_errors[index] = error
            else:
                self._task_errors.pop(index, None)
        replacements = (title, media, status, progress)
        for position, replacement in enumerate(replacements):
            if replacement is not None:
                values[position] = replacement
        self.task_tree.item(iid, values=values)
        self._schedule_queue_save()

    def _schedule_queue_save(self) -> None:
        if self._queue_save_after_id is None:
            self._queue_save_after_id = self.root.after(500, self._save_queue_snapshot)

    def _save_queue_snapshot(self) -> None:
        self._queue_save_after_id = None
        if not self._task_sources:
            with suppress(OSError):
                clear_desktop_queue()
            return
        tasks: list[DesktopTaskSnapshot] = []
        for index, source in enumerate(self._task_sources, start=1):
            iid = str(index)
            if not self.task_tree.exists(iid):
                continue
            values = list(self.task_tree.item(iid, "values"))
            progress_text = str(values[3]).rstrip("%") if len(values) > 3 else "0"
            try:
                progress = float(progress_text)
            except ValueError:
                progress = self._task_progress.get(index, 0.0)
            project = self._project_paths_by_queue_index.get(index)
            tasks.append(
                DesktopTaskSnapshot(
                    source=source,
                    title=str(values[0]) if values else "",
                    media_summary=str(values[1]) if len(values) > 1 else "",
                    state=self._task_states.get(index, "pending"),
                    progress=progress,
                    project_path=str(project) if project else "",
                    error=self._task_errors.get(index, ""),
                )
            )
        with suppress(OSError):
            save_desktop_queue(DesktopQueueSnapshot(tasks=tuple(tasks)))

    def _selected_task_index(self) -> int | None:
        selected = self.task_tree.selection()
        if not selected:
            return None
        try:
            return int(selected[0])
        except ValueError:
            return None

    def _update_task_action_states(self, _event: object | None = None) -> None:
        index = self._selected_task_index()
        processing = self._has_active_processes() or (self.worker and self.worker.is_alive())
        state = self._task_states.get(index or -1, "pending")
        self.remove_selected_button.configure(
            state="normal" if index is not None and not processing else "disabled"
        )
        has_project = index is not None and index in self._project_paths_by_queue_index
        self.open_task_button.configure(state="normal" if has_project else "disabled")
        self.pause_selected_button.configure(
            state=(
                "normal"
                if index is not None
                and state == "running"
                and index in self._processes_by_queue_index
                else "disabled"
            )
        )
        self.retry_selected_button.configure(
            state=(
                "normal"
                if index is not None and not processing and state in {"paused", "failed"}
                else "disabled"
            )
        )
        rendered = self._selected_rendered_path(index)
        self.open_rendered_button.configure(
            state="normal" if rendered is not None else "disabled"
        )

    def _remove_selected_task(self) -> None:
        index = self._selected_task_index()
        if index is None:
            return
        if self._has_active_processes() or (self.worker and self.worker.is_alive()):
            return
        remaining = [
            source
            for task_index, source in enumerate(self._task_sources, start=1)
            if task_index != index
        ]
        self.input_value.set("\n".join(remaining))
        if not remaining:
            self.authorized.set(False)

    def _open_selected_task_project(self) -> None:
        index = self._selected_task_index()
        project = self._project_paths_by_queue_index.get(index or -1)
        if project is None or not project.exists():
            messagebox.showinfo(
                "项目尚未创建",
                "该任务开始处理后，便可从这里打开它的独立项目文件夹。",
                parent=self.root,
            )
            return
        self._open_local_path(project)

    def _selected_rendered_path(self, index: int | None) -> Path | None:
        project = self._project_paths_by_queue_index.get(index or -1)
        if project is None:
            return None
        rendered = project / "rendered"
        if not rendered.is_dir():
            return None
        videos = [path for path in rendered.glob("*_hardsub.mp4") if path.is_file()]
        if not videos:
            videos = [path for path in rendered.glob("*_softsub.mp4") if path.is_file()]
        try:
            return max(videos, key=lambda path: path.stat().st_mtime_ns) if videos else None
        except OSError:
            return None

    @staticmethod
    def _open_local_path(path: Path) -> None:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _open_selected_rendered(self) -> None:
        rendered = self._selected_rendered_path(self._selected_task_index())
        if rendered is None:
            messagebox.showinfo(
                "成片尚未生成",
                "该任务完成字幕压制后，可从这里直接打开最终 MP4。",
                parent=self.root,
            )
            return
        self._open_local_path(rendered)

    def _pause_selected_task(self) -> None:
        index = self._selected_task_index()
        if index is None:
            return
        with self._process_lock:
            process = self._processes_by_queue_index.get(index)
        if process is None or process.poll() is not None:
            return
        if not messagebox.askyesno(
            "暂停这个任务",
            "会安全停止所选任务的当前进程；已完成阶段和下载片段都会保留。其他任务继续运行。",
            parent=self.root,
        ):
            return
        self._pause_requested_indices.add(index)
        self._update_task_row(index, status="正在暂停…", state_code="running")
        terminate_process_tree(process)

    def _retry_selected_task(self) -> None:
        index = self._selected_task_index()
        if index is None or self._task_states.get(index) not in {"paused", "failed"}:
            return
        if self._has_active_processes() or (self.worker and self.worker.is_alive()):
            return
        try:
            commands, environment, provider = self._validate()
        except (KeyError, ValueError) as exc:
            messagebox.showwarning("还不能继续", str(exc), parent=self.root)
            return
        if not 1 <= index <= len(commands):
            return
        self.active_direction = TRANSLATION_DIRECTIONS[self.direction_label.get()]
        self._save_desktop_settings()
        self._begin_queue(
            [commands[index - 1]],
            environment,
            provider,
            retrying=True,
            task_indices=(index,),
            preserve_task_center=True,
        )

    def _toggle_settings(self) -> None:
        self.settings_visible = not self.settings_visible
        if self.settings_visible:
            self.log_visible = False
            self.log_button.configure(text="显示运行记录")
        self.settings_button.configure(text="收起设置" if self.settings_visible else "处理设置")
        self._update_input_state()

    def _show_settings(self) -> None:
        if not self.settings_visible:
            self._toggle_settings()

    def _open_help_center(self) -> None:
        if self.help_center is not None and self.help_center.window.winfo_exists():
            self.help_center.window.lift()
            self.help_center.window.focus_force()
            return
        self.help_center = HelpCenterDialog(
            self.root,
            on_select_workflow=self._apply_setup_workflow,
            output_directory=Path(
                self.output_directory.get().strip() or default_output_directory()
            ).expanduser(),
        )

    def _open_direct_media_dialog(self) -> None:
        if self._has_active_processes() or (self.worker and self.worker.is_alive()):
            messagebox.showinfo("任务进行中", "请先停止当前任务，再修改视频队列。", parent=self.root)
            return
        if self.direct_media_dialog is not None and self.direct_media_dialog.window.winfo_exists():
            self.direct_media_dialog.window.lift()
            self.direct_media_dialog.window.focus_force()
            return
        self.direct_media_dialog = DirectMediaLinkDialog(
            self.root,
            on_add=self._add_direct_media_links,
        )

    def _add_direct_media_links(self, links: list[str]) -> None:
        combined = queue_input_values("\n".join([self.input_value.get(), *links]))
        self.input_value.set("\n".join(combined))
        self.input_entry.icursor("end")
        self._set_status(f"已加入 {len(links)} 条媒体直链，正在验证有效性…", "active")

    def _open_browser_capture_dialog(
        self, initial_url: str | None = None, *, auto_start: bool = False
    ) -> None:
        if self._has_active_processes() or (self.worker and self.worker.is_alive()):
            messagebox.showinfo("任务进行中", "请先停止当前任务，再抓取新的播放页。", parent=self.root)
            return
        if (
            self.browser_capture_dialog is not None
            and self.browser_capture_dialog.window.winfo_exists()
        ):
            self.browser_capture_dialog.window.lift()
            self.browser_capture_dialog.window.focus_force()
            return
        selected_url = initial_url if initial_url is not None else self.input_value.get().strip()
        self.browser_capture_dialog = BrowserCaptureDialog(
            self.root,
            initial_url=selected_url,
            on_capture=self._add_browser_capture,
            on_cancel=lambda page=selected_url: self._cancel_pending_browser_capture(page),
            auto_start=auto_start,
        )

    def _cancel_pending_browser_capture(self, page_url: str) -> None:
        self._pending_start_sources = None
        self._browser_capture_prompted_sources.discard(page_url)
        self._set_status("已取消浏览器抓取；任务尚未开始。", "muted")

    def _add_browser_capture(self, capture: BrowserMediaCapture) -> None:
        sources = queue_input_values(self.input_value.get())
        replaced = False
        updated: list[str] = []
        for source in sources:
            if source == capture.page_url:
                if capture.media_url not in updated:
                    updated.append(capture.media_url)
                replaced = True
            elif source not in updated:
                updated.append(source)
        if not replaced and capture.media_url not in updated:
            updated.append(capture.media_url)
        self._pending_start_sources = continued_start_sources_after_capture(
            self._pending_start_sources,
            page_url=capture.page_url,
            updated_sources=updated,
        )
        self.input_value.set("\n".join(updated))
        self.input_entry.icursor("end")
        self._set_status("浏览器已捕获完整媒体地址，正在验证并读取画质信息…", "active")

    def _apply_setup_workflow(self, workflow: str) -> None:
        if workflow == "download":
            self.subtitle_label.set("仅下载原视频（无字幕）")
        else:
            self.subtitle_label.set("仅目标语言字幕")
            self._show_settings()
        self._update_translation_fields()

    def _open_subtitle_review(self) -> None:
        from tkinter import filedialog

        from .pipeline import load_project_config

        selected = filedialog.askdirectory(
            parent=self.root,
            title="选择要审核字幕的项目文件夹",
            initialdir=self.output_directory.get().strip() or str(default_output_directory()),
            mustexist=True,
        )
        if not selected:
            return
        project = ProjectPaths(Path(selected).resolve())
        if not project.state_file.is_file():
            messagebox.showwarning(
                "不是项目文件夹",
                "请选择包含 pipeline_state.json 的项目文件夹。",
                parent=self.root,
            )
            return
        try:
            config = load_project_config(project)
            session = load_subtitle_review_session(project, config)
        except (OSError, ValueError, LocalizerError) as exc:
            messagebox.showerror("无法打开字幕审核", str(exc), parent=self.root)
            return
        SubtitleReviewDialog(self.root, session, config, on_preview=self._request_review_preview)

    def _request_review_preview(
        self,
        session: SubtitleReviewSession,
        config: AppConfig,
        start_seconds: float,
        callback: Callable[[Path | None, str | None], None],
    ) -> None:
        def worker() -> None:
            try:
                output = render_subtitle_review_preview(session, config, start_seconds=start_seconds)
            except (OSError, ValueError, LocalizerError) as exc:
                self.events.put(("review_preview", (callback, None, str(exc))))
            else:
                self.events.put(("review_preview", (callback, output, None)))

        threading.Thread(target=worker, daemon=True, name="localizer-review-preview").start()

    def _begin_update_check(self) -> None:
        self.update_button.configure(state="disabled", text="正在检查…")
        channel = UPDATE_CHANNELS[self.update_channel_label.get()]

        def worker() -> None:
            self.events.put(("update", check_for_update(channel=channel)))

        threading.Thread(target=worker, daemon=True, name="localizer-update-check").start()

    def _show_update_result(self, result: ReleaseCheck) -> None:
        self.update_button.configure(state="normal", text="检查更新")
        channel_name = "开发通道" if result.channel == "development" else "稳定通道"
        if result.status == "available":
            should_open = messagebox.askyesno(
                "发现新版本",
                f"更新通道：{channel_name}\n当前版本：v{result.current_version}\n"
                f"最新版本：v{result.latest_version}\n\n是否打开官方下载页？",
                parent=self.root,
            )
            if should_open and result.release_url:
                webbrowser.open(result.release_url)
            return
        if result.status == "current":
            messagebox.showinfo(
                "已经是最新版本",
                f"当前版本 v{result.current_version} 已是{channel_name}的最新版本。",
                parent=self.root,
            )
            return
        messagebox.showwarning(
            "暂时无法检查更新",
            "无法连接公开发布页。请稍后重试，或通过 GitHub Releases 手动查看版本。\n\n"
            f"技术信息：{result.detail}",
            parent=self.root,
        )

    def _toggle_log(self) -> None:
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.settings_visible = False
            self.settings_button.configure(text="处理设置")
        self.log_button.configure(text="隐藏运行记录" if self.log_visible else "显示运行记录")
        self._update_input_state()

    def _show_log(self) -> None:
        if not self.log_visible:
            self.log_visible = True
            self.log_button.configure(text="隐藏运行记录")
        self.settings_visible = False
        self.settings_button.configure(text="处理设置")
        self._update_input_state()

    def _has_active_processes(self) -> bool:
        with self._process_lock:
            return any(process.poll() is None for process in self._active_processes)

    def _clear_input(self) -> None:
        if self._has_active_processes() or (self.worker and self.worker.is_alive()):
            messagebox.showinfo("任务进行中", "请先停止当前任务，再移除视频。", parent=self.root)
            return
        self.input_value.set("")
        self.authorized.set(False)
        self.rights_basis_label.set("本人／本团队原创")
        for variable in (
            self.rights_holder,
            self.rights_license_url,
            self.rights_permission_reference,
            self.rights_attribution_text,
        ):
            variable.set("")
        self.rights_allow_translation.set(True)
        self.rights_allow_redistribution.set(True)
        self.rights_commercial_use.set(False)
        self._update_rights_summary()

    def _paste(self) -> None:
        if self._has_active_processes() or (self.worker and self.worker.is_alive()):
            messagebox.showinfo("任务进行中", "请先停止当前任务，再修改视频队列。", parent=self.root)
            return
        try:
            value = self.root.clipboard_get().strip()
        except tk.TclError:
            messagebox.showinfo(
                "剪贴板为空",
                "请先复制 YouTube、公开播放页或直接媒体地址。",
                parent=self.root,
            )
            return
        self.input_value.set(value)
        self.input_entry.icursor("end")

    def _choose_local_file(self) -> None:
        if self._has_active_processes() or (self.worker and self.worker.is_alive()):
            messagebox.showinfo("任务进行中", "请先停止当前任务，再修改视频队列。", parent=self.root)
            return
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择本地视频",
            filetypes=[
                ("视频文件", "*.mp4 *.mkv *.mov *.webm *.avi *.m4v"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self.input_value.set(path)

    def _choose_output_directory(self) -> None:
        from tkinter import filedialog

        initial = self.output_directory.get().strip() or str(default_output_directory())
        path = filedialog.askdirectory(
            parent=self.root,
            title="选择项目输出文件夹",
            initialdir=initial,
            mustexist=False,
        )
        if path:
            self.output_directory.set(path)

    def _select_subtitle_font_size(self, _event: object | None = None) -> None:
        selected = self.font_size_label.get()
        if selected in SUBTITLE_FONT_SIZES:
            self.subtitle_font_size_value.set(SUBTITLE_FONT_SIZES[selected])

    def _apply_subtitle_layout(self, x_percent: int, y_percent: int, font_size: int) -> None:
        x_percent, y_percent, font_size = clamp_subtitle_preview_values(
            x_percent, y_percent, font_size
        )
        self.subtitle_position_x.set(x_percent)
        self.subtitle_position_y.set(y_percent)
        self.subtitle_font_size_value.set(font_size)
        matching_label = next(
            (label for label, value in SUBTITLE_FONT_SIZES.items() if value == font_size),
            f"自定义（{font_size}）",
        )
        self.font_size_label.set(matching_label)
        values = list(SUBTITLE_FONT_SIZES)
        if matching_label not in values:
            values.append(matching_label)
        self.font_size_combo.configure(values=values)

    def _show_subtitle_preview(self) -> None:
        subtitle_mode = SUBTITLE_MODES[self.subtitle_label.get()]
        if subtitle_mode == "download_only":
            messagebox.showinfo("无需字幕预览", "当前为无字幕下载模式。", parent=self.root)
            return
        SubtitlePreviewDialog(
            self.root,
            subtitle_mode=subtitle_mode,
            x_percent=self.subtitle_position_x.get(),
            y_percent=self.subtitle_position_y.get(),
            font_size=self.subtitle_font_size_value.get(),
            on_apply=self._apply_subtitle_layout,
        )

    def _update_translation_fields(self) -> None:
        subtitle_mode = SUBTITLE_MODES[self.subtitle_label.get()]
        direction = TRANSLATION_DIRECTIONS[self.direction_label.get()]
        requires_capable_translator = requires_local_ai_or_api(direction)
        allowed_modes = (
            [label for label, value in TRANSLATION_MODES.items() if value in {"ollama", "openai-compatible"}]
            if requires_capable_translator
            else list(TRANSLATION_MODES)
        )
        self.translation_combo.configure(values=allowed_modes)
        if self.translation_label.get() not in allowed_modes:
            self.translation_label.set(
                "本地 AI 段落翻译并压制（高质量，无 API Key）"
                if local_ai_available()
                else "自动翻译并压制字幕（需要 API）"
            )
        allowed_subtitle_modes = (
            [label for label, value in SUBTITLE_MODES.items() if value in {"download_only", "chinese"}]
            if requires_capable_translator
            else list(SUBTITLE_MODES)
        )
        self.subtitle_combo.configure(values=allowed_subtitle_modes)
        if self.subtitle_label.get() not in allowed_subtitle_modes:
            self.subtitle_label.set("仅目标语言字幕")
            subtitle_mode = "chinese"
        provider = TRANSLATION_MODES[self.translation_label.get()]
        download_only = subtitle_mode == "download_only"
        enhancement_mode = SUPER_RESOLUTION_MODES[self.enhancement_label.get()]
        selection_state = "disabled" if download_only else "readonly"
        self.direction_combo.configure(state=selection_state)
        self.font_size_combo.configure(state=selection_state)
        self.preview_button.configure(state="disabled" if download_only else "normal")
        self.translation_combo.configure(state=selection_state)
        render_controls_state = (
            "readonly" if not download_only or enhancement_mode != "off" else "disabled"
        )
        for combo in (self.output_quality_combo, self.output_fps_combo, self.output_height_combo):
            combo.configure(state=render_controls_state)
        self.enhancement_combo.configure(state="readonly")
        self.start_button.configure(text="开始下载" if download_only else "开始本地化")
        hint = mode_description(subtitle_mode, provider)
        if requires_capable_translator and not download_only:
            hint += " 其他语种需要本地 AI（Ollama）或 API；不支持快速离线模型。"
        self.mode_hint.set(hint)
        if download_only:
            summary = "当前方案：最高源画质 · 最高源帧率 · 最佳音频 · 不生成字幕"
        else:
            provider_names = {
                "manual": "人工翻译",
                "offline": "本地快速翻译",
                "ollama": "本地 AI 段落翻译",
                "openai-compatible": "API 自动翻译",
            }
            summary = (
                f"当前方案：{self.direction_label.get()} · {self.subtitle_label.get()} · "
                f"{provider_names[provider]} · 自动硬件加速 · {self.output_quality_label.get()}"
            )
        if enhancement_mode != "off":
            enhancement_name = "通用实拍" if enhancement_mode == "general" else "动画"
            summary += f" · AI {enhancement_name}超分（本地 Vulkan）"
        queue_count = len(queue_input_values(self.input_value.get()))
        if queue_count > 1:
            parallel = gui_parallel_job_limit(queue_count)
            summary += f" · 已排队 {queue_count} 个视频（当前电脑最多 {parallel} 个并行，重负载自动排队）"
        self.workflow_summary.set(summary)
        automatic = not download_only and provider == "openai-compatible"
        if automatic and self.settings_visible:
            self.api_frame.grid()
        else:
            self.api_frame.grid_remove()
        state = "normal" if automatic else "disabled"
        for entry in (self.endpoint_entry, self.model_entry, self.key_entry):
            entry.configure(state=state)

    def _update_output_settings(self) -> None:
        enhancement_mode = SUPER_RESOLUTION_MODES[self.enhancement_label.get()]
        if enhancement_mode == "off":
            self.output_hint.set(
                "保持原始表示不降分辨率或帧率；选择 60/30 FPS 只会降低更高帧率，绝不补帧。"
            )
        else:
            self.output_hint.set(
                "AI 超分会逐帧恢复并放大，默认 2 倍且最高 4K；耗时和磁盘占用会明显增加，"
                "无法凭空恢复原视频从未记录的真实细节。"
            )
        self._update_output_directory_hint()
        self._update_translation_fields()

    def _update_output_directory_hint(self, *_args: object) -> None:
        raw_directory = self.output_directory.get().strip()
        if not raw_directory:
            self.output_directory_hint.set("请选择一个本地磁盘位置以保存项目和渲染文件。")
            return
        self.output_directory_hint.set(output_directory_status_hint(Path(raw_directory)))

    def _rights_config(self) -> RightsConfig:
        return RightsConfig(
            basis=RIGHTS_BASES[self.rights_basis_label.get()],
            rights_holder=self.rights_holder.get().strip(),
            license_url=self.rights_license_url.get().strip(),
            permission_reference=self.rights_permission_reference.get().strip(),
            attribution_text=self.rights_attribution_text.get().strip(),
            allow_translation=self.rights_allow_translation.get(),
            allow_redistribution=self.rights_allow_redistribution.get(),
            commercial_use=self.rights_commercial_use.get(),
        )

    def _show_rights_details(self) -> None:
        RightsDetailsDialog(
            self.root,
            basis=RIGHTS_BASES[self.rights_basis_label.get()],
            values={
                "rights_holder": self.rights_holder.get(),
                "license_url": self.rights_license_url.get(),
                "permission_reference": self.rights_permission_reference.get(),
                "attribution_text": self.rights_attribution_text.get(),
                "allow_translation": self.rights_allow_translation.get(),
                "allow_redistribution": self.rights_allow_redistribution.get(),
                "commercial_use": self.rights_commercial_use.get(),
            },
            on_apply=self._apply_rights_details,
        )

    def _apply_rights_details(self, values: dict[str, object]) -> None:
        self.rights_holder.set(str(values.get("rights_holder", "")))
        self.rights_license_url.set(str(values.get("license_url", "")))
        self.rights_permission_reference.set(str(values.get("permission_reference", "")))
        self.rights_attribution_text.set(str(values.get("attribution_text", "")))
        self.rights_allow_translation.set(bool(values.get("allow_translation", True)))
        self.rights_allow_redistribution.set(bool(values.get("allow_redistribution", True)))
        self.rights_commercial_use.set(bool(values.get("commercial_use", False)))
        self._update_rights_summary()

    def _update_rights_summary(self) -> None:
        basis = RIGHTS_BASES[self.rights_basis_label.get()]
        if basis == "cc_by_nc" and self.rights_commercial_use.get():
            self.rights_commercial_use.set(False)
        config = self._rights_config()
        issues = validate_rights(config, strict=True)
        if issues:
            self.rights_summary.set("版权记录待补充：" + " ".join(issues))
        else:
            self.rights_summary.set(
                f"版权记录已填写：{RIGHTS_BASIS_LABELS[basis]}；处理后会随项目生成可审计记录。"
            )

    def _validate(self) -> tuple[list[list[str]], dict[str, str], str]:
        if not self.authorized.get():
            raise ValueError("开始前请确认你拥有视频或已取得所需授权。")
        subtitle_mode = SUBTITLE_MODES[self.subtitle_label.get()]
        provider = TRANSLATION_MODES[self.translation_label.get()]
        enhancement_mode = SUPER_RESOLUTION_MODES[self.enhancement_label.get()]
        rights = self._rights_config()
        if rights_issues := validate_rights(rights, strict=True):
            raise ValueError("版权记录不完整：" + " ".join(rights_issues))
        if message := super_resolution_installation_message(enhancement_mode):
            raise ValueError(message)
        if subtitle_mode != "download_only" and (whisper_message := whisper_model_installation_message()):
            raise ValueError(whisper_message)
        values = queue_input_values(self.input_value.get())
        if not values:
            raise ValueError("请粘贴 YouTube、公开播放页或媒体直链，或选择本地视频文件。")
        commands = [
            build_process_command(
                value,
                subtitle_mode=subtitle_mode,
                translation_provider=provider,
                translation_direction=TRANSLATION_DIRECTIONS[self.direction_label.get()],
                subtitle_font=DEFAULT_SUBTITLE_FONT,
                subtitle_font_size=self.subtitle_font_size_value.get(),
                subtitle_position_x=self.subtitle_position_x.get(),
                subtitle_position_y=self.subtitle_position_y.get(),
                processing_profile="auto",
                output_quality=OUTPUT_QUALITIES[self.output_quality_label.get()],
                output_fps=OUTPUT_FRAME_RATES[self.output_fps_label.get()],
                output_height=OUTPUT_HEIGHTS[self.output_height_label.get()],
                super_resolution=enhancement_mode,
                rights_basis=rights.basis,
                rights_holder=rights.rights_holder,
                license_url=rights.license_url,
                permission_reference=rights.permission_reference,
                attribution_text=rights.attribution_text,
                commercial_use=rights.commercial_use,
                output_directory=self.output_directory.get(),
                resume=self.resume.get(),
            )
            for value in values
        ]
        environment = os.environ.copy()
        if provider == "openai-compatible" and subtitle_mode != "download_only":
            endpoint = self.endpoint.get().strip()
            model = self.model.get().strip()
            api_key = self.api_key.get().strip()
            if not endpoint or not model or not api_key:
                raise ValueError("自动翻译需要填写接口地址、模型名称和 API Key。")
            environment["OPENAI_COMPATIBLE_ENDPOINT"] = endpoint
            environment["OPENAI_COMPATIBLE_MODEL"] = model
            environment["OPENAI_COMPATIBLE_API_KEY"] = api_key
        workflow = "download_only" if subtitle_mode == "download_only" else provider
        return commands, environment, workflow

    def _start(self) -> None:
        if self._has_active_processes() or (self.worker and self.worker.is_alive()):
            return
        try:
            commands, environment, provider = self._validate()
        except (KeyError, ValueError) as exc:
            messagebox.showwarning("还不能开始", str(exc), parent=self.root)
            return

        sources = queue_input_values(self.input_value.get())
        if self._analysis_worker is not None and self._analysis_worker.is_alive():
            self._pending_start_sources = tuple(sources)
            self._set_status("正在完成媒体预分析，完成后会自动继续…", "active")
            return

        capture_source = browser_capture_required_source(sources, self._media_preview_errors)
        if capture_source is not None:
            self._pending_start_sources = tuple(sources)
            self._browser_capture_prompted_sources.add(capture_source)
            self._set_status(
                "该页面必须先通过浏览器抓取完整媒体地址，成功后会自动继续…",
                "active",
            )
            self._open_browser_capture_dialog(capture_source, auto_start=True)
            return

        self._pending_start_sources = None
        self.active_direction = TRANSLATION_DIRECTIONS[self.direction_label.get()]
        self._save_desktop_settings()
        self._begin_queue(commands, environment, provider)

    def _save_desktop_settings(self) -> None:
        """Persist non-secret launcher choices for the next session."""
        try:
            save_desktop_settings(
                DesktopSettings(
                    direction=TRANSLATION_DIRECTIONS[self.direction_label.get()],
                    subtitle_mode=SUBTITLE_MODES[self.subtitle_label.get()],
                    translation_provider=TRANSLATION_MODES[self.translation_label.get()],
                    font_size=self.subtitle_font_size_value.get(),
                    subtitle_x_percent=self.subtitle_position_x.get(),
                    subtitle_y_percent=self.subtitle_position_y.get(),
                    output_quality=OUTPUT_QUALITIES[self.output_quality_label.get()],
                    output_fps=OUTPUT_FRAME_RATES[self.output_fps_label.get()],
                    output_height=OUTPUT_HEIGHTS[self.output_height_label.get()],
                    enhancement_mode=SUPER_RESOLUTION_MODES[self.enhancement_label.get()],
                    output_directory=(
                        self.output_directory.get().strip() or str(default_output_directory())
                    ),
                    update_channel=UPDATE_CHANNELS[self.update_channel_label.get()],
                    resume=self.resume.get(),
                )
            )
        except (KeyError, OSError, ValueError):
            # A read-only or corrupt profile must never prevent processing or shutdown.
            return

    def _begin_queue(
        self,
        commands: list[list[str]],
        environment: dict[str, str],
        provider: str,
        *,
        retrying: bool = False,
        task_indices: tuple[int, ...] | None = None,
        preserve_task_center: bool = False,
    ) -> None:
        """Start a queue attempt and retain only its commands for safe retry."""
        if not commands:
            return

        resolved_task_indices = task_indices or tuple(range(1, len(commands) + 1))
        if len(resolved_task_indices) != len(commands):
            raise ValueError("Each queued command must have one desktop task index.")
        queue_sources = [command[3] for command in commands]
        if not preserve_task_center:
            self._replace_task_sources(queue_sources, schedule_analysis=False)
        for index in resolved_task_indices:
            self._update_task_row(index, state_code="queued", progress="0%", error="")
        self._clear_log()
        self._show_log()
        self.active_queue_index = 1
        self.active_queue_total = len(commands)
        self._queue_parallel_limit = gui_parallel_job_limit(self.active_queue_total)
        self._task_progress = {}
        self._parallel_queue = self.active_queue_total > 1
        if not preserve_task_center:
            self._current_queue_commands = [list(command) for command in commands]
            self._commands_by_task_index = {
                index: list(command)
                for index, command in zip(resolved_task_indices, commands, strict=True)
            }
        else:
            for index, command in zip(resolved_task_indices, commands, strict=True):
                self._commands_by_task_index[index] = list(command)
        self._current_queue_environment = dict(environment)
        self._current_output_directory = Path(
            self.output_directory.get().strip() or default_output_directory()
        ).expanduser()
        self._retry_commands = []
        self._retry_task_indices = ()
        if not preserve_task_center:
            self._project_paths_by_queue_index = {}
        self._pause_requested_indices.difference_update(resolved_task_indices)
        self._diagnostic_projects = set()
        self.retry_failed_button.configure(state="disabled")
        if retrying:
            self._append_log(
                f"正在恢复 {self.active_queue_total} 个未完成任务：已完成阶段会复用，"
                "不会重新下载或压制已有输出。\n\n"
            )
        else:
            self._append_log(
                "正在启动视频下载……\n" if provider == "download_only" else "正在启动本地化处理……\n"
            )
        if self.active_queue_total > 1:
            self._append_log(
                f"已加入 {self.active_queue_total} 个视频：根据当前 CPU 和内存自动启用 "
                f"{self._queue_parallel_limit} 个下载/预处理任务并行，"
                "Whisper、本地 AI 与压制阶段会自动排队以保护显存、CPU 和磁盘。\n\n"
            )
        self._append_log(f"项目输出位置：{self.output_directory.get().strip()}\n")
        if retrying:
            pass
        elif provider == "download_only":
            self._append_log(
                "当前为无字幕直接下载：只下载并合并最高画质视频和最高质量音频，"
                "不会运行语音识别、翻译或字幕压制。\n\n"
            )
        elif provider == "manual":
            source_name = "中文" if self.active_direction == "zh-to-en" else "英文"
            self._append_log(
                f"当前为免费模式：程序会完成下载和{source_name}字幕，然后导出等待翻译的文件。\n\n"
            )
        elif provider == "offline":
            if self.active_direction == "zh-to-en":
                self._append_log(
                    "当前为中文转英文离线模式：使用本地 Whisper 识别中文，再进行中译英。\n\n"
                )
            else:
                self._append_log(
                    "当前为英文转中文离线模式：使用本地 Whisper 识别英文，再进行英译中。\n\n"
                )
        elif provider == "ollama":
            target_name = language_name(self.active_direction, target=True)
            self._append_log(
                f"当前为本地 AI 段落翻译：会先理解完整段落，再自然翻译成{target_name}。"
                "不需要 API Key；离线安装包已内置本地模型。\n\n"
            )
        else:
            target_name = language_name(self.active_direction, target=True)
            self._append_log(f"当前为自动模式：完成翻译后会继续压制{target_name}字幕。\n\n")
        self._set_status("正在恢复未完成任务，请保持窗口打开" if retrying else "正在处理，请保持窗口打开", "active")
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(10)
        self.stop_requested = False
        self.active_provider = provider
        self.active_enhancement = SUPER_RESOLUTION_MODES[self.enhancement_label.get()] != "off"
        self._progress_value = 0.0
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.input_entry.configure(state="disabled")
        self.paste_button.configure(state="disabled")
        self.direct_media_button.configure(state="disabled")
        self.browser_capture_button.configure(state="disabled")
        self.local_file_button.configure(state="disabled")
        self._update_task_action_states()
        self.worker = threading.Thread(
            target=self._run_queue,
            args=(commands, environment, provider, resolved_task_indices),
            daemon=True,
        )
        self.worker.start()

    def _retry_unfinished(self) -> None:
        """Retry only the projects that failed or were interrupted in the last queue attempt."""
        if self._has_active_processes() or (self.worker and self.worker.is_alive()):
            return
        if not self._retry_commands:
            messagebox.showinfo("没有可重试任务", "上一批任务没有未完成项目。", parent=self.root)
            return
        self._begin_queue(
            self._retry_commands,
            self._current_queue_environment,
            self.active_provider,
            retrying=True,
            task_indices=self._retry_task_indices,
            preserve_task_center=True,
        )

    def _run_queue(
        self,
        commands: list[list[str]],
        environment: dict[str, str],
        provider: str,
        task_indices: tuple[int, ...] | None = None,
    ) -> None:
        failures = 0
        completed = 0
        total = len(commands)
        successful_indices: set[int] = set()
        failed_indices: set[int] = set()
        paused_indices: set[int] = set()
        parallel_jobs = min(total, getattr(self, "_queue_parallel_limit", gui_parallel_job_limit(total)))
        resolved_task_indices = task_indices or tuple(range(1, total + 1))
        pending = iter(
            (position, task_index, command)
            for position, (task_index, command) in enumerate(
                zip(resolved_task_indices, commands, strict=True), start=1
            )
        )
        futures: dict[Future[int], tuple[int, int]] = {}

        def submit_next(executor: ThreadPoolExecutor) -> bool:
            if self.stop_requested:
                return False
            try:
                position, task_index, command = next(pending)
            except StopIteration:
                return False
            self.events.put(("queue_item", (task_index, position, total, command[3])))
            future = executor.submit(
                self._run_process,
                command,
                environment,
                task_index,
                position,
                total,
            )
            futures[future] = (task_index, position)
            return True

        with ThreadPoolExecutor(
            max_workers=parallel_jobs,
            thread_name_prefix="localizer-queue",
        ) as executor:
            for _ in range(parallel_jobs):
                if not submit_next(executor):
                    break
            while futures:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    task_index, position = futures.pop(future)
                    completed += 1
                    try:
                        return_code = future.result()
                    except Exception as exc:  # pragma: no cover - unexpected worker failures
                        return_code = 1
                        self.events.put(
                            ("line", (task_index, position, total, f"\n任务内部失败：{exc}\n"))
                        )
                    pause_requested = task_index in self._pause_requested_indices
                    self._pause_requested_indices.discard(task_index)
                    paused = pause_requested and return_code != 0
                    if paused:
                        paused_indices.add(task_index)
                    elif return_code != 0:
                        failures += 1
                        failed_indices.add(task_index)
                    else:
                        successful_indices.add(task_index)
                    self.events.put(
                        ("task_finished", (task_index, position, total, return_code, paused))
                    )
                    if not self.stop_requested:
                        submit_next(executor)
        retry_indices = (
            tuple(
                index for index in resolved_task_indices if index not in successful_indices
            )
            if self.stop_requested
            else tuple(sorted(failed_indices | paused_indices))
        )
        self.events.put(
            (
                "done",
                (
                    1 if failures else 0,
                    provider,
                    completed,
                    total,
                    failures,
                    len(paused_indices),
                    retry_indices,
                ),
            )
        )

    def _run_process(
        self,
        command: list[str],
        environment: dict[str, str],
        queue_index: int,
        queue_position: int,
        queue_total: int,
    ) -> int:
        creationflags = gui_process_creationflags()
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
            with self._process_lock:
                self._active_processes.add(process)
                self._processes_by_queue_index[queue_index] = process
                self.process = process
            self.events.put(("process_started", queue_index))
            if self.stop_requested:
                terminate_process_tree(process)
            last_download_percent: float | None = None
            last_download_update = 0.0
            assert process.stdout is not None
            for line in process.stdout:
                if workspace := project_workspace_from_output(
                    line, self._current_output_directory
                ):
                    self.events.put(("project_workspace", (queue_index, workspace)))
                if download := _DOWNLOAD_PROGRESS_RE.search(line):
                    percent = float(download.group(1))
                    now = time.monotonic()
                    if (
                        last_download_percent is not None
                        and percent - last_download_percent < 1.0
                        and now - last_download_update < 0.4
                    ):
                        continue
                    last_download_percent = percent
                    last_download_update = now
                self.events.put(("line", (queue_index, queue_position, queue_total, line)))
            return process.wait()
        except OSError as exc:
            self.events.put(
                ("line", (queue_index, queue_position, queue_total, f"\n无法启动：{exc}\n"))
            )
            return 1
        finally:
            if process is not None:
                with self._process_lock:
                    self._active_processes.discard(process)
                    self._processes_by_queue_index.pop(queue_index, None)
                    if self.process is process:
                        self.process = next(iter(self._active_processes), None)

    def _create_failure_support_bundle_async(self, queue_index: int, root: Path) -> None:
        """Capture redacted evidence for an unexpected failure without blocking the UI."""
        if root in self._diagnostic_projects or not (root / "pipeline_state.json").is_file():
            return
        self._diagnostic_projects.add(root)

        def create_bundle() -> None:
            try:
                bundle = create_support_bundle(ProjectPaths(root))
            except (OSError, ValueError) as exc:
                self.events.put(("failure_bundle", (queue_index, root, None, str(exc))))
            else:
                self.events.put(("failure_bundle", (queue_index, root, bundle, None)))

        threading.Thread(
            target=create_bundle,
            name=f"localizer-diagnostics-{queue_index}",
            daemon=True,
        ).start()

    def _stop(self) -> None:
        with self._process_lock:
            processes = [process for process in self._active_processes if process.poll() is None]
        if self.stop_requested or not processes:
            return
        if not messagebox.askyesno(
            "停止处理",
            "确定停止正在运行的任务吗？已完成的阶段会保留，下次可继续。",
            parent=self.root,
        ):
            return
        self.stop_requested = True
        self._set_status("正在停止……")
        for process in processes:
            terminate_process_tree(process)

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "line":
                    index, position, total, line = payload  # type: ignore[misc]
                    prefix = (
                        f"[队列 {position}/{total} · 任务 {index}] " if int(total) > 1 else ""
                    )
                    self._append_log(prefix + str(line))
                    self._update_progress_from_output(
                        str(line),
                        queue_index=int(index),
                        queue_position=int(position),
                    )
                elif event == "media_analysis_started":
                    generation, index, _source = payload  # type: ignore[misc]
                    if int(generation) == self._analysis_generation:
                        self._update_task_row(
                            int(index),
                            media="正在读取标题、时长和画质…",
                            state_code="analyzing",
                        )
                elif event == "media_analysis_result":
                    generation, index, preview, error = payload  # type: ignore[misc]
                    if int(generation) != self._analysis_generation:
                        continue
                    task_index = int(index)
                    if isinstance(preview, MediaPreview):
                        self._media_previews[task_index] = preview
                        self._media_preview_errors.pop(task_index, None)
                        if (
                            preview.metadata is not None
                            and preview.metadata.source_type != "webpage_media"
                        ):
                            with suppress(OSError, TypeError, ValueError):
                                save_cached_inspection(
                                    self._task_sources[task_index - 1], preview.metadata
                                )
                        self._update_task_row(
                            task_index,
                            title=preview.title,
                            media=media_preview_summary(preview),
                            state_code="ready",
                            error="",
                        )
                    else:
                        message = str(error or "暂时无法读取媒体信息")
                        self._media_preview_errors[task_index] = message
                        self._update_task_row(
                            task_index,
                            media=message,
                            status="可直接尝试",
                            state_code="ready",
                            error=message,
                        )
                        if browser_capture_required(message):
                            source = self._task_sources[task_index - 1]
                            if source not in self._browser_capture_prompted_sources:
                                self._browser_capture_prompted_sources.add(source)
                                self._set_status(
                                    "该页面需要浏览器环境，正在启动独立 Edge 抓取窗口…",
                                    "active",
                                )
                                self.root.after(
                                    150,
                                    lambda page=source: self._open_browser_capture_dialog(
                                        page, auto_start=True
                                    ),
                                )
                elif event == "media_analysis_done":
                    generation, total = payload  # type: ignore[misc]
                    if int(generation) != self._analysis_generation:
                        continue
                    if not self._has_active_processes() and not (
                        self.worker and self.worker.is_alive()
                    ):
                        failed = len(self._media_preview_errors)
                        if failed:
                            self._set_status(
                                f"已预分析 {int(total) - failed}/{total} 个视频；其余仍可直接尝试",
                                "muted",
                            )
                        else:
                            self._set_status(f"{total} 个视频信息已就绪，可以开始", "success")
                        if retain_pending_start_sources(
                            self._pending_start_sources,
                            queue_input_values(self.input_value.get()),
                        ) is not None:
                            self.root.after(100, self._start)
                elif event == "done":
                    (
                        return_code,
                        provider,
                        completed,
                        total,
                        failures,
                        paused_count,
                        retry_indices,
                    ) = payload  # type: ignore[misc]
                    self._finish(
                        int(return_code),
                        str(provider),
                        completed=int(completed),
                        total=int(total),
                        failures=int(failures),
                        paused_count=int(paused_count),
                        retry_indices=tuple(int(index) for index in retry_indices),
                    )
                elif event == "process_started":
                    self._update_task_action_states()
                elif event == "project_workspace":
                    index, root = payload  # type: ignore[misc]
                    self._project_paths_by_queue_index[int(index)] = Path(root)
                    self._update_task_action_states()
                    self._schedule_queue_save()
                elif event == "queue_item":
                    index, position, total, source = payload  # type: ignore[misc]
                    self.active_queue_index = int(position)
                    self.active_queue_total = int(total)
                    self._task_progress[int(index)] = 0.0
                    self._progress_value = min(self._progress_value, 99.0)
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=self._progress_value)
                    self._set_status(
                        f"队列 {position}/{total} · 任务 {index}：正在启动", "active"
                    )
                    self._update_task_row(
                        int(index),
                        status="正在启动",
                        state_code="running",
                        progress="0%",
                    )
                    self._append_log(
                        f"\n{'=' * 10} 队列 {position}/{total} · 任务 {index}：{source} {'=' * 10}\n"
                    )
                elif event == "task_finished":
                    index, position, total, return_code, paused = payload  # type: ignore[misc]
                    if not paused:
                        self._task_progress[int(index)] = 100.0
                    if int(total):
                        aggregate = 99.0 * sum(self._task_progress.values()) / (100.0 * int(total))
                        self._progress_value = max(self._progress_value, min(99.0, aggregate))
                        self.progress.stop()
                        self.progress.configure(mode="determinate", value=self._progress_value)
                    if paused:
                        self._update_task_row(int(index), state_code="paused")
                        self._set_status(
                            f"任务 {index} 已暂停；其他队列任务继续运行", "active"
                        )
                    elif int(return_code) == 0:
                        self._update_task_row(
                            int(index), state_code="completed", progress="100%", error=""
                        )
                        self._set_status(
                            f"队列 {position}/{total} · 任务 {index} 已完成，继续处理…",
                            "active",
                        )
                    elif not self.stop_requested:
                        self._update_task_row(
                            int(index), state_code="failed", error="请查看运行记录和诊断包"
                        )
                        root = self._project_paths_by_queue_index.get(int(index))
                        if root is not None:
                            self._create_failure_support_bundle_async(int(index), root)
                elif event == "failure_bundle":
                    index, _root, bundle, error = payload  # type: ignore[misc]
                    if bundle is not None:
                        self._append_log(
                            f"[任务 {index}] 已自动导出脱敏诊断包：{bundle}\n"
                        )
                    else:
                        self._append_log(
                            f"[任务 {index}] 无法自动导出诊断包：{error}\n"
                        )
                elif event == "update":
                    self._show_update_result(payload)  # type: ignore[arg-type]
                elif event == "review_preview":
                    callback, output, error = payload  # type: ignore[misc]
                    callback(output, error)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _update_progress_from_output(
        self,
        line: str,
        *,
        queue_index: int | None = None,
        queue_position: int | None = None,
    ) -> None:
        update = progress_update_from_output(
            line, provider=self.active_provider,
            enhancement=getattr(self, "active_enhancement", False),
        )
        if update is None:
            return
        value, message = update
        self.progress.stop()
        self.progress.configure(mode="determinate")
        task_progress = min(99.0, value)
        if queue_index is not None:
            self._update_task_row(
                queue_index,
                status=message,
                state_code="running",
                progress=f"{task_progress:.0f}%",
            )
        if self._parallel_queue and queue_index is not None:
            self._task_progress[queue_index] = max(self._task_progress.get(queue_index, 0.0), task_progress)
            aggregate = 99.0 * sum(self._task_progress.values()) / (100.0 * self.active_queue_total)
        else:
            aggregate = 99.0 * (
                (self.active_queue_index - 1) + task_progress / 100
            ) / self.active_queue_total
        self._progress_value = max(self._progress_value, min(99.0, aggregate))
        self.progress.configure(value=self._progress_value)
        prefix = (
            f"队列 {queue_position or self.active_queue_index}/{self.active_queue_total}"
            f" · 任务 {queue_index or self.active_queue_index} · "
            if self.active_queue_total > 1
            else ""
        )
        self._set_status(prefix + message, "active")

    def _finish(
        self,
        return_code: int,
        provider: str,
        *,
        completed: int = 1,
        total: int = 1,
        failures: int = 0,
        paused_count: int = 0,
        retry_indices: tuple[int, ...] = (),
    ) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.input_entry.configure(state="normal")
        self.paste_button.configure(state="normal")
        self.direct_media_button.configure(state="normal")
        self.browser_capture_button.configure(state="normal")
        self.local_file_button.configure(state="normal")
        self.process = None
        self._parallel_queue = False
        self._task_progress = {}
        self._retry_task_indices = retry_indices
        self._retry_commands = []
        for index in retry_indices:
            command = self._commands_by_task_index.get(index)
            if command is None:
                continue
            resumed = list(command)
            if "--resume" not in resumed:
                resumed.append("--resume")
            self._retry_commands.append(resumed)
        self.retry_failed_button.configure(
            state="normal" if self._retry_commands else "disabled"
        )
        self._update_task_action_states()
        self.root.after(200, self._update_task_action_states)
        notify_window_attention(self.root)
        if self.stop_requested:
            self.stop_requested = False
            self.progress.configure(value=0)
            remaining = len(self._retry_commands)
            self._set_status(
                f"任务已停止；可恢复 {remaining} 个未完成任务" if remaining else "任务已停止"
            )
            self._append_log(
                f"\n任务已停止；已完成阶段会保留。"
                f"可点击“重试未完成任务”恢复 {remaining} 个项目。\n"
            )
            for index in retry_indices:
                self._update_task_row(int(index), state_code="paused")
            return
        if paused_count and return_code == 0:
            self.progress.configure(value=self._progress_value)
            self._set_status(
                f"队列其余任务已处理；{paused_count} 个任务已暂停，可单独继续",
                "active",
            )
            self._append_log(
                f"\n{paused_count} 个任务已安全暂停。选择任务后点击“继续所选”，"
                "或点击“重试未完成任务”。\n"
            )
            return
        if return_code != 0:
            self.progress.configure(value=0)
            successful = max(0, completed - failures - paused_count)
            self._set_status(
                f"队列完成：{successful}/{total} 个任务成功，请查看上方日志",
                "error",
            )
            recovery = friendly_failure_summary(self.log.get("1.0", "end"))
            paused_summary = f"，另有 {paused_count} 个已暂停" if paused_count else ""
            messagebox.showerror(
                "部分任务未完成",
                f"已完成 {successful}/{total} 个任务{paused_summary}。窗口日志和所选输出文件夹内项目的 logs "
                "文件夹包含详细原因；程序会为可识别的失败项目自动创建脱敏诊断包。"
                f"\n\n建议：{recovery}",
                parent=self.root,
            )
            return
        self.progress.configure(value=100)
        if provider == "download_only":
            self._set_status(f"{total} 个原视频下载完成，没有生成字幕", "success")
            messagebox.showinfo(
                "下载完成",
                f"{total} 个最高画质原视频已经保存到所选输出文件夹内项目的 source 文件夹。",
                parent=self.root,
            )
        elif provider == "manual":
            source_name = "中文" if self.active_direction == "zh-to-en" else "英文"
            target_name = "英文" if self.active_direction == "zh-to-en" else "中文"
            self._set_status(f"{total} 个任务的下载和{source_name}字幕已完成，等待人工翻译", "success")
            messagebox.showinfo(
                "第一阶段完成",
                f"{total} 个视频和{source_name}字幕已经准备好。请在所选输出文件夹内项目的 "
                "subtitles\\translation_chunks 中处理翻译文件；"
                f"导入翻译后即可压制{target_name}字幕。",
                parent=self.root,
            )
        else:
            target_code = self.active_direction.split("-to-", maxsplit=1)[1]
            target_name = language_name(self.active_direction, target=True)
            output_name = f"{target_code}_hardsub.mp4" if target_code not in {"en", "zh"} else (
                "english_hardsub.mp4" if target_code == "en" else "chinese_hardsub.mp4"
            )
            self._set_status(f"本地化完成，已生成 {total} 个{target_name}字幕视频", "success")
            messagebox.showinfo(
                "本地化完成",
                f"最终视频位于所选输出文件夹内项目的 rendered\\{output_name}。已完成 {total} 个任务。",
                parent=self.root,
            )

    def _set_status(self, message: str, tone: str = "muted") -> None:
        colors = {
            "muted": MUTED,
            "active": PRIMARY,
            "success": SUCCESS,
            "error": DANGER,
        }
        self.status.set(message)
        self.status_dot.configure(foreground=colors.get(tone, MUTED))

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _open_output(self) -> None:
        output = Path(self.output_directory.get().strip() or default_output_directory()).expanduser()
        output.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(output)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(output)])
        else:
            subprocess.Popen(["xdg-open", str(output)])

    def _export_support_bundle(self) -> None:
        from tkinter import filedialog

        selected = filedialog.askdirectory(
            parent=self.root,
            title="选择需要诊断的项目文件夹",
            initialdir=self.output_directory.get().strip() or str(default_output_directory()),
            mustexist=True,
        )
        if not selected:
            return
        project = ProjectPaths(Path(selected).resolve())
        if not project.state_file.is_file():
            messagebox.showwarning(
                "不是项目文件夹",
                "请选择包含 pipeline_state.json 的项目文件夹。",
                parent=self.root,
            )
            return
        try:
            bundle = create_support_bundle(project)
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法导出诊断包", str(exc), parent=self.root)
            return
        messagebox.showinfo(
            "诊断包已导出",
            f"已创建：\n{bundle}\n\n包内不含视频和字幕文本；路径、链接和凭证会被隐藏。",
            parent=self.root,
        )

    def _on_close(self) -> None:
        if self.browser_capture_dialog is not None:
            self.browser_capture_dialog.cancel_event.set()
        process_running = self._has_active_processes()
        worker_running = self.worker is not None and self.worker.is_alive()
        if process_running or worker_running:
            if not messagebox.askyesno(
                "退出工具",
                "处理仍在进行。退出会停止任务，但已完成的阶段可以继续。确定退出吗？",
                parent=self.root,
            ):
                return
            self.stop_requested = True
            with self._process_lock:
                processes = list(self._active_processes)
            for process in processes:
                terminate_process_tree(process)
        self._save_desktop_settings()
        if self._queue_save_after_id is not None:
            self.root.after_cancel(self._queue_save_after_id)
            self._queue_save_after_id = None
        self._save_queue_snapshot()
        self.root.destroy()


def run_gui() -> None:
    root = tk.Tk()
    LocalizerWindow(root)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
