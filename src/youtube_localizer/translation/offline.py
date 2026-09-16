from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import tempfile
import zipfile
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

import httpx

from ..errors import LocalizerError
from ..models import SubtitleCue
from ..resources import find_bundled_model
from .base import TranslationContext, TranslationProvider
from .cache import TranslationCache

LOGGER = logging.getLogger(__name__)
MAX_MODEL_DOWNLOAD_BYTES = 1_000_000_000
MODEL_REQUIRED_PATHS = (
    "metadata.json",
    "sentencepiece.model",
    "model/config.json",
    "model/model.bin",
)
EN_SENTENCE_END_RE = re.compile(r"[.!?](?:[\"')\]]+)?$")
ZH_SENTENCE_END_RE = re.compile(r"[。！？!?](?:[\"”’）》\]]+)?$")
EN_SENTENCE_MARK_RE = re.compile(r"[.!?]")
ZH_SENTENCE_MARK_RE = re.compile(r"[。！？!?]")


def _replace_term(text: str, source: str, target: str) -> str:
    return re.sub(re.escape(source), lambda _match: target, text, flags=re.IGNORECASE)


def enforce_glossary(
    source_text: str,
    translated_text: str,
    glossary: dict[str, str],
    *,
    target_code: str,
    default_translations: dict[str, str] | None = None,
) -> str:
    output = translated_text
    default_translations = default_translations or {}
    for source, target in sorted(glossary.items(), key=lambda item: len(item[0]), reverse=True):
        if not re.search(re.escape(source), source_text, flags=re.IGNORECASE):
            continue
        if target in output:
            continue
        default_translation = default_translations.get(source, "")
        if default_translation and re.search(
            re.escape(default_translation), output, flags=re.IGNORECASE
        ):
            output = _replace_term(output, default_translation, target)
            continue
        if re.search(re.escape(source), output, flags=re.IGNORECASE):
            output = _replace_term(output, source, target)
        else:
            output = f"{output} ({target})" if target_code == "en" else f"{output}（{target}）"
    return output


def group_sentence_cues(
    cues: list[SubtitleCue],
    *,
    source_code: str,
    max_cues: int = 4,
    max_characters: int = 240,
    max_gap_ms: int = 1200,
) -> list[list[SubtitleCue]]:
    groups: list[list[SubtitleCue]] = []
    current: list[SubtitleCue] = []
    end_pattern = ZH_SENTENCE_END_RE if source_code == "zh" else EN_SENTENCE_END_RE
    for cue in cues:
        if current and cue.start_ms - current[-1].end_ms > max_gap_ms:
            groups.append(current)
            current = []
        current.append(cue)
        combined_length = sum(len(item.text) for item in current)
        if (
            end_pattern.search(cue.text.strip())
            or len(current) >= max_cues
            or combined_length >= max_characters
        ):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def group_paragraph_cues(
    cues: list[SubtitleCue],
    *,
    source_code: str,
    max_cues: int = 20,
    max_characters: int = 1000,
    max_gap_ms: int = 1800,
    max_duration_ms: int = 50_000,
    target_sentences: int = 4,
    minimum_duration_ms: int = 15_000,
) -> list[list[SubtitleCue]]:
    """Group rolling captions into paragraph-sized translation units.

    Automatic captions often split one spoken sentence across many two-second cues. A soft
    boundary waits for several complete sentences and enough spoken time; hard limits prevent
    an unpunctuated transcript from exceeding the compact model's useful context window.
    """
    groups: list[list[SubtitleCue]] = []
    current: list[SubtitleCue] = []
    character_count = 0
    sentence_count = 0
    end_pattern = ZH_SENTENCE_END_RE if source_code == "zh" else EN_SENTENCE_END_RE
    mark_pattern = ZH_SENTENCE_MARK_RE if source_code == "zh" else EN_SENTENCE_MARK_RE

    def flush() -> None:
        nonlocal current, character_count, sentence_count
        if current:
            groups.append(current)
        current = []
        character_count = 0
        sentence_count = 0

    for cue in cues:
        if current and cue.start_ms - current[-1].end_ms > max_gap_ms:
            flush()
        current.append(cue)
        character_count += len(cue.text)
        sentence_count += len(mark_pattern.findall(cue.text))
        duration = current[-1].end_ms - current[0].start_ms
        soft_boundary = (
            duration >= minimum_duration_ms
            and sentence_count >= target_sentences
            and end_pattern.search(cue.text.strip()) is not None
        )
        hard_boundary = (
            len(current) >= max_cues
            or character_count >= max_characters
            or duration >= max_duration_ms
        )
        if soft_boundary or hard_boundary:
            flush()
    flush()
    return groups


def _target_units(text: str, target_code: str) -> list[str]:
    if target_code == "en":
        return re.findall(r"\S+", text)
    return re.findall(r"[\u3400-\u9fff]|[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*|[^\s]", text)


def _join_target_units(units: list[str], target_code: str) -> str:
    if target_code == "en":
        return " ".join(units)
    output = ""
    for unit in units:
        if (
            output
            and output[-1].isascii()
            and output[-1].isalnum()
            and unit[0].isascii()
            and unit[0].isalnum()
        ):
            output += " "
        output += unit
    return output


def split_group_translation(
    translated_text: str,
    source_cues: list[SubtitleCue],
    *,
    source_code: str,
    target_code: str,
) -> list[str] | None:
    if len(source_cues) == 1:
        return [translated_text.strip()]
    units = _target_units(translated_text, target_code)
    if len(units) < len(source_cues):
        return None
    weights = [
        max(1, len(re.findall(r"\S+", cue.text)) if source_code == "en" else len(cue.text))
        for cue in source_cues
    ]
    total_weight = sum(weights)
    boundaries = [0]
    consumed_weight = 0
    for index, weight in enumerate(weights[:-1], start=1):
        consumed_weight += weight
        boundary = round(len(units) * consumed_weight / total_weight)
        boundary = max(boundaries[-1] + 1, boundary)
        boundary = min(boundary, len(units) - (len(source_cues) - index))
        boundaries.append(boundary)
    boundaries.append(len(units))
    parts = [
        _join_target_units(units[start:end], target_code).strip()
        for start, end in pairwise(boundaries)
    ]
    return parts if all(parts) else None


def segment_translated_paragraph(
    text: str,
    *,
    target_code: str,
    max_characters: int,
) -> list[str]:
    """Split a complete translation at semantic punctuation, not source cue boundaries."""
    normalized = " ".join(text.split()) if target_code == "en" else "".join(text.split())
    if not normalized:
        return []
    if target_code == "zh":
        sentences = re.findall(r"[^。！？!?]+[。！？!?]*", normalized)
        break_characters = "，,；;：:、"
    else:
        sentences = re.findall(r".+?(?:[.!?]+(?=\s|$)|$)", normalized)
        break_characters = ",;: "

    segments: list[str] = []
    for sentence in (value.strip() for value in sentences if value.strip()):
        sentence_segment_start = len(segments)
        remaining = sentence
        while len(remaining) > max_characters:
            candidates = [
                index + 1
                for index, character in enumerate(remaining[: max_characters + 1])
                if character in break_characters and index + 1 >= max_characters // 2
            ]
            boundary = candidates[-1] if candidates else max_characters
            segment = remaining[:boundary].strip()
            if segment:
                segments.append(segment)
            remaining = remaining[boundary:].strip()
        if remaining:
            punctuation = "。！？!?，,；;：:、."
            can_merge_short_tail = (
                len(segments) > sentence_segment_start
                and len(remaining) < max_characters // 4
                and len(segments[-1]) + len(remaining) <= round(max_characters * 1.2)
            )
            if can_merge_short_tail or (
                segments
                and len(remaining) <= 3
                and all(character in punctuation for character in remaining)
            ):
                segments[-1] += remaining
            else:
                segments.append(remaining)
    return segments


def paragraph_translation_to_cues(
    translated_text: str,
    source_cues: list[SubtitleCue],
    *,
    target_code: str,
    first_id: int = 1,
    max_characters: int = 40,
) -> list[SubtitleCue]:
    """Place natural translated phrases across a paragraph's original time range."""
    if not source_cues:
        return []
    segments = segment_translated_paragraph(
        translated_text,
        target_code=target_code,
        max_characters=max_characters,
    )
    if not segments:
        raise LocalizerError("Local AI returned an empty paragraph translation.")
    start_ms = source_cues[0].start_ms
    end_ms = source_cues[-1].end_ms
    duration = max(len(segments), end_ms - start_ms)
    weights = [max(1, len(_target_units(segment, target_code))) for segment in segments]
    total_weight = sum(weights)
    boundaries = [start_ms]
    minimum_duration = min(900, duration // len(segments))
    weighted_duration = max(0, duration - minimum_duration * len(segments))
    consumed_weight = 0
    for index, weight in enumerate(weights[:-1], start=1):
        consumed_weight += weight
        boundary = (
            start_ms
            + minimum_duration * index
            + round(weighted_duration * consumed_weight / total_weight)
        )
        boundary = max(boundaries[-1] + 1, boundary)
        boundary = min(boundary, end_ms - (len(segments) - index))
        boundaries.append(boundary)
    boundaries.append(end_ms)
    return [
        SubtitleCue(
            id=first_id + index,
            start_ms=segment_start,
            end_ms=segment_end,
            text=segment,
        )
        for index, (segment, (segment_start, segment_end)) in enumerate(
            zip(segments, pairwise(boundaries), strict=True)
        )
    ]


def select_offline_translation_device(requested_device: str) -> str:
    """Prefer reliable CPU translation unless CUDA was explicitly requested."""
    return "cpu" if requested_device == "auto" else requested_device


def validate_offline_model(
    model_directory: Path,
    *,
    source_code: str = "en",
    target_code: str = "zh",
) -> dict[str, Any] | None:
    """Return validated model metadata, or None when the directory is incomplete."""
    if not model_directory.is_dir():
        return None
    if any(not (model_directory / relative).is_file() for relative in MODEL_REQUIRED_PATHS):
        return None
    try:
        metadata = json.loads((model_directory / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if metadata.get("from_code") != source_code or metadata.get("to_code") != target_code:
        return None
    return metadata


def _validate_archive_members(archive: zipfile.ZipFile) -> None:
    total_size = 0
    for member in archive.infolist():
        path = PurePosixPath(member.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise LocalizerError("The offline translation model archive contains an unsafe path.")
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise LocalizerError("The offline translation model archive contains a symlink.")
        total_size += member.file_size
        if total_size > MAX_MODEL_DOWNLOAD_BYTES:
            raise LocalizerError("The offline translation model archive is unexpectedly large.")


def install_offline_model_archive(
    archive_path: Path,
    destination: Path,
    *,
    source_code: str = "en",
    target_code: str = "zh",
) -> Path:
    """Validate and atomically install an Argos-compatible translation model archive."""
    existing = validate_offline_model(destination, source_code=source_code, target_code=target_code)
    if existing is not None:
        return destination
    if destination.exists():
        raise LocalizerError(
            f"Offline model directory exists but is incomplete: {destination}. "
            "Rename or remove only that directory, then retry."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.install-", dir=destination.parent)
    )
    try:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                _validate_archive_members(archive)
                archive.extractall(temporary_root)
        except (OSError, zipfile.BadZipFile) as exc:
            raise LocalizerError(
                f"The offline translation model archive is invalid: {exc}"
            ) from exc
        candidates = [
            path
            for path in temporary_root.rglob("metadata.json")
            if validate_offline_model(path.parent, source_code=source_code, target_code=target_code)
            is not None
        ]
        if len(candidates) != 1:
            raise LocalizerError(
                "The offline translation archive does not contain exactly one valid "
                f"{source_code}-to-{target_code} model."
            )
        candidates[0].parent.replace(destination)
        return destination
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def _download_model_archive(
    url: str,
    destination_parent: Path,
    *,
    source_code: str,
    target_code: str,
) -> Path:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise LocalizerError("Offline model downloads require a valid HTTPS URL.")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".offline-model-", suffix=".argosmodel", dir=destination_parent
    )
    temporary_path = Path(temporary_name)
    downloaded = 0
    try:
        LOGGER.info(
            "Downloading the offline %s-to-%s model for first-time use…",
            source_code,
            target_code,
        )
        timeout = httpx.Timeout(connect=30, read=600, write=30, pool=30)
        with (
            os.fdopen(descriptor, "wb") as output,
            httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as response,
        ):
            response.raise_for_status()
            length = int(response.headers.get("content-length") or 0)
            if length > MAX_MODEL_DOWNLOAD_BYTES:
                raise LocalizerError("The offline translation model download is too large.")
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                downloaded += len(chunk)
                if downloaded > MAX_MODEL_DOWNLOAD_BYTES:
                    raise LocalizerError("The offline translation model download is too large.")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if downloaded == 0:
            raise LocalizerError("The offline translation model download was empty.")
        LOGGER.info("Offline translation model downloaded (%.1f MB).", downloaded / 1_048_576)
        return temporary_path
    except httpx.HTTPError as exc:
        temporary_path.unlink(missing_ok=True)
        raise LocalizerError(
            "Could not download the offline translation model. Check the internet connection "
            f"and retry. Details: {exc}"
        ) from exc
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def ensure_offline_model(
    model_directory: Path,
    *,
    model_url: str,
    auto_download: bool,
    source_code: str = "en",
    target_code: str = "zh",
) -> Path:
    model_directory = model_directory.expanduser()
    if (
        validate_offline_model(model_directory, source_code=source_code, target_code=target_code)
        is not None
    ):
        return model_directory
    if (bundled := find_bundled_model(model_directory.name)) and validate_offline_model(
        bundled, source_code=source_code, target_code=target_code
    ) is not None:
        LOGGER.info("Using bundled offline translation model at %s.", bundled)
        return bundled
    if model_directory.exists():
        raise LocalizerError(
            f"Offline model directory exists but is incomplete: {model_directory}."
        )
    if not auto_download:
        raise LocalizerError(
            f"Offline {source_code}-to-{target_code} model is not installed at "
            f"{model_directory}. "
            "Enable translation.offline_auto_download or install the model there."
        )
    model_directory.parent.mkdir(parents=True, exist_ok=True)
    archive = _download_model_archive(
        model_url,
        model_directory.parent,
        source_code=source_code,
        target_code=target_code,
    )
    try:
        installed = install_offline_model_archive(
            archive,
            model_directory,
            source_code=source_code,
            target_code=target_code,
        )
    finally:
        archive.unlink(missing_ok=True)
    LOGGER.info("Offline translation model installed at %s", installed)
    return installed


class LocalOfflineProvider(TranslationProvider):
    """Translate subtitle cues locally with CTranslate2 and SentencePiece."""

    def __init__(
        self,
        *,
        model_directory: Path,
        model_url: str,
        auto_download: bool,
        device: str,
        compute_type: str,
        cache: TranslationCache,
        source_code: str = "en",
        target_code: str = "zh",
    ) -> None:
        try:
            import ctranslate2
            import sentencepiece as spm
        except ImportError as exc:
            raise LocalizerError(
                "Offline translation dependencies are missing. Run: python -m pip install -e "
                '".[offline-translation]"'
            ) from exc

        self.model_directory = ensure_offline_model(
            model_directory,
            model_url=model_url,
            auto_download=auto_download,
            source_code=source_code,
            target_code=target_code,
        )
        self.metadata = (
            validate_offline_model(
                self.model_directory,
                source_code=source_code,
                target_code=target_code,
            )
            or {}
        )
        self.source_code = source_code
        self.target_code = target_code
        self.cache = cache
        self.processor = spm.SentencePieceProcessor(
            model_proto=(self.model_directory / "sentencepiece.model").read_bytes()
        )

        selected_device = select_offline_translation_device(device)
        selected_compute_type = compute_type
        if compute_type == "auto" and selected_device == "cpu":
            selected_compute_type = "int8"
        try:
            self.translator = ctranslate2.Translator(
                str(self.model_directory / "model"),
                device=selected_device,
                compute_type=selected_compute_type,
            )
        except Exception as exc:
            raise LocalizerError(
                f"Could not load the offline translation model on {selected_device}: {exc}"
            ) from exc
        self.device = selected_device
        self.compute_type = selected_compute_type
        LOGGER.info(
            "Offline translation ready on %s (%s).",
            self.device,
            self.compute_type,
        )

    def _translate_tokens(self, tokenized: list[list[str]]):
        arguments = {
            "replace_unknowns": True,
            "max_batch_size": 2048,
            "batch_type": "tokens",
            "beam_size": 4,
            "num_hypotheses": 1,
            "length_penalty": 0.2,
        }
        return self.translator.translate_batch(tokenized, **arguments)

    def _glossary_default_translations(self, sources: list[str]) -> dict[str, str]:
        if not sources:
            return {}
        glossary_results = self._translate_tokens(
            [self.processor.encode(source, out_type=str) for source in sources]
        )
        return {
            source: self.processor.decode_pieces(result.hypotheses[0])
            .replace(chr(0x2581), " ")
            .strip()
            for source, result in zip(sources, glossary_results, strict=True)
        }

    def translate_batch(
        self,
        cues: list[SubtitleCue],
        context: TranslationContext,
    ) -> list[SubtitleCue]:
        original_texts = [cue.text for cue in cues]
        payload = {
            "provider": "offline-argos-opus-paragraph-v3",
            "source_code": self.source_code,
            "target_code": self.target_code,
            "model_version": self.metadata.get("package_version", "unknown"),
            "texts": original_texts,
            "glossary": context.glossary,
        }
        key = self.cache.key(payload)
        cached = self.cache.get(key)
        translations: list[str]
        if isinstance(cached, dict) and isinstance(cached.get("translations"), list):
            translations = [str(value) for value in cached["translations"]]
            if len(translations) != len(cues) or any(not value.strip() for value in translations):
                translations = []
        else:
            translations = []

        if not translations:
            tokenized = [
                self.processor.encode(text.replace("\n", " "), out_type=str)
                for text in original_texts
            ]
            try:
                results = self._translate_tokens(tokenized)
            except Exception as exc:
                raise LocalizerError(f"Local offline translation failed: {exc}") from exc
            active_glossary_sources = [
                source
                for source in context.glossary
                if any(
                    re.search(re.escape(source), text, flags=re.IGNORECASE)
                    for text in original_texts
                )
            ]
            default_translations = self._glossary_default_translations(active_glossary_sources)
            translations = [
                enforce_glossary(
                    source_text,
                    self.processor.decode_pieces(result.hypotheses[0])
                    .replace(chr(0x2581), " ")
                    .strip(),
                    context.glossary,
                    target_code=self.target_code,
                    default_translations=default_translations,
                )
                for source_text, result in zip(original_texts, results, strict=True)
            ]
            if len(translations) != len(cues) or any(not value for value in translations):
                raise LocalizerError("Local offline translation returned missing subtitle text.")
            self.cache.put(key, {"translations": translations})

        return [
            SubtitleCue(
                id=cue.id,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                text=translation,
                confidence=cue.confidence,
            )
            for cue, translation in zip(cues, translations, strict=True)
        ]


def translate_cues_contextually(
    provider: LocalOfflineProvider,
    cues: list[SubtitleCue],
    context: TranslationContext,
    *,
    source_code: str,
    target_code: str,
    batch_size: int,
) -> list[SubtitleCue]:
    groups = group_paragraph_cues(cues, source_code=source_code)
    translated_by_id: dict[int, SubtitleCue] = {}
    separator = "" if source_code == "zh" else " "
    grouped_context = TranslationContext(
        title=context.title,
        channel=context.channel,
        description=context.description,
        source_url=context.source_url,
        glossary={},
    )
    active_glossary_sources = [
        source
        for source in context.glossary
        if any(re.search(re.escape(source), cue.text, flags=re.IGNORECASE) for cue in cues)
    ]
    glossary_defaults = provider._glossary_default_translations(active_glossary_sources)

    for offset in range(0, len(groups), batch_size):
        group_batch = groups[offset : offset + batch_size]
        merged = [
            SubtitleCue(
                id=group[0].id,
                start_ms=group[0].start_ms,
                end_ms=group[-1].end_ms,
                text=separator.join(cue.text.strip() for cue in group),
            )
            for group in group_batch
        ]
        LOGGER.info(
            "Offline translating paragraph batch %s/%s…",
            offset // batch_size + 1,
            (len(groups) + batch_size - 1) // batch_size,
        )
        translated_groups = provider.translate_batch(merged, grouped_context)
        for group, translated_group in zip(group_batch, translated_groups, strict=True):
            parts = split_group_translation(
                translated_group.text,
                group,
                source_code=source_code,
                target_code=target_code,
            )
            if parts is None:
                fallback = provider.translate_batch(group, context)
                for cue in fallback:
                    translated_by_id[cue.id] = cue
                continue
            for cue, text in zip(group, parts, strict=True):
                translated_by_id[cue.id] = cue.model_copy(
                    update={
                        "text": enforce_glossary(
                            cue.text,
                            text,
                            context.glossary,
                            target_code=target_code,
                            default_translations=glossary_defaults,
                        )
                    }
                )

    missing = [cue.id for cue in cues if cue.id not in translated_by_id]
    if missing:
        raise LocalizerError(f"Contextual offline translation omitted cue IDs: {missing}")
    return [translated_by_id[cue.id] for cue in cues]
