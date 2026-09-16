from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass

from ..models import SubtitleCue


@dataclass(frozen=True)
class SubtitleQualityFinding:
    cue_id: int
    category: str
    message: str


def _visible_text(text: str) -> str:
    return re.sub(r"\[[^\]]+\]", "", text).strip()


def _word_count(text: str) -> int:
    return len(re.findall(r"[^\W_]+(?:['’-][^\W_]+)?", text, flags=re.UNICODE))


def _cjk_count(text: str) -> int:
    return len(re.findall(r"[\u3400-\u9fff]", text))


def _script_character_count(text: str, language: str) -> int:
    if language == "ja":
        return len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff]", text))
    if language == "ko":
        return len(re.findall(r"[\uac00-\ud7af]", text))
    return _cjk_count(text)


def _numbers(text: str) -> list[str]:
    normalized = (
        unicodedata.normalize("NFKC", text).replace("٬", ",").replace("٫", ".").replace("٪", "%")
    )
    values = re.findall(r"\d+(?:[.,]\d+)*%?", normalized)
    return [
        "".join(str(unicodedata.digit(char)) if char.isdigit() else char for char in value).replace(
            ",", ""
        )
        for value in values
    ]


def audit_subtitles(
    cues: list[SubtitleCue],
    *,
    language: str,
    max_lines: int,
    preferred_line_length: int,
    source_cues: list[SubtitleCue] | None = None,
    glossary: dict[str, str] | None = None,
) -> dict[str, object]:
    """Return deterministic, review-oriented subtitle checks for the final target track.

    These are warnings rather than automatic rewrites. A sentence can be intentionally fast,
    so the report helps a person inspect only the risky cues instead of silently altering timing.
    """
    findings: list[SubtitleQualityFinding] = []
    previous_visible = ""
    language = language.lower().split("-", maxsplit=1)[0]
    source_by_id = {cue.id: cue for cue in source_cues or []}
    glossary = glossary or {}
    for cue in cues:
        visible = _visible_text(cue.text)
        if not visible:
            continue
        duration_seconds = max(0.001, (cue.end_ms - cue.start_ms) / 1000)
        lines = [line for line in cue.text.splitlines() if line.strip()]
        if duration_seconds < 0.7:
            findings.append(
                SubtitleQualityFinding(cue.id, "flash", "Very short subtitle flash (under 0.7s).")
            )
        if len(lines) > max_lines:
            findings.append(
                SubtitleQualityFinding(
                    cue.id,
                    "line_count",
                    f"Uses {len(lines)} lines; the selected style prefers at most {max_lines}.",
                )
            )
        line_multiplier = (
            1.5
            if language == "zh"
            else 1.7
            if language == "ja"
            else 2.0
            if language == "ko"
            else 3.0
        )
        if any(len(line) > preferred_line_length * line_multiplier for line in lines):
            findings.append(
                SubtitleQualityFinding(
                    cue.id,
                    "line_length",
                    "A subtitle line still exceeds the preferred display width.",
                )
            )
        if language in {"zh", "ja", "ko"}:
            reading_speed = _script_character_count(visible, language) / duration_seconds
            preferred_speed = {"zh": 10.0, "ja": 12.0, "ko": 11.0}[language]
            if reading_speed > preferred_speed:
                findings.append(
                    SubtitleQualityFinding(
                        cue.id,
                        "reading_speed",
                        f"{language.upper()} reading speed is {reading_speed:.1f} characters/second "
                        f"(preferred ≤{preferred_speed:g}).",
                    )
                )
        else:
            reading_speed = _word_count(visible) / duration_seconds
            preferred_speed = 3.5 if language == "ar" else 4.5
            if reading_speed > preferred_speed:
                findings.append(
                    SubtitleQualityFinding(
                        cue.id,
                        "reading_speed",
                        f"{language.upper()} reading speed is {reading_speed:.1f} words/second "
                        f"(preferred ≤{preferred_speed:g}).",
                    )
                )
        normalized = re.sub(r"\s+", " ", visible).casefold()
        if normalized and normalized == previous_visible:
            findings.append(
                SubtitleQualityFinding(
                    cue.id,
                    "duplicate",
                    "Same visible text as the previous subtitle cue.",
                )
            )
        previous_visible = normalized

        source = source_by_id.get(cue.id)
        if source is not None:
            source_numbers = _numbers(source.text)
            target_numbers = _numbers(visible)
            if source_numbers != target_numbers:
                findings.append(
                    SubtitleQualityFinding(
                        cue.id,
                        "number_consistency",
                        f"Numbers differ from source: {source_numbers} -> {target_numbers}.",
                    )
                )
            for source_term, target_term in glossary.items():
                if (
                    source_term.casefold() in source.text.casefold()
                    and target_term.casefold() not in visible.casefold()
                ):
                    findings.append(
                        SubtitleQualityFinding(
                            cue.id,
                            "term_consistency",
                            f"Expected glossary translation {source_term!r} -> {target_term!r}.",
                        )
                    )

    category_counts = Counter(finding.category for finding in findings)
    flagged_cue_ids = sorted({finding.cue_id for finding in findings})
    return {
        "target_language": language,
        "total_cues": len(cues),
        "flagged_cue_ids": flagged_cue_ids,
        "flagged_cue_count": len(flagged_cue_ids),
        "finding_count": len(findings),
        "findings_by_category": dict(sorted(category_counts.items())),
        "findings": [asdict(finding) for finding in findings],
    }


def select_review_cues(cues: list[SubtitleCue], report: dict[str, object]) -> list[SubtitleCue]:
    """Keep only timed cues flagged by the deterministic final-subtitle audit."""
    raw_ids = report.get("flagged_cue_ids", [])
    flagged_ids = (
        {value for value in raw_ids if isinstance(value, int)}
        if isinstance(raw_ids, list)
        else set()
    )
    return [cue for cue in cues if cue.id in flagged_ids]
