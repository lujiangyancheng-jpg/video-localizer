from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .errors import ConfigurationError

MINIMUM_OUTPUT_FREE_BYTES = 20 * 1024**3

# The bundled Argos models only cover the two fast, fully offline directions below.
# All other pairs deliberately require a capable local LLM (Ollama) or an API.
TRANSLATION_DIRECTIONS = (
    "en-to-zh",
    "zh-to-en",
    "en-to-ja",
    "en-to-ko",
    "en-to-es",
    "en-to-fr",
    "en-to-de",
    "en-to-pt",
    "en-to-ru",
    "en-to-ar",
    "zh-to-ja",
    "zh-to-ko",
    "zh-to-es",
    "zh-to-fr",
    "zh-to-de",
    "zh-to-pt",
    "zh-to-ru",
    "zh-to-ar",
)
FAST_OFFLINE_DIRECTIONS = frozenset({"en-to-zh", "zh-to-en"})
LOCAL_AI_ONLY_DIRECTIONS = frozenset(TRANSLATION_DIRECTIONS) - FAST_OFFLINE_DIRECTIONS
LANGUAGE_NAMES = {
    "zh": "Simplified Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ru": "Russian",
    "ar": "Arabic",
}
FFMPEG_LANGUAGE_CODES = {
    "zh": "zho",
    "en": "eng",
    "ja": "jpn",
    "ko": "kor",
    "es": "spa",
    "fr": "fra",
    "de": "deu",
    "pt": "por",
    "ru": "rus",
    "ar": "ara",
}


def language_pair(direction: str) -> tuple[str, str]:
    """Return the source and target ISO language codes for a supported direction."""
    if direction not in TRANSLATION_DIRECTIONS:
        raise ConfigurationError(f"Unsupported translation direction: {direction}")
    source, target = direction.split("-to-", maxsplit=1)
    return source, target


def requires_local_ai_or_api(direction: str) -> bool:
    return direction in LOCAL_AI_ONLY_DIRECTIONS


def default_output_directory() -> Path:
    """Choose a user-owned local media folder instead of the application checkout."""
    home = Path(os.environ.get("USERPROFILE", "~")).expanduser() if os.name == "nt" else Path.home()
    return home / "Videos" / "YouTube Chinese Localizer"


def is_onedrive_directory(directory: Path, *, environment: Mapping[str, str] | None = None) -> bool:
    """Return whether *directory* is located below a configured OneDrive root."""
    environment = os.environ if environment is None else environment
    candidate = directory.expanduser().resolve()
    for variable in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        raw_root = environment.get(variable)
        if not raw_root:
            continue
        root = Path(raw_root).expanduser().resolve()
        if candidate == root or root in candidate.parents:
            return True
    return any(part.casefold() == "onedrive" for part in candidate.parts)


def output_directory_advice(directory: Path) -> str:
    """Describe storage risks before a large video job starts without creating files."""
    candidate = directory.expanduser().resolve()
    if is_onedrive_directory(candidate):
        return "此位置位于 OneDrive，同步大视频可能拖慢下载和压制；建议使用本地磁盘。"

    probe = candidate
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return "开始时会检查该输出位置是否可写。"
    free_gib = free / 1024**3
    if free < MINIMUM_OUTPUT_FREE_BYTES:
        return f"可用空间仅 {free_gib:.1f} GiB；高画质视频建议至少保留 20 GiB。"
    return f"本地磁盘可用空间 {free_gib:.1f} GiB。"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DownloadConfig(StrictModel):
    format: str = "bestvideo+bestaudio/best"
    format_sort: list[str] = Field(
        default_factory=lambda: ["res", "fps", "br", "size"], min_length=1
    )
    prefer_mp4: bool = True
    download_thumbnail: bool = True
    download_metadata: bool = True
    concurrent_fragment_downloads: int = Field(default=4, ge=1, le=8)
    retry_attempts: int = Field(default=2, ge=1, le=3)
    retry_delay_seconds: int = Field(default=8, ge=1, le=60)


class TranscriptionConfig(StrictModel):
    model: str = "medium"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    compute_type: str = "auto"
    # 0 chooses a conservative value from the actual CPU and RAM. This leaves capacity for
    # Windows and prevents the CPU-only fallback from making modest computers unresponsive.
    cpu_threads: int = Field(default=0, ge=0, le=32)
    beam_size: int = Field(default=5, ge=1, le=20)
    vad_filter: bool = True
    word_timestamps: bool = True


class TranslationConfig(StrictModel):
    direction: Literal[
        "en-to-zh",
        "zh-to-en",
        "en-to-ja",
        "en-to-ko",
        "en-to-es",
        "en-to-fr",
        "en-to-de",
        "en-to-pt",
        "en-to-ru",
        "en-to-ar",
        "zh-to-ja",
        "zh-to-ko",
        "zh-to-es",
        "zh-to-fr",
        "zh-to-de",
        "zh-to-pt",
        "zh-to-ru",
        "zh-to-ar",
    ] = "en-to-zh"
    provider: Literal["manual", "offline", "ollama", "openai-compatible"] = "manual"
    model: str = ""
    endpoint: str = ""
    batch_size: int = Field(default=40, ge=1, le=200)
    preserve_timestamps: bool = True
    glossary_file: str = "glossary.yaml"
    offline_model_directory: Path = Path("~/.youtube-chinese-localizer/models/translate-en_zh-1_9")
    offline_model_url: str = "https://argos-net.com/v1/translate-en_zh-1_9.argosmodel"
    offline_zh_en_model_directory: Path = Path(
        "~/.youtube-chinese-localizer/models/translate-zh_en-1_9"
    )
    offline_zh_en_model_url: str = "https://argos-net.com/v1/translate-zh_en-1_9.argosmodel"
    offline_device: Literal["auto", "cpu", "cuda"] = "auto"
    offline_compute_type: str = "auto"
    offline_auto_download: bool = True
    ollama_endpoint: str = "http://localhost:11434"
    ollama_model: str = "qwen3:4b"
    ollama_context_tokens: int = Field(default=4096, ge=2048, le=8192)
    ollama_auto_pull: bool = True
    ollama_timeout_seconds: int = Field(default=600, ge=30, le=3600)

    @model_validator(mode="after")
    def require_capable_translator_for_extra_languages(self) -> TranslationConfig:
        if requires_local_ai_or_api(self.direction) and self.provider not in {
            "ollama",
            "openai-compatible",
        }:
            raise ValueError(
                "Translations to Japanese, Korean, Spanish, French, German, Portuguese, "
                "Russian, or Arabic require the local AI (Ollama) or an OpenAI-compatible API."
            )
        return self


class SubtitleConfig(StrictModel):
    format: Literal["srt", "ass"] = "ass"
    font: str = "Noto Sans CJK SC"
    font_size: int = Field(default=48, ge=12, le=120)
    english_font_size: int = Field(default=34, ge=10, le=100)
    position_x_percent: int = Field(default=50, ge=2, le=98)
    position_y_percent: int = Field(default=96, ge=2, le=98)
    outline: int = Field(default=3, ge=0, le=10)
    shadow: int = Field(default=1, ge=0, le=10)
    margin_v: int = Field(default=45, ge=0, le=500)
    max_lines: int = Field(default=2, ge=1, le=4)
    max_chinese_chars_per_line: int = Field(default=20, ge=8, le=40)
    preserve_sound_descriptions: bool = True


class RenderConfig(StrictModel):
    # Auto verifies NVIDIA, Intel Quick Sync, AMD AMF, and (on macOS) VideoToolbox in that order,
    # then falls back to libx264. Explicit codecs remain available for reproducible workflows.
    codec: str = "auto"
    crf: int = Field(default=17, ge=0, le=51)
    preset: str = "medium"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    faststart: bool = True
    copy_audio_when_possible: bool = True
    soft_subtitles: bool = True
    # None means keep the original dimensions/frame rate. Direct-download mode applies lower
    # requested limits with one ordinary conversion; it never enlarges pixels unless optional
    # AI super resolution is enabled.
    output_height: int | None = Field(default=None, ge=144, le=4320)
    output_fps: int | None = Field(default=None, ge=1, le=240)


class EnhancementConfig(StrictModel):
    """Optional, fully local AI video restoration settings.

    ``off`` keeps the original source pixels.  The two enabled modes use the portable
    NCNN/Vulkan runtime so NVIDIA, AMD, and Intel GPUs can share one package.
    ``target_height`` is taken from ``render.output_height`` when the user chose one;
    otherwise a conservative 2x target capped at ``max_auto_height`` is used.
    """

    mode: Literal["off", "general", "animation"] = "off"
    scale: Literal[2, 4] = 2
    max_auto_height: int = Field(default=2160, ge=720, le=4320)
    tile_size: int = Field(default=0, ge=0, le=1024)
    batch_frames: int = Field(default=0, ge=0, le=120)


class RightsConfig(StrictModel):
    """User-supplied rights basis recorded beside every processed project.

    This is an audit record, not an automated legal conclusion.  Older config files remain
    valid through the explicit ``unspecified`` value and are marked for human review.
    """

    basis: Literal[
        "unspecified",
        "owned",
        "written_permission",
        "cc_by",
        "cc_by_sa",
        "cc_by_nc",
        "public_domain",
        "other",
    ] = "unspecified"
    rights_holder: str = Field(default="", max_length=300)
    license_url: str = Field(default="", max_length=2000)
    permission_reference: str = Field(default="", max_length=1000)
    attribution_text: str = Field(default="", max_length=2000)
    allow_translation: bool = True
    allow_redistribution: bool = True
    commercial_use: bool = False


class PublishingConfig(StrictModel):
    generate_metadata: bool = True
    attribution_template: str = (
        "原视频作者：{channel}\n"
        "原视频链接：{source_url}\n"
        "本视频经授权或依据相应许可进行翻译与转载。"
    )
    permission_note: str = "请在发布前填写实际授权或许可证信息。"


class AppConfig(StrictModel):
    output_directory: Path = Field(default_factory=default_output_directory)
    subtitle_language: str = "zh-CN"
    subtitle_mode: Literal["download_only", "chinese", "bilingual_en_zh", "bilingual_zh_en"] = (
        "chinese"
    )
    download: DownloadConfig = DownloadConfig()
    transcription: TranscriptionConfig = TranscriptionConfig()
    translation: TranslationConfig = TranslationConfig()
    subtitles: SubtitleConfig = SubtitleConfig()
    render: RenderConfig = RenderConfig()
    enhancement: EnhancementConfig = EnhancementConfig()
    rights: RightsConfig = RightsConfig()
    publishing: PublishingConfig = PublishingConfig()

    @model_validator(mode="after")
    def restrict_bilingual_layout_to_chinese_and_english(self) -> AppConfig:
        if self.subtitle_mode in {
            "bilingual_en_zh",
            "bilingual_zh_en",
        } and requires_local_ai_or_api(self.translation.direction):
            raise ValueError(
                "Bilingual layouts are available only for Chinese-English translation. "
                "Choose target-language subtitles for other languages."
            )
        return self


def migrate_config_data(data: dict) -> dict:
    """Remove retired settings while keeping saved projects forward-compatible."""
    migrated = dict(data)
    download = migrated.get("download")
    if isinstance(download, dict) and "prefer_youtube_chinese" in download:
        migrated_download = dict(download)
        migrated_download.pop("prefer_youtube_chinese", None)
        migrated["download"] = migrated_download
    return migrated


def validate_config_data(data: dict) -> AppConfig:
    try:
        return AppConfig.model_validate(migrate_config_data(data))
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid configuration:\n{exc}") from exc


def load_config(path: Path | None = None) -> AppConfig:
    load_dotenv(override=False)
    data: dict = {}
    if path:
        if not path.is_file():
            raise ConfigurationError(f"Configuration file does not exist: {path}")
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigurationError(f"Cannot read YAML configuration {path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigurationError("The configuration root must be a YAML mapping.")
        data = loaded

    endpoint = os.getenv("OPENAI_COMPATIBLE_ENDPOINT")
    model = os.getenv("OPENAI_COMPATIBLE_MODEL")
    if endpoint or model:
        translation = dict(data.get("translation", {}))
        if endpoint:
            translation["endpoint"] = endpoint
        if model:
            translation["model"] = model
        data["translation"] = translation

    return validate_config_data(data)
