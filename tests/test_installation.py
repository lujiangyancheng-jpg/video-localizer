from pathlib import Path

import pytest

from youtube_localizer.installation import component_inventory, verify_optional_components


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


def test_component_inventory_reports_presence_and_disk_size(tmp_path) -> None:
    small = tmp_path / "models" / "faster-whisper-small"
    small.mkdir(parents=True)
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.json"):
        (small / name).write_bytes(b"model")
    inventory = {component.key: component for component in component_inventory(tmp_path)}

    assert inventory["whisper-small"].installed
    assert not inventory["whisper-small"].repair_needed
    assert inventory["whisper-small"].size_bytes == 20
    assert not inventory["whisper-medium"].installed
    assert not inventory["local-ai"].installed
    assert not inventory["super-resolution"].installed


def test_component_inventory_distinguishes_partial_install_from_absent(tmp_path) -> None:
    partial = tmp_path / "runtime" / "super-resolution"
    partial.mkdir(parents=True)
    (partial / "waifu2x-ncnn-vulkan.exe").write_bytes(b"runtime")

    inventory = {component.key: component for component in component_inventory(tmp_path)}
    component = inventory["super-resolution"]
    assert not component.installed
    assert component.repair_needed
    assert "修复" in component.detail
