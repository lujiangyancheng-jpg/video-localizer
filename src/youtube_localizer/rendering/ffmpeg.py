from __future__ import annotations

import logging
from pathlib import Path

from ..config import RenderConfig
from ..errors import ExternalToolError, LocalizerError
from ..hardware import HARDWARE_H264_CODECS, select_h264_encoder
from ..resource_gate import heavy_workload_slot
from ..resources import bundled_fonts_directory
from ..utils.subprocesses import run_command, run_streaming_command

LOGGER = logging.getLogger(__name__)


def escape_filter_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    value = value.replace(":", r"\:").replace("'", r"\'").replace("[", r"\[").replace("]", r"\]")
    return value


def video_codec_arguments(config: RenderConfig) -> list[str]:
    """Return quality arguments shared by subtitle rendering and AI enhancement."""
    if config.codec in {"libx264", "libx265"}:
        return ["-crf", str(config.crf), "-preset", config.preset]
    if config.codec in {"h264_nvenc", "hevc_nvenc"}:
        return ["-cq", str(config.crf), "-preset", config.preset]
    if config.codec == "h264_qsv":
        return ["-global_quality", str(config.crf), "-preset", config.preset]
    if config.codec == "h264_amf":
        amf_quality = {
            "ultrafast": "speed",
            "superfast": "speed",
            "veryfast": "speed",
            "faster": "balanced",
            "fast": "balanced",
            "medium": "balanced",
            "slow": "quality",
            "slower": "quality",
            "veryslow": "quality",
        }.get(config.preset, "balanced")
        return [
            "-rc",
            "cqp",
            "-qp_i",
            str(config.crf),
            "-qp_p",
            str(min(51, config.crf + 2)),
            "-qp_b",
            str(min(51, config.crf + 3)),
            "-quality",
            amf_quality,
        ]
    if config.codec == "h264_videotoolbox":
        return ["-q:v", str(config.crf)]
    return []


def resolve_render_backend(
    config: RenderConfig, ffmpeg: str = "ffmpeg"
) -> tuple[str, RenderConfig]:
    """Resolve a verified hardware encoder, with the same safe CPU fallback everywhere."""
    if config.codec != "auto" and config.codec not in HARDWARE_H264_CODECS:
        return ffmpeg, config
    hardware_encoder = select_h264_encoder(
        ffmpeg,
        preferred=config.codec if config.codec != "auto" else "auto",
    )
    if hardware_encoder.ffmpeg is None:
        LOGGER.warning(
            "Skipping unavailable hardware encoding before the full render: %s "
            "Using fast CPU encoding at the same visual-quality setting.",
            hardware_encoder.detail,
        )
        return ffmpeg, config.model_copy(update={"codec": "libx264", "preset": "fast"})
    LOGGER.info("Using verified hardware H.264 encoder: %s.", hardware_encoder.codec)
    if hardware_encoder.uses_compatibility_build:
        LOGGER.info("Using the bundled NVENC compatibility encoder for this driver.")
    return hardware_encoder.ffmpeg, config.model_copy(update={"codec": hardware_encoder.codec})


def build_hardsub_command(
    source_video: Path,
    subtitle_file: Path,
    output_file: Path,
    config: RenderConfig,
    *,
    source_audio_codec: str = "",
    ffmpeg: str = "ffmpeg",
    start: float | None = None,
    duration: float | None = None,
    fonts_directory: Path | None = None,
    source_frame_rate: float | None = None,
) -> list[str]:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-stats_period",
        "2",
        "-progress",
        "pipe:1",
        "-y",
    ]
    if start is not None:
        command += ["-ss", str(max(0, start))]
    command += ["-i", str(source_video)]
    if duration is not None:
        command += ["-t", str(max(0.1, duration))]
    escaped = escape_filter_path(subtitle_file)
    filter_name = "ass" if subtitle_file.suffix.lower() in {".ass", ".ssa"} else "subtitles"
    subtitle_filter = f"{filter_name}=filename='{escaped}'"
    if fonts_directory is not None:
        subtitle_filter += f":fontsdir='{escape_filter_path(fonts_directory)}'"
    video_filters = [subtitle_filter]
    if config.output_height is not None:
        # Never upscale a smaller source. The escaped comma belongs to FFmpeg's expression
        # parser, not the shell (commands are always executed as argument arrays).
        video_filters.append(
            f"scale=-2:min({config.output_height}\\,ih):force_original_aspect_ratio=decrease"
        )
    if config.output_fps is not None and (
        source_frame_rate is None or source_frame_rate > config.output_fps + 0.5
    ):
        # Do not manufacture frames when a user selects 60 FPS for a 30 FPS source.
        video_filters.append(f"fps={config.output_fps}")
    command += [
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        ",".join(video_filters),
        "-c:v",
        config.codec,
    ]
    command += video_codec_arguments(config)
    if config.copy_audio_when_possible and source_audio_codec.lower() == "aac":
        command += ["-c:a", "copy"]
    else:
        command += ["-c:a", config.audio_codec, "-b:a", config.audio_bitrate]
    if config.faststart:
        command += ["-movflags", "+faststart"]
    command += [str(output_file)]
    return command


def build_video_transform_command(
    source_video: Path,
    output_file: Path,
    config: RenderConfig,
    *,
    source_audio_codec: str = "",
    source_frame_rate: float | None = None,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    """Build an ordinary scale/frame-rate conversion without subtitles or AI."""
    filters: list[str] = []
    if config.output_height is not None:
        filters.append(
            f"scale=-2:min({config.output_height}\\,ih):force_original_aspect_ratio=decrease"
        )
    if config.output_fps is not None and (
        source_frame_rate is None or source_frame_rate > config.output_fps + 0.5
    ):
        filters.append(f"fps={config.output_fps}")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-stats_period",
        "2",
        "-progress",
        "pipe:1",
        "-y",
        "-i",
        str(source_video),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
    ]
    if filters:
        command.extend(["-vf", ",".join(filters)])
    command.extend(["-c:v", config.codec, *video_codec_arguments(config)])
    if config.copy_audio_when_possible and source_audio_codec.lower() == "aac":
        command.extend(["-c:a", "copy"])
    else:
        command.extend(["-c:a", config.audio_codec, "-b:a", config.audio_bitrate])
    if config.faststart:
        command.extend(["-movflags", "+faststart"])
    command.append(str(output_file))
    return command


def render_video_transform(
    source_video: Path,
    output_file: Path,
    config: RenderConfig,
    *,
    source_audio_codec: str = "",
    expected_duration: float | None = None,
    source_frame_rate: float | None = None,
    ffmpeg: str = "ffmpeg",
) -> Path:
    """Apply requested output dimensions/FPS in direct-download workflows."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temp = output_file.with_name(f"{output_file.stem}.partial{output_file.suffix}")
    active_ffmpeg, active_config = resolve_render_backend(config, ffmpeg)
    progress = _FFmpegProgress(expected_duration, label="Converting video output")

    def execute(render_config: RenderConfig) -> None:
        run_streaming_command(
            build_video_transform_command(
                source_video,
                temp,
                render_config,
                source_audio_codec=source_audio_codec,
                source_frame_rate=source_frame_rate,
                ffmpeg=active_ffmpeg,
            ),
            line_callback=progress.consume,
        )

    with heavy_workload_slot("video output conversion", kind="encoder"):
        try:
            execute(active_config)
        except ExternalToolError as exc:
            if active_config.codec not in HARDWARE_H264_CODECS:
                raise LocalizerError(f"FFmpeg video conversion failed.\n{exc}") from exc
            LOGGER.warning("Hardware conversion failed; retrying with fast CPU encoding.")
            execute(active_config.model_copy(update={"codec": "libx264", "preset": "fast"}))
    if not temp.is_file() or temp.stat().st_size == 0:
        raise LocalizerError("FFmpeg reported success but did not create the converted video.")
    temp.replace(output_file)
    return output_file


def render_hardsub(
    source_video: Path,
    subtitle_file: Path,
    output_file: Path,
    config: RenderConfig,
    *,
    source_audio_codec: str = "",
    ffmpeg: str = "ffmpeg",
    start: float | None = None,
    duration: float | None = None,
    expected_duration: float | None = None,
    source_frame_rate: float | None = None,
) -> Path:
    with heavy_workload_slot("hard-subtitle rendering", kind="encoder"):
        return _render_hardsub(
            source_video,
            subtitle_file,
            output_file,
            config,
            source_audio_codec=source_audio_codec,
            ffmpeg=ffmpeg,
            start=start,
            duration=duration,
            expected_duration=expected_duration,
            source_frame_rate=source_frame_rate,
        )


def _render_hardsub(
    source_video: Path,
    subtitle_file: Path,
    output_file: Path,
    config: RenderConfig,
    *,
    source_audio_codec: str = "",
    ffmpeg: str = "ffmpeg",
    start: float | None = None,
    duration: float | None = None,
    expected_duration: float | None = None,
    source_frame_rate: float | None = None,
) -> Path:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temp = output_file.with_name(f"{output_file.stem}.partial{output_file.suffix}")
    fonts_directory = bundled_fonts_directory()
    if fonts_directory is not None:
        LOGGER.info("Using bundled subtitle fonts from %s.", fonts_directory)
    active_ffmpeg, active_config = resolve_render_backend(config, ffmpeg)
    command = build_hardsub_command(
        source_video,
        subtitle_file,
        temp,
        active_config,
        source_audio_codec=source_audio_codec,
        ffmpeg=active_ffmpeg,
        start=start,
        duration=duration,
        fonts_directory=fonts_directory,
        source_frame_rate=source_frame_rate,
    )
    progress = _FFmpegProgress(expected_duration or duration)
    try:
        run_streaming_command(command, line_callback=progress.consume)
    except ExternalToolError as exc:
        if active_config.codec in HARDWARE_H264_CODECS:
            detail = str(exc).lower()
            if active_config.codec == "h264_nvenc" and (
                "nvenc api version" in detail or "minimum required nvidia driver" in detail
            ):
                LOGGER.warning(
                    "NVIDIA encoding needs a newer graphics driver for this FFmpeg build; "
                    "retrying with fast CPU encoding. Update the NVIDIA driver to restore "
                    "the much faster NVENC path."
                )
            else:
                LOGGER.warning("Hardware encoding failed; retrying with fast CPU encoding.")
            # CRF stays unchanged, so visual quality is preserved. The faster preset trades a
            # somewhat larger output file for a considerably shorter CPU fallback render.
            fallback = active_config.model_copy(update={"codec": "libx264", "preset": "fast"})
            run_streaming_command(
                build_hardsub_command(
                    source_video,
                    subtitle_file,
                    temp,
                    fallback,
                    source_audio_codec=source_audio_codec,
                    ffmpeg=active_ffmpeg,
                    start=start,
                    duration=duration,
                    fonts_directory=fonts_directory,
                    source_frame_rate=source_frame_rate,
                ),
                line_callback=progress.consume,
            )
        else:
            hint = ""
            if "No such filter" in str(exc) or "subtitles" in str(exc).lower():
                hint = " Ensure your FFmpeg build includes libass subtitle support."
            raise LocalizerError(f"FFmpeg hard-subtitle rendering failed.{hint}\n{exc}") from exc
    if not temp.is_file() or temp.stat().st_size == 0:
        raise LocalizerError("FFmpeg reported success but did not create a rendered video.")
    temp.replace(output_file)
    return output_file


class _FFmpegProgress:
    def __init__(
        self, expected_duration: float | None, *, label: str = "Rendering subtitles"
    ) -> None:
        self.expected_duration = (
            expected_duration if expected_duration and expected_duration > 0 else None
        )
        self.label = label
        self.values: dict[str, str] = {}

    def consume(self, line: str) -> None:
        key, separator, value = line.partition("=")
        if not separator:
            return
        self.values[key] = value
        if key != "progress":
            return
        elapsed_text = self.values.get("out_time", "00:00:00")
        speed = self.values.get("speed", "?")
        elapsed = _parse_ffmpeg_time(elapsed_text)
        if self.expected_duration is None:
            LOGGER.info("%s: %s elapsed, speed %s.", self.label, elapsed_text, speed)
            return
        percent = min(100.0, max(0.0, elapsed / self.expected_duration * 100))
        LOGGER.info(
            "%s: %.1f%% (%s / %s), speed %s.",
            self.label,
            percent,
            elapsed_text,
            _format_duration(self.expected_duration),
            speed,
        )


def _parse_ffmpeg_time(value: str) -> float:
    try:
        hours, minutes, seconds = value.split(":", maxsplit=2)
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        return 0.0


def _format_duration(value: float) -> str:
    total_seconds = max(0, round(value))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def build_softsub_command(
    source_video: Path,
    subtitle_file: Path,
    output_file: Path,
    *,
    language: str = "zho",
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source_video),
        "-i",
        str(subtitle_file),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map",
        "1:0",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-c:s",
        "mov_text",
        "-metadata:s:s:0",
        f"language={language}",
        "-movflags",
        "+faststart",
        str(output_file),
    ]


def render_softsub(
    source_video: Path,
    subtitle_file: Path,
    output_file: Path,
    *,
    language: str,
    ffmpeg: str = "ffmpeg",
) -> Path:
    """Mux a selectable subtitle track without recompressing video or audio."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temp = output_file.with_name(f"{output_file.stem}.partial{output_file.suffix}")
    try:
        run_command(
            build_softsub_command(
                source_video,
                subtitle_file,
                temp,
                language=language,
                ffmpeg=ffmpeg,
            )
        )
    except ExternalToolError as exc:
        raise LocalizerError(f"FFmpeg soft-subtitle muxing failed.\n{exc}") from exc
    if not temp.is_file() or temp.stat().st_size == 0:
        raise LocalizerError("FFmpeg reported success but did not create a soft-subtitle video.")
    temp.replace(output_file)
    return output_file
