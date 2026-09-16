from __future__ import annotations

import re
from pathlib import Path

from youtube_localizer import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_release_version_is_consistent_across_package_metadata() -> None:
    expected = __version__
    files = {
        PROJECT_ROOT / "pyproject.toml": rf'^version = "{re.escape(expected)}"$',
        PROJECT_ROOT
        / "installer"
        / "build_offline_installer.ps1": rf'\$Version = "{re.escape(expected)}"',
        PROJECT_ROOT
        / "installer"
        / "offline-installer.iss": rf'#define AppVersion "{re.escape(expected)}"',
    }

    for path, pattern in files.items():
        assert re.search(pattern, path.read_text(encoding="utf-8"), flags=re.MULTILINE), path


def test_four_part_iterations_reuse_the_matching_three_part_model_packs() -> None:
    expected_model_version = ".".join(__version__.split(".")[:3])
    builder = (PROJECT_ROOT / "installer" / "build_offline_installer.ps1").read_text(
        encoding="utf-8"
    )
    installer = (PROJECT_ROOT / "installer" / "offline-installer.iss").read_text(encoding="utf-8")

    assert len(__version__.split(".")) == 4
    assert f'$ModelPackVersion = "{expected_model_version}"' in builder
    assert f'#define ModelPackVersion "{expected_model_version}"' in installer
    model_files = {
        PROJECT_ROOT / "installer" / "build_whisper_model_pack.ps1": "$Version",
        PROJECT_ROOT / "installer" / "whisper-model-pack.iss": "AppVersion",
        PROJECT_ROOT / "installer" / "build_local_ai_model_pack.ps1": "$Version",
        PROJECT_ROOT / "installer" / "local-ai-model-pack.iss": "AppVersion",
    }
    for path, marker in model_files.items():
        text = path.read_text(encoding="utf-8")
        assert marker in text
        assert f'"{expected_model_version}"' in text


def test_current_release_is_presented_as_the_stable_standard_download() -> None:
    expected = __version__
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    checklist = (PROJECT_ROOT / "docs" / "RELEASE_CHECKLIST.md").read_text(encoding="utf-8")

    assert f"v{expected} Standard 正式版" in readme
    assert f"YouTube-Chinese-Localizer-{expected}-Standard-Offline-Setup.exe" in readme
    assert "默认使用稳定更新通道" in readme
    assert "作为非 Pre-release 发布" in checklist
