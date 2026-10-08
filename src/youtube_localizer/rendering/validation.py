from __future__ import annotations

import json
import os
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..errors import LocalizerError
from ..utils.subprocesses import run_command


def probe_output(path: Path, *, ffprobe: str = "ffprobe") -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        raise LocalizerError(f"Rendered output is missing or empty: {path}")
    result = run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            path,
        ]
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise LocalizerError(f"ffprobe returned invalid JSON for output {path}") from exc


def validate_rendered_video(
    path: Path,
    *,
    expected_duration: float,
    ffprobe: str = "ffprobe",
    ffmpeg: str = "ffmpeg",
    decode: bool = True,
    require_audio: bool = True,
    expected_height: int | None = None,
    expected_frame_rate: float | None = None,
) -> dict[str, Any]:
    data = probe_output(path, ffprobe=ffprobe)
    streams = data.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if video is None:
        raise LocalizerError("Rendered file has no video stream.")
    if require_audio and not any(stream.get("codec_type") == "audio" for stream in streams):
        raise LocalizerError("Rendered file has no audio stream.")
    if expected_height is not None and abs(int(video.get("height") or 0) - expected_height) > 2:
        raise LocalizerError(f"Rendered height does not match the requested {expected_height}p.")
    if expected_frame_rate is not None:
        try:
            rate = float(Fraction(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0"))
        except (ValueError, ZeroDivisionError):
            rate = 0
        if abs(rate - expected_frame_rate) > max(0.15, expected_frame_rate * 0.01):
            raise LocalizerError(
                f"Rendered frame rate {rate:g} does not match {expected_frame_rate:g} FPS."
            )
    duration = float(data.get("format", {}).get("duration") or 0)
    tolerance = max(2.0, expected_duration * 0.02)
    if expected_duration > 0 and abs(duration - expected_duration) > tolerance:
        raise LocalizerError(
            f"Rendered duration {duration:.2f}s differs from source {expected_duration:.2f}s "
            f"by more than {tolerance:.2f}s."
        )
    if decode:
        null_target = "NUL" if os.name == "nt" else "/dev/null"
        run_command(
            [
                ffmpeg,
                "-v",
                "error",
                "-xerror",
                "-i",
                path,
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-f",
                "null",
                null_target,
            ]
        )
    return data
