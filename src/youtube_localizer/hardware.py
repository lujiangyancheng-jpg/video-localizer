from __future__ import annotations

import csv
import ctypes
import os
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .errors import ExternalToolError
from .resources import nvenc_compatibility_ffmpeg
from .utils.subprocesses import resolve_executable, run_command

HARDWARE_H264_CODECS = frozenset({"h264_nvenc", "h264_qsv", "h264_amf", "h264_videotoolbox"})


@dataclass(frozen=True)
class NvidiaGPU:
    name: str
    driver_version: str
    total_memory_mib: int | None
    used_memory_mib: int | None


@dataclass(frozen=True)
class SystemResources:
    """Small, dependency-free inventory used to keep CPU fallback responsive."""

    logical_cpu_count: int
    memory_mib: int | None


@dataclass(frozen=True)
class H264Encoder:
    """A verified H.264 encoder executable selected for this computer."""

    ffmpeg: str | None
    detail: str
    codec: str = "libx264"
    uses_compatibility_build: bool = False


# Kept as a public alias for saved integrations and older callers.
NvencEncoder = H264Encoder


def query_nvidia_gpus() -> list[NvidiaGPU]:
    """Read lightweight NVIDIA inventory data when nvidia-smi is available."""
    executable = resolve_executable("nvidia-smi")
    if executable is None:
        return []
    try:
        result = run_command(
            [
                executable,
                "--query-gpu=name,driver_version,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            timeout=5,
        )
    except ExternalToolError:
        return []

    gpus: list[NvidiaGPU] = []
    for row in csv.reader(result.stdout.splitlines()):
        if len(row) != 4:
            continue
        name, driver, total, used = (item.strip() for item in row)
        if not name:
            continue
        gpus.append(
            NvidiaGPU(
                name=name,
                driver_version=driver,
                total_memory_mib=_parse_memory_mib(total),
                used_memory_mib=_parse_memory_mib(used),
            )
        )
    return gpus


def detect_system_resources() -> SystemResources:
    """Return the resources that affect safe CPU fallback without extra packages."""
    logical_cpu_count = max(1, os.cpu_count() or 1)
    memory_mib: int | None = None
    if os.name == "nt":

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            memory_mib = round(status.ullTotalPhys / 1024**2)
    else:
        with suppress(AttributeError, OSError, ValueError):
            memory_mib = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**2)
    return SystemResources(logical_cpu_count=logical_cpu_count, memory_mib=memory_mib)


def recommended_cpu_threads(resources: SystemResources | None = None) -> int:
    """Reserve enough CPU for the desktop while keeping CPU-only machines useful."""
    resources = resources or detect_system_resources()
    cores = resources.logical_cpu_count
    if cores <= 2:
        threads = 1
    elif cores <= 4:
        threads = 2
    elif cores <= 8:
        threads = cores - 2
    else:
        threads = min(8, cores - 4)
    if resources.memory_mib is not None:
        if resources.memory_mib < 8 * 1024:
            threads = min(threads, 2)
        elif resources.memory_mib < 12 * 1024:
            threads = min(threads, 4)
    return max(1, threads)


def resolve_cpu_threads(requested: int, resources: SystemResources | None = None) -> int:
    """Resolve the auto value (0) and avoid over-subscribing a small CPU."""
    resources = resources or detect_system_resources()
    if requested <= 0:
        return recommended_cpu_threads(resources)
    return max(1, min(requested, resources.logical_cpu_count))


def probe_h264_encoder(codec: str, ffmpeg: str | None = None) -> tuple[bool, str]:
    """Verify an H.264 hardware encoder with a negligible real FFmpeg job."""
    if codec not in HARDWARE_H264_CODECS:
        return False, f"{codec} is not a supported hardware H.264 encoder."
    executable = ffmpeg or resolve_executable("ffmpeg")
    if executable is None:
        return False, "FFmpeg was not found."
    try:
        run_command(
            [
                executable,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                # Some otherwise valid encoders reject tiny test frames. This remains negligible
                # while matching dimensions that hardware encoders accept in normal projects.
                "color=c=black:s=320x240:r=1",
                "-frames:v",
                "2",
                "-an",
                "-c:v",
                codec,
                "-f",
                "null",
                "-",
            ],
            timeout=10,
        )
    except ExternalToolError as exc:
        detail = str(exc)
        normalized = detail.casefold()
        if codec == "h264_nvenc" and (
            "minimum required nvidia driver" in normalized or "nvenc api version" in normalized
        ):
            return (
                False,
                "当前 NVIDIA 驱动不能供此 FFmpeg 使用 NVENC；可自动改用兼容编码器或其他方案。",
            )
        if codec == "h264_nvenc" and "no nvenc capable devices" in normalized:
            return False, "FFmpeg 没有发现可用的 NVIDIA NVENC 编码器。"
        return False, f"{codec} 测试失败：{_last_detail_line(detail)}"
    return True, f"{codec} 硬件编码可用。"


def probe_h264_nvenc(ffmpeg: str | None = None) -> tuple[bool, str]:
    """Backward-compatible NVIDIA-specific wrapper for the generic probe."""
    return probe_h264_encoder("h264_nvenc", ffmpeg)


def select_h264_nvenc_encoder(ffmpeg: str | None = None) -> H264Encoder:
    """Select a working NVENC build without assuming a particular driver version.

    The bundled compatibility build carries an earlier NVENC API. It lets a stable driver
    continue using GPU encoding when a newer FFmpeg build requires an unavailable API.
    """
    primary = ffmpeg or resolve_executable("ffmpeg")
    failure_details: list[str] = []
    if primary:
        ready, detail = probe_h264_nvenc(primary)
        if ready:
            return H264Encoder(primary, detail, codec="h264_nvenc")
        failure_details.append(f"默认 FFmpeg：{detail}")

    compatibility = nvenc_compatibility_ffmpeg()
    if compatibility and (not primary or Path(primary).resolve() != compatibility):
        ready, detail = probe_h264_nvenc(str(compatibility))
        if ready:
            return H264Encoder(
                str(compatibility),
                "已使用随安装包提供的 NVENC 兼容编码器。",
                codec="h264_nvenc",
                uses_compatibility_build=True,
            )
        failure_details.append(f"兼容编码器：{detail}")

    if failure_details:
        return H264Encoder(None, "；".join(failure_details))
    return H264Encoder(None, "未找到可用的 FFmpeg。")


def select_h264_encoder(ffmpeg: str | None = None, *, preferred: str = "auto") -> H264Encoder:
    """Choose a verified encoder across NVIDIA, Intel, AMD, and macOS hardware.

    `auto` intentionally probes instead of guessing from GPU brand. This supports mixed-GPU
    laptops, old drivers, and a system FFmpeg whose compiled encoders differ from its GPU.
    """
    if preferred not in {"auto", *HARDWARE_H264_CODECS}:
        return H264Encoder(None, f"Unknown hardware encoder preference: {preferred}.")

    if preferred in {"auto", "h264_nvenc"}:
        nvenc = select_h264_nvenc_encoder(ffmpeg)
        if nvenc.ffmpeg or preferred == "h264_nvenc":
            return nvenc
        failure_details = [nvenc.detail]
    else:
        failure_details = []

    primary = ffmpeg or resolve_executable("ffmpeg")
    candidates = ["h264_qsv", "h264_amf"]
    if sys.platform == "darwin":
        candidates.append("h264_videotoolbox")
    if preferred != "auto":
        candidates = [preferred]

    if primary:
        for codec in candidates:
            ready, detail = probe_h264_encoder(codec, primary)
            if ready:
                return H264Encoder(primary, detail, codec=codec)
            failure_details.append(detail)
    else:
        failure_details.append("未找到可用的 FFmpeg。")
    return H264Encoder(None, "；".join(failure_details))


def format_nvidia_gpus(gpus: list[NvidiaGPU]) -> str:
    if not gpus:
        return "未检测到可读取的 NVIDIA GPU 信息。"
    entries = []
    for gpu in gpus:
        memory = (
            f"，显存 {gpu.used_memory_mib}/{gpu.total_memory_mib} MiB"
            if gpu.total_memory_mib is not None and gpu.used_memory_mib is not None
            else ""
        )
        driver = f"，驱动 {gpu.driver_version}" if gpu.driver_version else ""
        entries.append(f"{gpu.name}{driver}{memory}")
    return "；".join(entries)


def _parse_memory_mib(value: str) -> int | None:
    try:
        return int(float(value))
    except ValueError:
        return None


def _last_detail_line(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return lines[-1] if lines else "未知错误"
