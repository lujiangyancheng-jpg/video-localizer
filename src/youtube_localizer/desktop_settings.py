"""Small, versioned preferences store for the desktop launcher.

Secrets are deliberately excluded.  API keys continue to live only in the
current process environment or in the launcher's in-memory entry field.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import default_output_directory
from .onboarding import onboarding_state_directory
from .utils.files import atomic_write_json, load_json

SETTINGS_SCHEMA_VERSION = 2
VALID_DIRECTIONS = frozenset(
    f"{source}-to-{target}"
    for source in ("en", "zh")
    for target in ("zh", "en", "ja", "ko", "es", "fr", "de", "pt", "ru", "ar")
    if source != target
)
VALID_SUBTITLE_MODES = frozenset({"download_only", "chinese", "bilingual_en_zh", "bilingual_zh_en"})
VALID_TRANSLATION_PROVIDERS = frozenset({"manual", "offline", "ollama", "openai-compatible"})
VALID_OUTPUT_QUALITIES = frozenset({"best", "high", "standard"})
VALID_OUTPUT_FPS = frozenset({None, 30, 60})
VALID_OUTPUT_HEIGHTS = frozenset({None, 480, 720, 1080, 1440, 2160, 4320})
VALID_ENHANCEMENT_MODES = frozenset({"off", "general", "animation"})
VALID_UPDATE_CHANNELS = frozenset({"stable", "development"})


@dataclass(frozen=True, slots=True)
class DesktopSettings:
    schema_version: int = SETTINGS_SCHEMA_VERSION
    direction: str = "en-to-zh"
    subtitle_mode: str = "chinese"
    translation_provider: str = "offline"
    font_size: int = 48
    subtitle_x_percent: int = 50
    subtitle_y_percent: int = 96
    output_quality: str = "best"
    output_fps: int | None = None
    output_height: int | None = None
    enhancement_mode: str = "off"
    output_directory: str = ""
    update_channel: str = "stable"
    resume: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_desktop_settings() -> DesktopSettings:
    return DesktopSettings(output_directory=str(default_output_directory()))


def desktop_settings_path(environment: Mapping[str, str] | None = None) -> Path:
    return onboarding_state_directory(environment) / "desktop-settings.json"


def _supported(value: object, supported: frozenset[Any], fallback: Any) -> Any:
    return value if value in supported else fallback


def _bounded_int(value: object, *, minimum: int, maximum: int, fallback: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return min(maximum, max(minimum, value))


def _settings_from_mapping(data: Mapping[str, Any]) -> DesktopSettings:
    defaults = default_desktop_settings()
    output_directory = data.get("output_directory")
    if not isinstance(output_directory, str) or not output_directory.strip():
        output_directory = defaults.output_directory

    return DesktopSettings(
        direction=_supported(data.get("direction"), VALID_DIRECTIONS, defaults.direction),
        subtitle_mode=_supported(
            data.get("subtitle_mode"), VALID_SUBTITLE_MODES, defaults.subtitle_mode
        ),
        translation_provider=_supported(
            data.get("translation_provider"),
            VALID_TRANSLATION_PROVIDERS,
            defaults.translation_provider,
        ),
        font_size=_bounded_int(
            data.get("font_size"), minimum=20, maximum=96, fallback=defaults.font_size
        ),
        subtitle_x_percent=_bounded_int(
            data.get("subtitle_x_percent"),
            minimum=2,
            maximum=98,
            fallback=defaults.subtitle_x_percent,
        ),
        subtitle_y_percent=_bounded_int(
            data.get("subtitle_y_percent"),
            minimum=2,
            maximum=98,
            fallback=defaults.subtitle_y_percent,
        ),
        output_quality=_supported(
            data.get("output_quality"), VALID_OUTPUT_QUALITIES, defaults.output_quality
        ),
        output_fps=_supported(data.get("output_fps"), VALID_OUTPUT_FPS, defaults.output_fps),
        output_height=_supported(
            data.get("output_height"), VALID_OUTPUT_HEIGHTS, defaults.output_height
        ),
        enhancement_mode=_supported(
            data.get("enhancement_mode"),
            VALID_ENHANCEMENT_MODES,
            defaults.enhancement_mode,
        ),
        output_directory=output_directory.strip(),
        update_channel=_supported(
            data.get("update_channel"), VALID_UPDATE_CHANNELS, defaults.update_channel
        ),
        resume=data.get("resume") if isinstance(data.get("resume"), bool) else defaults.resume,
    )


def load_desktop_settings(
    *, environment: Mapping[str, str] | None = None, settings_path: Path | None = None
) -> DesktopSettings:
    path = settings_path or desktop_settings_path(environment)
    try:
        data = load_json(path)
    except (OSError, ValueError, TypeError):
        return default_desktop_settings()
    if not isinstance(data, dict):
        return default_desktop_settings()
    return _settings_from_mapping(data)


def save_desktop_settings(
    settings: DesktopSettings,
    *,
    environment: Mapping[str, str] | None = None,
    settings_path: Path | None = None,
) -> Path:
    path = settings_path or desktop_settings_path(environment)
    atomic_write_json(path, settings.to_dict())
    return path
