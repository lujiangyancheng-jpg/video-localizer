from pathlib import Path

import pytest

from youtube_localizer.installation import verify_optional_components


def test_absent_components_are_optional_but_partial_installs_fail(tmp_path: Path):
    assert verify_optional_components(tmp_path) == []
    models = tmp_path / "models"
    models.mkdir()
    (models / "model-pack-local-ai.json").write_text("{}")
    with pytest.raises(ValueError, match="missing or empty"):
        verify_optional_components(tmp_path)


def test_whisper_model_file_alone_does_not_pass_verification(tmp_path, monkeypatch):
    directory = tmp_path / "models" / "faster-whisper-medium"
    directory.mkdir(parents=True)
    (directory / "model.bin").write_bytes(b"model")
    monkeypatch.setattr(
        "youtube_localizer.installation.hash_file",
        lambda _: "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae",
    )
    with pytest.raises(ValueError, match="config.json"):
        verify_optional_components(tmp_path)
