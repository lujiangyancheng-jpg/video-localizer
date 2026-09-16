from __future__ import annotations

import os
from pathlib import Path
from queue import Queue

import pytest

from youtube_localizer.gui import (
    DEFAULT_SUBTITLE_FONT,
    SUBTITLE_FONT_SIZES,
    SUBTITLE_FONTS,
    LocalizerWindow,
    api_configuration,
    browser_capture_required,
    browser_capture_required_source,
    build_process_command,
    clamp_subtitle_preview_values,
    continued_start_sources_after_capture,
    friendly_failure_summary,
    gui_parallel_job_limit,
    gui_process_creationflags,
    label_for_value,
    local_ai_available,
    mode_description,
    notify_window_attention,
    output_directory_status_hint,
    progress_update_from_output,
    project_workspace_from_output,
    queue_input_values,
    retain_pending_start_sources,
    retry_queue_commands,
    task_tree_visible_rows,
    validate_browser_capture_input,
    validate_direct_media_links,
    whisper_model_installation_message,
)
from youtube_localizer.hardware import SystemResources


def test_build_process_command_uses_argument_array_and_resume() -> None:
    command = build_process_command(
        " https://www.youtube.com/watch?v=abc123 ",
        subtitle_mode="chinese",
        translation_provider="manual",
        python_executable="python.exe",
        main_script=Path("main.py"),
    )

    assert command == [
        "python.exe",
        "main.py",
        "process",
        "https://www.youtube.com/watch?v=abc123",
        "--subtitle-mode",
        "chinese",
        "--translation-provider",
        "manual",
        "--translation-direction",
        "en-to-zh",
        "--subtitle-font",
        "Noto Sans CJK SC",
        "--super-resolution",
        "off",
        "--rights-basis",
        "unspecified",
        "--noncommercial-use",
        "--resume",
    ]


def test_build_process_command_passes_enhancement_and_rights_record() -> None:
    command = build_process_command(
        "video.mp4",
        subtitle_mode="download_only",
        translation_provider="offline",
        super_resolution="animation",
        rights_basis="cc_by",
        rights_holder="Example Creator",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        permission_reference="Source description",
        attribution_text="Example attribution",
        commercial_use=True,
    )

    assert command[command.index("--super-resolution") + 1] == "animation"
    assert command[command.index("--rights-basis") + 1] == "cc_by"
    assert command[command.index("--rights-holder") + 1] == "Example Creator"
    assert command[command.index("--license-url") + 1].startswith("https://")
    assert command[command.index("--permission-reference") + 1] == "Source description"
    assert command[command.index("--attribution-text") + 1] == "Example attribution"
    assert "--commercial-use" in command


def test_build_process_command_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="请粘贴"):
        build_process_command(
            " ",
            subtitle_mode="chinese",
            translation_provider="manual",
        )
    with pytest.raises(ValueError, match="字幕模式"):
        build_process_command(
            "video.mp4",
            subtitle_mode="unknown",
            translation_provider="manual",
        )


def test_queue_input_values_keeps_local_paths_and_deduplicates_lines() -> None:
    assert queue_input_values(
        "  https://youtu.be/one  \nC:/Videos/My clip.mp4\nhttps://youtu.be/one\n"
    ) == [
        "https://youtu.be/one",
        "C:/Videos/My clip.mp4",
    ]


def test_task_tree_rows_keep_small_queues_compact() -> None:
    assert task_tree_visible_rows(0) == 2
    assert task_tree_visible_rows(1) == 2
    assert task_tree_visible_rows(3) == 3
    assert task_tree_visible_rows(20) == 5


def test_direct_media_dialog_accepts_complete_cdn_links_and_rejects_pages() -> None:
    link = "https://groupvideo.photo.qq.com/1071_0bc/opaque-resource/"
    assert validate_direct_media_links(link) == [link]

    with pytest.raises(ValueError, match="播放页"):
        validate_direct_media_links("https://www.example.test/play/episode.html")


def test_direct_media_dialog_rejects_truncated_display_text() -> None:
    with pytest.raises(ValueError, match="截断"):
        validate_direct_media_links("https://groupvideo.photo.qq.com/1071_0bc…")


def test_browser_capture_accepts_one_dynamic_page_and_rejects_a_queue() -> None:
    page = "https://www.lmm85.com/play/8164_1_1.html"
    assert validate_browser_capture_input(page) == page

    with pytest.raises(ValueError, match="每次需要一个"):
        validate_browser_capture_input(f"{page}\nhttps://www.example.test/play/other.html")


def test_browser_capture_redirects_youtube_to_the_normal_link_flow() -> None:
    with pytest.raises(ValueError, match="YouTube"):
        validate_browser_capture_input("https://www.youtube.com/watch?v=abc123")


@pytest.mark.parametrize(
    "message",
    [
        "该站点要求 Cloudflare 浏览器验证",
        "播放器使用第三方 iframe",
        "页面通过混淆脚本动态加载媒体",
    ],
)
def test_browser_capture_required_recognizes_dynamic_page_failures(message: str) -> None:
    assert browser_capture_required(message)


def test_browser_capture_required_source_blocks_only_the_affected_playback_page() -> None:
    sources = [
        "https://cdn.example.test/video.mp4",
        "https://www.lmm85.com/play/8164_1_1.html",
        "https://www.youtube.com/watch?v=abc123",
    ]
    errors = {
        1: "HTTP 429",
        2: "该站点要求 Cloudflare 浏览器验证",
        3: "Cloudflare",
    }

    assert browser_capture_required_source(sources, errors) == sources[1]
    assert browser_capture_required_source(sources, {1: "HTTP 429"}) is None


def test_capture_retargets_only_the_start_request_that_expected_that_page() -> None:
    page = "https://www.lmm85.com/play/8164_1_1.html"
    media = "https://cdn.example.test/video.mp4"

    assert continued_start_sources_after_capture(
        (page,), page_url=page, updated_sources=[media]
    ) == (media,)
    assert (
        continued_start_sources_after_capture(
            ("https://example.test/other",),
            page_url=page,
            updated_sources=[media],
        )
        is None
    )


def test_pending_start_is_cancelled_when_the_visible_queue_changes() -> None:
    original = ("https://example.test/original",)

    assert retain_pending_start_sources(original, list(original)) == original
    assert retain_pending_start_sources(original, ["https://example.test/replacement"]) is None
    assert retain_pending_start_sources(None, list(original)) is None


def test_start_routes_dynamic_page_through_browser_capture_before_pipeline() -> None:
    page = "https://www.lmm85.com/play/8164_1_1.html"

    class FakeVariable:
        def get(self) -> str:
            return page

    window = object.__new__(LocalizerWindow)
    window._has_active_processes = lambda: False
    window.worker = None
    window._analysis_worker = None
    window._validate = lambda: ([["python", "main.py", "process", page]], {}, "download_only")
    window.input_value = FakeVariable()
    window._media_preview_errors = {1: "该站点要求 Cloudflare 浏览器验证"}
    window._browser_capture_prompted_sources = set()
    window._pending_start_sources = None
    statuses: list[tuple[str, str]] = []
    opened: list[tuple[str, bool]] = []
    started: list[object] = []
    window._set_status = lambda message, state: statuses.append((message, state))
    window._open_browser_capture_dialog = lambda source, auto_start=False: opened.append(
        (source, auto_start)
    )
    window._begin_queue = lambda *args, **kwargs: started.append((args, kwargs))

    window._start()

    assert opened == [(page, True)]
    assert not started
    assert window._pending_start_sources == (page,)
    assert "自动继续" in statuses[-1][0]


def test_stored_internal_values_are_mapped_back_to_display_labels() -> None:
    assert label_for_value({"推荐": "best", "标准": "standard"}, "best", "回退") == "推荐"
    assert label_for_value({"推荐": "best"}, "unknown", "回退") == "回退"


def test_gui_parallel_queue_adapts_to_cpu_and_memory() -> None:
    low = SystemResources(4, 8 * 1024)
    typical = SystemResources(8, 16 * 1024)
    capable = SystemResources(16, 32 * 1024)
    high_end = SystemResources(24, 64 * 1024)

    assert gui_parallel_job_limit(0, low) == 1
    assert gui_parallel_job_limit(20, low) == 1
    assert gui_parallel_job_limit(20, typical) == 2
    assert gui_parallel_job_limit(20, capable) == 3
    assert gui_parallel_job_limit(20, high_end) == 4
    assert gui_parallel_job_limit(2, high_end) == 2


def test_retry_queue_commands_only_restarts_incomplete_items_with_resume() -> None:
    commands = [
        ["python", "main.py", "process", "one"],
        ["python", "main.py", "process", "two", "--resume"],
        ["python", "main.py", "process", "three"],
    ]

    assert retry_queue_commands(commands, (3, 2, 3, 99)) == [
        ["python", "main.py", "process", "three", "--resume"],
        ["python", "main.py", "process", "two", "--resume"],
    ]
    assert commands[0] == ["python", "main.py", "process", "one"]


def test_project_workspace_from_output_accepts_only_selected_output_root(tmp_path) -> None:
    output_root = tmp_path / "output"
    workspace = output_root / "example"
    workspace.mkdir(parents=True)

    assert (
        project_workspace_from_output(f"Project workspace: {workspace}", output_root)
        == workspace.resolve()
    )
    assert (
        project_workspace_from_output(f"INFO Project workspace: {workspace}", output_root)
        == workspace.resolve()
    )
    assert (
        project_workspace_from_output(f"Project workspace: {tmp_path / 'outside'}", output_root)
        is None
    )
    assert project_workspace_from_output("unrelated logging", output_root) is None


def test_output_directory_hint_reports_recoverable_projects(tmp_path) -> None:
    project = tmp_path / "unfinished"
    project.mkdir()
    (project / "pipeline_state.json").write_text(
        '{"project_status":"incomplete","steps":{"download":{"name":"download",'
        '"status":"failed","started_at":"now","input_hash":"i","config_hash":"c"}}}',
        encoding="utf-8",
    )

    hint = output_directory_status_hint(tmp_path)

    assert "1 个未完成项目" in hint
    assert "继续上次处理" in hint


def test_gui_queue_tracks_only_failed_items_for_retry() -> None:
    window = object.__new__(LocalizerWindow)
    window.stop_requested = False
    window.events = Queue()
    window._pause_requested_indices = set()

    def run_process(
        _command: list[str],
        _environment: dict[str, str],
        index: int,
        _position: int,
        _total: int,
    ) -> int:
        return 1 if index == 2 else 0

    window._run_process = run_process  # type: ignore[method-assign]
    window._run_queue(
        [
            ["python", "main.py", "process", "one"],
            ["python", "main.py", "process", "two"],
            ["python", "main.py", "process", "three"],
        ],
        {},
        "offline",
    )

    events = list(window.events.queue)
    done_payload = next(payload for event, payload in events if event == "done")
    assert done_payload[-1] == (2,)


def test_gui_queue_keeps_a_paused_item_recoverable_without_counting_it_as_failure() -> None:
    window = object.__new__(LocalizerWindow)
    window.stop_requested = False
    window.events = Queue()
    window._pause_requested_indices = {3}

    def run_process(
        _command: list[str],
        _environment: dict[str, str],
        _index: int,
        _position: int,
        _total: int,
    ) -> int:
        return 1

    window._run_process = run_process  # type: ignore[method-assign]
    window._run_queue(
        [["python", "main.py", "process", "three"]],
        {},
        "offline",
        task_indices=(3,),
    )

    events = list(window.events.queue)
    done_payload = next(payload for event, payload in events if event == "done")
    assert done_payload[0] == 0
    assert done_payload[4] == 0
    assert done_payload[5] == 1
    assert done_payload[-1] == (3,)


def test_api_configuration_does_not_require_or_mutate_environment() -> None:
    environment = {
        "OPENAI_COMPATIBLE_ENDPOINT": "https://example.test/v1",
        "OPENAI_COMPATIBLE_MODEL": "example-model",
        "OPENAI_COMPATIBLE_API_KEY": "secret",
    }

    assert api_configuration(environment) == (
        "https://example.test/v1",
        "example-model",
        "secret",
    )
    assert environment["OPENAI_COMPATIBLE_API_KEY"] == "secret"


def test_build_process_command_supports_offline_local_transcription() -> None:
    command = build_process_command(
        "https://youtu.be/abc123def45",
        subtitle_mode="bilingual_en_zh",
        translation_provider="offline",
        translation_direction="zh-to-en",
        resume=False,
        python_executable="python.exe",
        main_script=Path("main.py"),
    )

    assert "offline" in command
    assert command[command.index("--translation-direction") + 1] == "zh-to-en"
    assert not any("youtube-chinese" in argument for argument in command)
    assert "--resume" not in command


def test_build_process_command_passes_selected_processing_profile() -> None:
    command = build_process_command(
        "video.mp4",
        subtitle_mode="chinese",
        translation_provider="offline",
        processing_profile="quality",
    )

    assert command[command.index("--processing-profile") + 1] == "quality"


def test_build_process_command_passes_smart_output_controls() -> None:
    command = build_process_command(
        "video.mp4",
        subtitle_mode="chinese",
        translation_provider="offline",
        processing_profile="auto",
        output_quality="best",
        output_fps=60,
        output_height=2160,
    )

    assert command[command.index("--processing-profile") + 1] == "auto"
    assert command[command.index("--output-quality") + 1] == "best"
    assert command[command.index("--output-fps") + 1] == "60"
    assert command[command.index("--output-height") + 1] == "2160"


def test_build_process_command_passes_preview_position_and_manual_size() -> None:
    command = build_process_command(
        "video.mp4",
        subtitle_mode="chinese",
        translation_provider="offline",
        subtitle_font_size=59,
        subtitle_position_x=37,
        subtitle_position_y=81,
    )

    assert command[command.index("--subtitle-font-size") + 1] == "59"
    assert command[command.index("--subtitle-position-x") + 1] == "37"
    assert command[command.index("--subtitle-position-y") + 1] == "81"


def test_subtitle_preview_values_are_clamped_to_renderer_safe_ranges() -> None:
    assert clamp_subtitle_preview_values(-1, 101, 121) == (2, 98, 120)


def test_build_process_command_passes_the_selected_output_directory() -> None:
    command = build_process_command(
        "video.mp4",
        subtitle_mode="chinese",
        translation_provider="offline",
        output_directory=Path("D:/Localized videos"),
    )

    assert command[command.index("--output-dir") + 1] == str(Path("D:/Localized videos"))


def test_build_process_command_supports_local_ai_without_api_key() -> None:
    command = build_process_command(
        "https://youtu.be/abc123def45",
        subtitle_mode="chinese",
        translation_provider="ollama",
        python_executable="python.exe",
        main_script=Path("main.py"),
    )

    assert command[command.index("--translation-provider") + 1] == "ollama"
    assert isinstance(local_ai_available(), bool)


def test_build_process_command_uses_the_single_bundled_font_and_selected_size() -> None:
    command = build_process_command(
        "video.mp4",
        subtitle_mode="chinese",
        translation_provider="offline",
        subtitle_font=DEFAULT_SUBTITLE_FONT,
        subtitle_font_size=SUBTITLE_FONT_SIZES["大号（56）"],
    )

    assert command[command.index("--subtitle-font") + 1] == DEFAULT_SUBTITLE_FONT
    assert command[command.index("--subtitle-font-size") + 1] == "56"
    assert tuple(SUBTITLE_FONTS.values()) == (DEFAULT_SUBTITLE_FONT,)


def test_build_process_command_rejects_an_unsafe_subtitle_font_size() -> None:
    with pytest.raises(ValueError, match="12"):
        build_process_command(
            "video.mp4",
            subtitle_mode="chinese",
            translation_provider="offline",
            subtitle_font_size=121,
        )


def test_build_process_command_supports_direct_download_without_subtitles() -> None:
    command = build_process_command(
        "https://youtu.be/abc123def45",
        subtitle_mode="download_only",
        translation_provider="offline",
    )

    assert command[command.index("--subtitle-mode") + 1] == "download_only"


def test_mode_description_explains_download_only_and_local_ai() -> None:
    assert "不生成字幕" in mode_description("download_only", "ollama")
    assert "完整段落" in mode_description("chinese", "ollama")
    assert "API Key" in mode_description("chinese", "openai-compatible")


def test_progress_updates_show_real_download_translation_and_rendering_progress() -> None:
    value, message = progress_update_from_output(
        "INFO Preflight ready: package=standard", provider="offline"
    ) or (None, "")
    assert value == 2.0
    assert "检查硬件" in message

    value, message = progress_update_from_output(
        "[download] 50.0% of 20MiB", provider="ollama"
    ) or (None, "")
    assert value == 11.0
    assert message == "正在下载原视频：50.0%"

    value, message = progress_update_from_output(
        "INFO Local AI translating paragraph 12/24…", provider="ollama"
    ) or (None, "")
    assert value == 47.0 + 28.0 * 11 / 24
    assert message == "本地 AI 翻译：12/24 段"

    value, message = progress_update_from_output(
        "INFO Rendering subtitles: 75.0%", provider="ollama"
    ) or (None, "")
    assert value == 93.5
    assert message == "正在压制字幕：75.0%"


def test_download_progress_includes_transfer_speed_and_eta() -> None:
    value, message = progress_update_from_output(
        "[download]  50.0% of 100MiB at 12.5MiB/s ETA 00:04",
        provider="download_only",
    ) or (None, "")

    assert value == 50.0
    assert message == "正在下载原视频：50.0% · 12.5MiB/s · 剩余 00:04"


def test_completion_attention_always_rings_the_application_bell() -> None:
    class Root:
        called = False

        def bell(self) -> None:
            self.called = True

    root = Root()
    notify_window_attention(root)  # type: ignore[arg-type]
    assert root.called is True


def test_open_rendered_ignores_short_previews_and_prefers_the_latest_final_video(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    rendered = project / "rendered"
    rendered.mkdir(parents=True)
    (rendered / "review_preview_10.mp4").write_bytes(b"preview")
    first = rendered / "chinese_hardsub.mp4"
    second = rendered / "english_hardsub.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    os.utime(first, (1_700_000_000, 1_700_000_000))
    os.utime(second, (1_700_000_100, 1_700_000_100))
    window = object.__new__(LocalizerWindow)
    window._project_paths_by_queue_index = {1: project}

    assert window._selected_rendered_path(1) == second


def test_windows_gui_process_hides_its_console() -> None:
    if os.name == "nt":
        import subprocess

        flags = gui_process_creationflags()
        assert flags & subprocess.CREATE_NEW_PROCESS_GROUP
        assert flags & subprocess.CREATE_NO_WINDOW
    else:
        assert gui_process_creationflags() == 0


def test_gui_explains_when_a_packaged_install_has_no_whisper_model(monkeypatch) -> None:
    monkeypatch.setattr("youtube_localizer.gui.package_tier", lambda: "standard")
    monkeypatch.setattr("youtube_localizer.gui.installed_whisper_models", lambda: ())

    message = whisper_model_installation_message()

    assert message is not None
    assert "Whisper Small" in message
    assert "帮助中心" in message


@pytest.mark.parametrize(
    ("log_text", "expected"),
    [
        ("HTTP Error 429: Too Many Requests", "暂时限制"),
        ("CUDA error: out of memory", "显存"),
        ("OSError: [Errno 28] No space left on device", "20 GiB"),
        ("FFmpeg hard-subtitle rendering failed", "字幕压制"),
        ("Cloudflare 浏览器验证", "浏览器抓取"),
        ("媒体服务器拒绝了这条直链（HTTP 403）", "重新复制"),
        ("unexpected failure", "导出诊断包"),
    ],
)
def test_friendly_failure_summary_recommends_a_recovery_action(
    log_text: str, expected: str
) -> None:
    assert expected in friendly_failure_summary(log_text)
