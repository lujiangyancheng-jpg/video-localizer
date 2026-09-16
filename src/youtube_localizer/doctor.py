from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import is_onedrive_directory, output_directory_advice
from .download.youtube import discover_javascript_runtimes
from .hardware import (
    detect_system_resources,
    format_nvidia_gpus,
    query_nvidia_gpus,
    recommended_cpu_threads,
    select_h264_encoder,
)
from .resources import (
    bundled_fonts_directory,
    find_bundled_model,
    installed_whisper_models,
    ollama_executable,
    package_tier,
    resolve_whisper_model,
)
from .transcription.whisper_engine import cuda_runtime_status
from .translation.offline import validate_offline_model
from .utils.subprocesses import resolve_executable


@dataclass
class DoctorCheck:
    name: str
    status: str
    detail: str
    required: bool = True


def _module_check(module: str, label: str, *, required: bool = True) -> DoctorCheck:
    found = importlib.util.find_spec(module) is not None
    return DoctorCheck(
        label,
        "ok" if found else ("missing" if required else "optional"),
        "installed" if found else f"install Python package: {module.replace('_', '-')}",
        required,
    )


def _font_check() -> DoctorCheck:
    if bundled := bundled_fonts_directory():
        return DoctorCheck("Chinese subtitle font", "ok", f"bundled fonts: {bundled}", False)
    candidates = ["Noto Sans CJK SC"]
    font_files: list[Path] = []
    if os.name == "nt":
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for pattern in ("msyh*.ttc", "simhei.ttf", "*NotoSansCJK*"):
            font_files.extend(fonts.glob(pattern))
    else:
        for root in (Path("/usr/share/fonts"), Path("/Library/Fonts")):
            if root.exists():
                font_files.extend(root.rglob("*Noto*Sans*CJK*"))
    return DoctorCheck(
        "Chinese subtitle font",
        "ok" if font_files else "warning",
        str(font_files[0]) if font_files else f"none of {', '.join(candidates)} detected",
        False,
    )


def _system_capacity_check() -> DoctorCheck:
    resources = detect_system_resources()
    memory = (
        f"{resources.memory_mib / 1024:.1f} GiB RAM"
        if resources.memory_mib is not None
        else "RAM unavailable"
    )
    detail = (
        f"{resources.logical_cpu_count} logical CPU threads, {memory}; "
        f"automatic CPU transcription uses {recommended_cpu_threads(resources)} thread(s)."
    )
    if resources.memory_mib is not None and resources.memory_mib < 8 * 1024:
        return DoctorCheck(
            "System capacity",
            "warning",
            detail + " Medium Whisper may be slow; select Safe CPU for long videos.",
            False,
        )
    if resources.logical_cpu_count <= 2:
        return DoctorCheck(
            "System capacity",
            "warning",
            detail + " Long CPU-only jobs should use Safe CPU.",
            False,
        )
    return DoctorCheck("System capacity", "ok", detail, False)


def run_doctor(
    output_directory: Path,
    *,
    offline_model_directory: Path | None = None,
    offline_zh_en_model_directory: Path | None = None,
) -> list[DoctorCheck]:
    installed_tier = package_tier()
    version_ok = sys.version_info >= (3, 11)
    checks = [
        DoctorCheck(
            "Python",
            "ok" if version_ok else "missing",
            sys.version.split()[0] + (" (3.11+)" if version_ok else "; Python 3.11+ required"),
        ),
    ]
    checks.append(_system_capacity_check())
    for executable in ("ffmpeg", "ffprobe"):
        path = resolve_executable(executable)
        checks.append(
            DoctorCheck(
                executable,
                "ok" if path else "missing",
                path or f"{executable} is not on PATH",
            )
        )
    ytdlp_path = resolve_executable("yt-dlp")
    ytdlp_module = importlib.util.find_spec("yt_dlp") is not None
    checks.append(
        DoctorCheck(
            "yt-dlp",
            "ok" if ytdlp_path or ytdlp_module else "missing",
            ytdlp_path or ("Python module installed" if ytdlp_module else "install package yt-dlp"),
        )
    )
    runtimes = discover_javascript_runtimes()
    ejs_available = importlib.util.find_spec("yt_dlp_ejs") is not None
    javascript_ready = bool(runtimes) and ejs_available
    runtime_detail = ", ".join(f"{name}: {path}" for name, path in runtimes.items())
    checks.append(
        DoctorCheck(
            "yt-dlp JavaScript support",
            "ok" if javascript_ready else "missing",
            (
                f"{runtime_detail}; yt-dlp-ejs installed"
                if javascript_ready
                else "Install project dependencies with yt-dlp[default,deno] so all YouTube "
                "formats can be discovered."
            ),
        )
    )
    offline_runtime = all(
        importlib.util.find_spec(module) is not None for module in ("ctranslate2", "sentencepiece")
    )
    checks.append(
        DoctorCheck(
            "Offline translation runtime",
            "ok" if offline_runtime else "optional",
            (
                "CTranslate2 and SentencePiece installed"
                if offline_runtime
                else 'install with: python -m pip install -e ".[offline-translation]"'
            ),
            False,
        )
    )
    default_offline_model = (
        offline_model_directory or Path("~/.youtube-chinese-localizer/models/translate-en_zh-1_9")
    ).expanduser()
    if validate_offline_model(default_offline_model) is None:
        default_offline_model = find_bundled_model("translate-en_zh-1_9") or default_offline_model
    model_ready = validate_offline_model(default_offline_model) is not None
    checks.append(
        DoctorCheck(
            "Offline English→Chinese model",
            "ok" if model_ready else "optional",
            (
                str(default_offline_model)
                if model_ready
                else "downloads automatically on first offline translation"
            ),
            False,
        )
    )
    zh_en_model = (
        offline_zh_en_model_directory
        or Path("~/.youtube-chinese-localizer/models/translate-zh_en-1_9")
    ).expanduser()
    if validate_offline_model(zh_en_model, source_code="zh", target_code="en") is None:
        zh_en_model = find_bundled_model("translate-zh_en-1_9") or zh_en_model
    zh_en_model_ready = (
        validate_offline_model(zh_en_model, source_code="zh", target_code="en") is not None
    )
    checks.append(
        DoctorCheck(
            "Offline Chinese-to-English model",
            "ok" if zh_en_model_ready else "optional",
            (
                str(zh_en_model)
                if zh_en_model_ready
                else "downloads automatically on first Chinese-to-English translation"
            ),
            False,
        )
    )
    ollama_path = ollama_executable()
    checks.append(
        DoctorCheck(
            "Local AI paragraph translation",
            "ok" if ollama_path else "optional",
            (
                str(ollama_path)
                if ollama_path
                else (
                    "not included in the Standard package; fast offline translation is ready"
                    if installed_tier == "standard"
                    else "install with: winget install Ollama.Ollama"
                )
            ),
            False,
        )
    )
    checks.append(_module_check("faster_whisper", "faster-whisper", required=False))
    installed_whispers = installed_whisper_models()
    for whisper_name in ("medium", "small"):
        whisper_model, whisper_is_local = resolve_whisper_model(whisper_name)
        package_model_missing = (
            installed_tier is not None and whisper_name not in installed_whispers
        )
        checks.append(
            DoctorCheck(
                f"Whisper {whisper_name} model",
                "ok" if whisper_is_local else "optional",
                (
                    whisper_model
                    if whisper_is_local
                    else (
                        "install the matching Whisper model pack; Small is recommended for most computers"
                        if package_model_missing
                        else "downloads on first source-checkout use"
                    )
                ),
                False,
            )
        )
    cuda_ready, cuda_detail = cuda_runtime_status()
    checks.append(
        DoctorCheck(
            "CUDA for faster-whisper",
            "ok" if cuda_ready else "optional",
            cuda_detail if cuda_ready else f"{cuda_detail}; CPU mode remains supported",
            False,
        )
    )
    nvidia_gpus = query_nvidia_gpus()
    checks.append(
        DoctorCheck(
            "NVIDIA GPU",
            "ok" if nvidia_gpus else "optional",
            format_nvidia_gpus(nvidia_gpus),
            False,
        )
    )
    hardware_encoder = select_h264_encoder()
    checks.append(
        DoctorCheck(
            "Hardware hard-subtitle encoding",
            "ok" if hardware_encoder.ffmpeg else ("warning" if nvidia_gpus else "optional"),
            hardware_encoder.detail,
            False,
        )
    )
    api_key = bool(os.getenv("OPENAI_COMPATIBLE_API_KEY"))
    checks.append(
        DoctorCheck(
            "Translation configuration",
            "ok" if api_key or model_ready or zh_en_model_ready or ollama_path else "optional",
            (
                "API key detected"
                if api_key
                else (
                    "local translation ready; a cloud API is optional"
                    if model_ready or zh_en_model_ready or ollama_path
                    else "manual export/import available; ChatGPT Plus does not include API credits"
                )
            ),
            False,
        )
    )
    try:
        output_directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output_directory, delete=True):
            pass
        writable = True
        detail = str(output_directory.resolve())
    except OSError as exc:
        writable = False
        detail = str(exc)
    checks.append(
        DoctorCheck(
            "Output directory",
            "ok" if writable else "missing",
            detail,
        )
    )
    storage_advice = output_directory_advice(output_directory)
    storage_status = (
        "warning"
        if is_onedrive_directory(output_directory) or storage_advice.startswith("可用空间仅")
        else "ok"
    )
    checks.append(DoctorCheck("Output performance", storage_status, storage_advice, False))
    checks.append(_font_check())
    return checks
