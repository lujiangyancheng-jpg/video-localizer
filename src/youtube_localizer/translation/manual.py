from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..errors import TranslationImportError
from ..models import ProjectPaths, SubtitleCue
from ..subtitles.bilingual import combine_bilingual
from ..subtitles.parser import parse_subtitle, write_srt
from ..subtitles.readability import readability_pass
from ..subtitles.styling import write_bilingual_ass
from ..utils.files import atomic_write_json, atomic_write_text, load_json
from ..utils.hashing import stable_hash
from ..utils.text import ms_to_srt, timestamp_to_ms
from .base import TranslationContext, TranslationProvider
from .prompts import context_prompt, translation_rules

FORMAT_MARKER = "YCLP_TRANSLATION_CHUNK_V1"


def cue_payload(
    cue: SubtitleCue,
    *,
    source_code: str = "en",
    target_code: str = "zh",
) -> dict[str, Any]:
    return {
        "id": cue.id,
        "start": ms_to_srt(cue.start_ms),
        "end": ms_to_srt(cue.end_ms),
        source_code: cue.text,
        target_code: "",
    }


def serialize_translation_batch(
    cues: list[SubtitleCue],
    *,
    source_code: str = "en",
    target_code: str = "zh",
) -> str:
    return "\n".join(
        json.dumps(
            cue_payload(cue, source_code=source_code, target_code=target_code),
            ensure_ascii=False,
        )
        for cue in cues
    )


class ManualExportProvider(TranslationProvider):
    def __init__(self, *, source_code: str = "en", target_code: str = "zh") -> None:
        self.source_code = source_code
        self.target_code = target_code

    def translate_batch(
        self, cues: list[SubtitleCue], context: TranslationContext
    ) -> list[SubtitleCue]:
        raise TranslationImportError(
            "Manual translation does not make API calls. Export chunks, translate them in "
            "ChatGPT, then import each returned file."
        )

    def export(
        self,
        cues: list[SubtitleCue],
        context: TranslationContext,
        output_dir: Path,
        *,
        batch_size: int = 40,
    ) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        for old in output_dir.glob("chunk_*.md"):
            old.unlink()
        exported: list[Path] = []
        manifest_chunks: list[dict[str, Any]] = []
        for number, offset in enumerate(range(0, len(cues), batch_size), start=1):
            batch = cues[offset : offset + batch_size]
            name = f"chunk_{number:03d}.md"
            path = output_dir / name
            content = (
                f"<!-- {FORMAT_MARKER} -->\n"
                f"# Translation chunk {number:03d}\n\n"
                "## Instructions\n\n"
                f"{translation_rules(self.source_code, self.target_code)}\n\n"
                f"Keep the `{self.source_code}` text for validation and fill every "
                f"`{self.target_code}` value. "
                "Return JSONL only; do not wrap it in prose.\n\n"
                "## Video context\n\n"
                f"{context_prompt(context)}\n\n"
                "## JSONL payload\n\n"
                "```jsonl\n"
                f"{serialize_translation_batch(batch, source_code=self.source_code, target_code=self.target_code)}\n"
                "```\n"
            )
            atomic_write_text(path, content)
            exported.append(path)
            manifest_chunks.append(
                {
                    "file": name,
                    "cue_ids": [cue.id for cue in batch],
                    "payload_hash": stable_hash(
                        [
                            cue_payload(
                                cue,
                                source_code=self.source_code,
                                target_code=self.target_code,
                            )
                            for cue in batch
                        ]
                    ),
                }
            )
        atomic_write_json(
            output_dir / "manifest.json",
            {
                "version": 1,
                "format": FORMAT_MARKER,
                "cue_count": len(cues),
                "source_code": self.source_code,
                "target_code": self.target_code,
                "source_hash": stable_hash([cue.model_dump() for cue in cues]),
                "chunks": manifest_chunks,
            },
        )
        return exported


def _extract_json_records(content: str) -> list[dict[str, Any]]:
    stripped = content.strip()
    fenced = re.findall(r"```(?:jsonl|json)?\s*(.*?)```", stripped, re.DOTALL | re.IGNORECASE)
    candidates = fenced or [stripped]
    records: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                records.extend(item for item in parsed if isinstance(item, dict))
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
                continue
        except json.JSONDecodeError:
            pass
        for line in candidate.splitlines():
            line = line.strip().rstrip(",")
            if not line.startswith("{"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TranslationImportError(f"Invalid JSONL line: {line[:120]}") from exc
            if isinstance(item, dict):
                records.append(item)
    if not records:
        raise TranslationImportError(
            "No JSON translation records found. Return one JSON object per cue with "
            "id, start, end, en, and zh."
        )
    return records


def parse_imported_translations(
    content: str,
    source_cues: list[SubtitleCue],
    *,
    source_code: str = "en",
    target_code: str = "zh",
) -> dict[int, SubtitleCue]:
    source_by_id = {cue.id: cue for cue in source_cues}
    output: dict[int, SubtitleCue] = {}
    for item in _extract_json_records(content):
        missing = {"id", "start", "end", source_code, target_code} - set(item)
        if missing:
            raise TranslationImportError(
                f"Translation record is missing fields: {', '.join(sorted(missing))}"
            )
        try:
            cue_id = int(item["id"])
        except (TypeError, ValueError) as exc:
            raise TranslationImportError(f"Invalid cue ID: {item['id']!r}") from exc
        if cue_id in output:
            raise TranslationImportError(f"Duplicate cue ID in import: {cue_id}")
        source = source_by_id.get(cue_id)
        if not source:
            raise TranslationImportError(f"Unknown cue ID in import: {cue_id}")
        try:
            start_ms = timestamp_to_ms(str(item["start"]))
            end_ms = timestamp_to_ms(str(item["end"]))
        except ValueError as exc:
            raise TranslationImportError(str(exc)) from exc
        if start_ms != source.start_ms or end_ms != source.end_ms:
            raise TranslationImportError(
                f"Cue {cue_id} timestamps changed. Expected "
                f"{ms_to_srt(source.start_ms)} --> {ms_to_srt(source.end_ms)}."
            )
        if str(item[source_code]).strip() != source.text.strip():
            raise TranslationImportError(f"Cue {cue_id} {source_code} source text changed.")
        translated = str(item[target_code]).strip()
        if not translated:
            raise TranslationImportError(f"Cue {cue_id} has an empty {target_code} translation.")
        source_urls = re.findall(r'https?://[^\s<>"]+', source.text)
        missing_urls = [url for url in source_urls if url not in translated]
        if missing_urls:
            raise TranslationImportError(
                f"Cue {cue_id} changed or omitted URL(s): {', '.join(missing_urls)}"
            )
        output[cue_id] = source.model_copy(update={"text": translated})
    return output


def import_translation_file(
    project: ProjectPaths,
    translated_file: Path,
    *,
    width: int = 20,
    max_lines: int = 2,
    subtitle_mode: str = "chinese",
    subtitle_config=None,
    source_code: str = "en",
    target_code: str = "zh",
) -> tuple[int, int, list[str]]:
    if not translated_file.is_file():
        raise TranslationImportError(f"Translated file does not exist: {translated_file}")
    source_path = project.english_srt if source_code == "en" else project.chinese_srt
    target_path = project.english_srt if target_code == "en" else project.chinese_srt
    source_cues = parse_subtitle(source_path)
    imported = parse_imported_translations(
        translated_file.read_text(encoding="utf-8-sig"),
        source_cues,
        source_code=source_code,
        target_code=target_code,
    )
    store_path = project.temp / "manual_translations.json"
    source_hash = stable_hash([cue.model_dump(mode="json") for cue in source_cues])
    payload = load_json(store_path) if store_path.is_file() else {}
    if (
        not isinstance(payload, dict)
        or payload.get("source_hash") != source_hash
        or payload.get("source_code") != source_code
        or payload.get("target_code") != target_code
        or not isinstance(payload.get("translations"), dict)
    ):
        payload = {
            "source_hash": source_hash,
            "source_code": source_code,
            "target_code": target_code,
            "translations": {},
        }
    stored = payload["translations"]
    for cue_id, cue in imported.items():
        stored[str(cue_id)] = cue.model_dump(mode="json")
    atomic_write_json(store_path, payload)

    warnings: list[str] = []
    if len(stored) == len(source_cues):
        ordered: list[SubtitleCue] = []
        for source in source_cues:
            raw = stored.get(str(source.id))
            if not raw:
                raise TranslationImportError(
                    f"Internal translation store is missing cue {source.id}."
                )
            translated = SubtitleCue.model_validate(raw)
            if (
                translated.start_ms != source.start_ms
                or translated.end_ms != source.end_ms
                or translated.id != source.id
            ):
                raise TranslationImportError(f"Stored translation for cue {source.id} is invalid.")
            ordered.append(translated)
        if target_code == "zh":
            target_cues, issues = readability_pass(ordered, width=width, max_lines=max_lines)
            warnings.extend(f"Cue {issue.cue_id}: {issue.message}" for issue in issues)
        else:
            target_cues = ordered
        write_srt(target_path, target_cues)
        if subtitle_mode != "chinese":
            english = source_cues if source_code == "en" else target_cues
            chinese = source_cues if source_code == "zh" else target_cues
            bilingual = combine_bilingual(english, chinese, mode=subtitle_mode)
            write_srt(project.bilingual_srt, bilingual)
            if subtitle_config is not None:
                write_bilingual_ass(
                    project.bilingual_ass,
                    english,
                    chinese,
                    subtitle_config,
                    mode=subtitle_mode,
                )
    return len(stored), len(source_cues), warnings
