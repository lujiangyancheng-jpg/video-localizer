from __future__ import annotations

from youtube_localizer.models import SubtitleCue
from youtube_localizer.subtitles.quality import audit_subtitles, select_review_cues


def test_quality_audit_reports_fast_duplicate_and_long_lines() -> None:
    cues = [
        SubtitleCue(id=1, start_ms=0, end_ms=500, text="one two three four five six seven"),
        SubtitleCue(id=2, start_ms=500, end_ms=1500, text="one two three four five six seven"),
    ]

    report = audit_subtitles(
        cues,
        language="en",
        max_lines=2,
        preferred_line_length=10,
    )

    assert report["flagged_cue_ids"] == [1, 2]
    assert report["findings_by_category"]["reading_speed"] == 2
    assert report["findings_by_category"]["duplicate"] == 1
    assert report["findings_by_category"]["flash"] == 1


def test_quality_audit_uses_chinese_reading_speed() -> None:
    cue = SubtitleCue(id=1, start_ms=0, end_ms=1000, text="你" * 12)

    report = audit_subtitles(
        [cue],
        language="zh",
        max_lines=2,
        preferred_line_length=20,
    )

    assert report["flagged_cue_ids"] == [1]
    assert report["findings_by_category"]["reading_speed"] == 1


def test_quality_review_selection_keeps_only_flagged_timed_cues() -> None:
    cues = [
        SubtitleCue(id=1, start_ms=0, end_ms=1000, text="需要检查。"),
        SubtitleCue(id=2, start_ms=1000, end_ms=2000, text="无需检查。"),
    ]

    review = select_review_cues(cues, {"flagged_cue_ids": [1, "invalid"]})

    assert review == [cues[0]]


def test_quality_audit_supports_japanese_korean_and_arabic_reading_speed() -> None:
    japanese = audit_subtitles(
        [SubtitleCue(id=1, start_ms=0, end_ms=1000, text="日" * 13)],
        language="ja-JP",
        max_lines=2,
        preferred_line_length=20,
    )
    korean = audit_subtitles(
        [SubtitleCue(id=1, start_ms=0, end_ms=1000, text="한" * 12)],
        language="ko",
        max_lines=2,
        preferred_line_length=20,
    )
    arabic = audit_subtitles(
        [SubtitleCue(id=1, start_ms=0, end_ms=1000, text="واحد اثنان ثلاثة أربعة")],
        language="ar",
        max_lines=2,
        preferred_line_length=20,
    )

    assert japanese["findings_by_category"]["reading_speed"] == 1
    assert korean["findings_by_category"]["reading_speed"] == 1
    assert arabic["findings_by_category"]["reading_speed"] == 1


def test_quality_audit_flags_changed_numbers_and_glossary_terms() -> None:
    source = [SubtitleCue(id=7, start_ms=0, end_ms=2000, text="Project Nova costs 1,200 dollars")]
    target = [SubtitleCue(id=7, start_ms=0, end_ms=2000, text="该项目花费 200 美元")]

    report = audit_subtitles(
        target,
        language="zh",
        max_lines=2,
        preferred_line_length=20,
        source_cues=source,
        glossary={"Project Nova": "新星计划"},
    )

    assert report["findings_by_category"]["number_consistency"] == 1
    assert report["findings_by_category"]["term_consistency"] == 1


def test_quality_audit_treats_arabic_indic_digits_as_the_same_number() -> None:
    report = audit_subtitles(
        [SubtitleCue(id=1, start_ms=0, end_ms=2000, text="السعر ١٢٣ دولار")],
        language="ar",
        max_lines=2,
        preferred_line_length=20,
        source_cues=[SubtitleCue(id=1, start_ms=0, end_ms=2000, text="The price is 123 dollars")],
    )

    assert "number_consistency" not in report["findings_by_category"]
