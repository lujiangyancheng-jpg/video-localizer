from __future__ import annotations

from youtube_localizer import __version__
from youtube_localizer.onboarding import (
    REPOSITORY_NAME,
    REPOSITORY_URL,
    model_compatibility_version,
    model_release_page_url,
    onboarding_completed,
    onboarding_state_path,
    quick_readiness_message,
    release_asset_url,
    release_page_url,
    setup_readiness_items,
    setup_status_message,
    user_guide_url,
    whisper_model_asset_names,
)


def test_release_links_use_the_current_version() -> None:
    assert REPOSITORY_NAME == "video-localizer"
    assert REPOSITORY_URL.endswith("/video-localizer")
    assert release_page_url().endswith(f"/releases/tag/v{__version__}")
    assert release_asset_url("setup.exe").endswith(f"/v{__version__}/setup.exe")
    assert model_release_page_url().endswith("/releases/tag/v0.7.0")
    assert user_guide_url().endswith("/docs/USER_GUIDE.zh-CN.md")


def test_whisper_model_assets_are_paired_and_versioned() -> None:
    setup, data = whisper_model_asset_names("small")

    assert setup == "YouTube-Chinese-Localizer-0.7.0-Whisper-Small-Model-Setup.exe"
    assert data == setup.removesuffix(".exe") + "-1.bin"
    assert model_compatibility_version("1.2.3.45") == "1.2.3"


def test_onboarding_completion_is_persistent_across_app_iterations(tmp_path) -> None:
    from youtube_localizer.onboarding import mark_onboarding_completed

    state_path = tmp_path / "onboarding.json"
    assert not onboarding_completed(state_path=state_path)

    mark_onboarding_completed(state_path=state_path)

    assert onboarding_completed(state_path=state_path)
    state_path.write_text('{"completed_version": "0.6.8"}', encoding="utf-8")
    assert onboarding_completed(state_path=state_path)


def test_onboarding_state_prefers_local_app_data() -> None:
    path = onboarding_state_path({"LOCALAPPDATA": r"C:\\Users\\demo\\AppData\\Local"})

    assert str(path).endswith(r"YouTube Chinese Localizer\onboarding.json")


def test_setup_status_explains_missing_or_ready_models() -> None:
    assert "还需安装 Whisper" in setup_status_message("standard", ())
    assert "Small / Medium 已就绪" in setup_status_message("complete", ("small", "medium"))
    assert "源码运行环境" in setup_status_message(None, ())


def test_help_center_readiness_is_actionable_without_blocking_optional_ai() -> None:
    items = setup_readiness_items(
        "standard",
        (),
        local_ai_ready=False,
        resource_mode="serialized",
        output_advice="本地磁盘可用空间 80.0 GiB。",
    )

    assert next(item for item in items if item.title == "语音识别模型").status == "action"
    assert next(item for item in items if item.title == "翻译方式").status == "optional"
    assert "避免电脑卡死" in next(item for item in items if item.title == "性能调度").detail
    assert quick_readiness_message((), local_ai_ready=False, resource_mode="serialized") == (
        "本机状态：未安装 Whisper · 本地快速翻译 · 安全调度"
    )
