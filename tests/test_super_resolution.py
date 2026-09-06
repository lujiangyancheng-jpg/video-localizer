from __future__ import annotations

import io
import json
import shutil
import struct
import subprocess
import zlib

import pytest

from youtube_localizer.config import EnhancementConfig, RenderConfig
from youtube_localizer.enhancement.super_resolution import (
    _run_upscaler_batch,
    build_enhanced_encode_command,
    build_upscaler_command,
    read_png_frame,
    super_resolution_target_height,
)
from youtube_localizer.errors import ExternalToolError


def _png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b"")


def test_target_height_is_bounded_and_never_downscales() -> None:
    enhancement = EnhancementConfig(mode="general")

    assert super_resolution_target_height(720, RenderConfig(), enhancement) == 1440
    assert (
        super_resolution_target_height(1080, RenderConfig(output_height=2160), enhancement) == 2160
    )
    assert (
        super_resolution_target_height(1080, RenderConfig(output_height=720), enhancement) == 1080
    )
    assert (
        super_resolution_target_height(480, RenderConfig(output_height=4320), enhancement) == 1920
    )


def test_upscaler_command_selects_model_scale_and_device(tmp_path) -> None:
    command = build_upscaler_command(
        tmp_path / "waifu2x.exe",
        tmp_path / "input",
        tmp_path / "output",
        tmp_path,
        EnhancementConfig(mode="animation", scale=4, tile_size=128),
        gpu_id=1,
    )

    assert command[command.index("-m") + 1].endswith("models-cunet")
    assert command[command.index("-s") + 1] == "4"
    assert command[command.index("-t") + 1] == "128"
    assert command[command.index("-g") + 1] == "1"


def test_png_reader_splits_a_continuous_pipe() -> None:
    image = _png()
    stream = io.BytesIO(image + image)

    assert read_png_frame(stream) == image
    assert read_png_frame(stream) == image
    assert read_png_frame(stream) is None


def test_upscaler_probes_an_alternate_gpu_after_a_crash(tmp_path, monkeypatch) -> None:
    input_directory = tmp_path / "input"
    output_directory = tmp_path / "output"
    input_directory.mkdir()
    (input_directory / "frame000000001.png").write_bytes(_png())
    attempted: list[int] = []

    def fake_run(command: list[str], **_kwargs) -> None:
        gpu = int(command[command.index("-g") + 1])
        attempted.append(gpu)
        if gpu == 0:
            raise ExternalToolError("driver crash", command=command)
        output_directory.mkdir(exist_ok=True)
        (output_directory / "frame000000001.png").write_bytes(_png())

    monkeypatch.setattr("youtube_localizer.enhancement.super_resolution.run_command", fake_run)

    selected = _run_upscaler_batch(
        tmp_path / "waifu2x.exe",
        input_directory,
        output_directory,
        tmp_path,
        EnhancementConfig(mode="general"),
        frame_count=1,
        selected_gpu=None,
    )

    assert selected == 1
    assert attempted == [0, 1]


def test_enhanced_encoder_preserves_optional_audio_and_target_height(tmp_path) -> None:
    command = build_enhanced_encode_command(
        tmp_path / "source.mp4",
        tmp_path / "output.mp4",
        frame_rate=23.976,
        target_height=2160,
        source_audio_codec="aac",
        render=RenderConfig(codec="libx264"),
        ffmpeg="ffmpeg.exe",
    )

    assert "1:a?" in command
    assert "scale=-2:2160:flags=lanczos" in command
    assert command[command.index("-c:a") + 1] == "copy"


def test_output_fps_is_applied_and_progress_is_visible(tmp_path):
    from youtube_localizer.gui import progress_update_from_output

    command = build_enhanced_encode_command(
        tmp_path / "in.mp4",
        tmp_path / "out.mp4",
        frame_rate=60,
        target_height=2160,
        source_audio_codec="aac",
        render=RenderConfig(output_fps=30),
        ffmpeg="ffmpeg",
    )
    assert "fps=30" in command[command.index("-vf") + 1]
    value, message = progress_update_from_output(
        "AI super resolution: 150 frames complete (50.0%); 3.00 fps; ETA 50s.",
        provider="download_only",
        enhancement=True,
    )
    assert 10 < value < 99
    assert "3.00" in message and "50" in message
    value, _ = progress_update_from_output(
        "[download] 100.0%",
        provider="download_only",
        enhancement=True,
    )
    assert value == 10


@pytest.mark.integration
@pytest.mark.parametrize("corrupt_checkpoint", [False, True])
def test_segment_resume_preserves_audio_duration_and_output_fps(
    tmp_path, monkeypatch, corrupt_checkpoint
):
    from youtube_localizer.enhancement import super_resolution as sr

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg tools are required")
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=12:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-y",
            str(source),
        ],
        check=True,
    )
    executable = tmp_path / "test-runtime"
    executable.write_bytes(b"test-runtime")
    monkeypatch.setattr(sr, "super_resolution_runtime", lambda: (executable, tmp_path))
    monkeypatch.setattr(sr, "SEGMENT_FRAMES", 6)
    monkeypatch.setattr(sr, "resolve_render_backend", lambda render, exe: (exe, render))
    calls = []
    fail = [True]
    original_stream = sr._enhance_video_stream

    def stream(*args, **kwargs):
        calls.append(kwargs["start_frame"])
        if kwargs["start_frame"] == 6 and fail[0]:
            fail[0] = False
            raise ExternalToolError("simulated interruption", command=[])
        return original_stream(*args, **kwargs)

    def upscale(_exe, inputs, outputs, *_args, **_kwargs):
        for frame in inputs.glob("*.png"):
            shutil.copy2(frame, outputs / frame.name)
        return 0

    monkeypatch.setattr(sr, "_enhance_video_stream", stream)
    monkeypatch.setattr(sr, "_run_upscaler_batch", upscale)
    output = tmp_path / "enhanced.mp4"
    kwargs = dict(
        source_width=64,
        source_height=64,
        frame_rate=12,
        duration=3,
        source_audio_codec="aac",
        render=RenderConfig(codec="libx264", output_fps=6),
        enhancement=EnhancementConfig(mode="general"),
        working_directory=tmp_path / "work",
        ffmpeg=ffmpeg,
    )
    with pytest.raises(ExternalToolError, match="simulated interruption"):
        sr.enhance_video(source, output, **kwargs)
    assert not output.exists()
    if corrupt_checkpoint:
        checkpoint = next((tmp_path / "work" / "checkpoints").glob("*/segment-*.mp4"))
        checkpoint.write_bytes(b"damaged-checkpoint")
    sr.enhance_video(source, output, **kwargs)
    assert calls == ([0, 6, 0, 6, 12] if corrupt_checkpoint else [0, 6, 6, 12])
    info = json.loads(
        subprocess.check_output(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)]
        )
    )
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert video["height"] == 128
    assert video["avg_frame_rate"] == "6/1"
    assert int(video["nb_frames"]) == 18
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert abs(float(info["format"]["duration"]) - 3) < 0.1
    assert not list((tmp_path / "work" / "checkpoints").glob("*/segment-*.mp4"))
