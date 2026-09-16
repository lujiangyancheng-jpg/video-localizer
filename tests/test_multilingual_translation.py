from __future__ import annotations

import pytest

from youtube_localizer.config import AppConfig, ConfigurationError, validate_config_data
from youtube_localizer.gui import build_process_command, language_name
from youtube_localizer.models import ProjectPaths, SubtitleCue
from youtube_localizer.pipeline import (
    _target_ass,
    _target_subtitle,
    _write_localized_subtitles,
    rendered_output,
)
from youtube_localizer.subtitles.parser import parse_subtitle


def test_extra_language_requires_local_ai_or_api() -> None:
    with pytest.raises(ConfigurationError, match="require the local AI"):
        validate_config_data({"translation": {"direction": "zh-to-es", "provider": "offline"}})

    config = validate_config_data({"translation": {"direction": "zh-to-es", "provider": "ollama"}})

    assert config.translation.direction == "zh-to-es"
    assert config.translation.provider == "ollama"


def test_extra_language_rejects_bilingual_layout() -> None:
    with pytest.raises(ConfigurationError, match="Bilingual layouts"):
        validate_config_data(
            {
                "subtitle_mode": "bilingual_en_zh",
                "translation": {"direction": "en-to-ja", "provider": "ollama"},
            }
        )


def test_gui_command_accepts_local_ai_for_extra_language() -> None:
    command = build_process_command(
        "video.mp4",
        subtitle_mode="chinese",
        translation_provider="ollama",
        translation_direction="zh-to-es",
    )

    assert command[command.index("--translation-direction") + 1] == "zh-to-es"
    assert language_name("zh-to-es", target=True) == "西班牙语"


def test_gui_command_rejects_fast_offline_for_extra_language() -> None:
    with pytest.raises(ValueError, match="本地 AI"):
        build_process_command(
            "video.mp4",
            subtitle_mode="chinese",
            translation_provider="offline",
            translation_direction="en-to-ja",
        )


def test_extra_language_writes_own_subtitle_and_render_names(tmp_path) -> None:
    project = ProjectPaths(tmp_path / "project")
    project.create()
    config = AppConfig.model_validate(
        {
            "translation": {"direction": "zh-to-es", "provider": "ollama"},
            "publishing": {"generate_metadata": False},
        }
    )
    translated = [SubtitleCue(id=1, start_ms=0, end_ms=1000, text="Hola a todos")]

    outputs, warnings = _write_localized_subtitles(
        project,
        [],
        [],
        config,
        target_cues=translated,
    )

    assert warnings == []
    assert outputs == [project.subtitles / "es.srt", project.subtitles / "es.ass"]
    assert parse_subtitle(_target_subtitle(project, config))[0].text == "Hola a todos"
    assert _target_ass(project, config).is_file()
    assert rendered_output(project, config) == project.rendered / "es_hardsub.mp4"
