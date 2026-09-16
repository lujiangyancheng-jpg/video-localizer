from __future__ import annotations

import json

from youtube_localizer.config import RightsConfig
from youtube_localizer.models import SourceMetadata
from youtube_localizer.publishing.rights import generate_rights_assets, validate_rights


def _metadata() -> SourceMetadata:
    return SourceMetadata(
        source_type="youtube",
        source_input="https://www.youtube.com/watch?v=rights-test",
        source_url="https://www.youtube.com/watch?v=rights-test",
        video_id="rights-test",
        title="Authorized sample",
        channel="Example Creator",
        duration=30,
    )


def test_owned_source_generates_auditable_rights_assets(tmp_path) -> None:
    config = RightsConfig(basis="owned", rights_holder="Example Creator")

    outputs = generate_rights_assets(_metadata(), tmp_path, config)

    assert {path.name for path in outputs} == {
        "RIGHTS_RECORD.json",
        "RIGHTS_RECORD.md",
        "ATTRIBUTION.txt",
    }
    record = json.loads((tmp_path / "RIGHTS_RECORD.json").read_text(encoding="utf-8"))
    assert record["record_type"] == "user-supplied rights declaration"
    assert record["requires_human_review"] is False
    assert record["rights"]["basis"] == "owned"


def test_creative_commons_requires_a_public_license_link() -> None:
    assert any("许可页面链接" in issue for issue in validate_rights(RightsConfig(basis="cc_by")))


def test_noncommercial_license_rejects_commercial_declaration() -> None:
    config = RightsConfig(
        basis="cc_by_nc",
        license_url="https://creativecommons.org/licenses/by-nc/4.0/",
        commercial_use=True,
    )

    assert any("不允许" in issue for issue in validate_rights(config))


def test_written_permission_requires_holder_and_reference() -> None:
    issues = validate_rights(RightsConfig(basis="written_permission"))

    assert any("权利人" in issue for issue in issues)
    assert any("授权证明" in issue for issue in issues)
