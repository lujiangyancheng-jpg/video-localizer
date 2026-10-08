from __future__ import annotations

import shutil
import subprocess

import pytest

from youtube_localizer.errors import LocalizerError
from youtube_localizer.rendering.validation import validate_rendered_video


@pytest.mark.integration
def test_silent_video_validation_checks_requested_specs(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg required")
    video = tmp_path / "silent.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=blue:size=160x90:rate=10:duration=1",
            "-c:v",
            "libx264",
            str(video),
        ],
        check=True,
    )
    result = validate_rendered_video(
        video, expected_duration=1, require_audio=False, expected_height=90, expected_frame_rate=10
    )
    assert not any(stream["codec_type"] == "audio" for stream in result["streams"])
    with pytest.raises(LocalizerError, match="audio"):
        validate_rendered_video(video, expected_duration=1)
    with pytest.raises(LocalizerError, match="height"):
        validate_rendered_video(
            video, expected_duration=1, require_audio=False, expected_height=720
        )
    with pytest.raises(LocalizerError, match="frame rate"):
        validate_rendered_video(
            video, expected_duration=1, require_audio=False, expected_frame_rate=30
        )
