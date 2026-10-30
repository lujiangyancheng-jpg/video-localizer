from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import httpx
import pytest

from youtube_localizer import network
from youtube_localizer.config import DownloadConfig
from youtube_localizer.download import youtube
from youtube_localizer.errors import InputValidationError
from youtube_localizer.inspection_cache import load_cached_inspection, save_cached_inspection
from youtube_localizer.models import SourceMetadata
from youtube_localizer.network import NetworkRoute, NetworkSettings, classify_network_failure
from youtube_localizer.network import load_network_settings as read_settings


@pytest.fixture(autouse=True)
def isolated_route(monkeypatch):
    monkeypatch.setattr(network, "_active", None)
    monkeypatch.delenv(network.ROUTE_ENV, raising=False)
    monkeypatch.setattr(network, "getproxies", lambda: {})
    monkeypatch.setattr(network, "load_network_settings", lambda: NetworkSettings("direct"))


@pytest.mark.parametrize(
    "value",
    [
        "http://user:secret@localhost:7890",
        "socks4://localhost:1080",
        "http://localhost",
        "http://localhost:0",
        "http://localhost:99999",
        "http://localhost:7890/path",
        "http://localhost:7890?token=secret",
        "http://localhost:7890#x",
        "",
        "a b:12",
    ],
)
def test_proxy_rejects_credentials_unsupported_protocol_and_invalid_endpoint(value):
    with pytest.raises(InputValidationError):
        network.normalize_proxy(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("127.0.0.1:7890", "http://127.0.0.1:7890"),
        ("socks5://[::1]:1080/", "socks5://[::1]:1080"),
        ("https://proxy.test:443", "https://proxy.test:443"),
    ],
)
def test_proxy_normalization(value, expected):
    assert network.normalize_proxy(value) == expected


def test_system_route_freezes_one_proxy_and_direct_ignores_environment(monkeypatch):
    monkeypatch.setattr(
        network,
        "getproxies",
        lambda: {
            "https": "http://127.0.0.1:7890",
            "http": "http://127.0.0.1:7891",
            "no": "*",
        },
    )
    route = network.resolve_route(NetworkSettings())
    assert route.proxy == "http://127.0.0.1:7890"
    assert route.httpx_options() == {"proxy": route.proxy, "trust_env": False}
    assert "--proxy-server=" + route.proxy in route.browser_args()
    direct = network.resolve_route(NetworkSettings("direct"))
    assert direct.proxy == ""
    assert direct.browser_args() == ["--no-proxy-server"]


def test_route_snapshot_survives_worker_process_without_global_proxy_mutation(monkeypatch):
    route = NetworkRoute("manual", "http://127.0.0.1:7890")
    env = route.environment({"HTTP_PROXY": "unchanged"})
    assert env["HTTP_PROXY"] == "unchanged"
    assert env["NO_PROXY"] == "localhost,127.0.0.1,::1"
    monkeypatch.setenv(network.ROUTE_ENV, env[network.ROUTE_ENV])
    assert network.current_route() == route
    with network.using_route(NetworkRoute("direct")):
        assert network.current_route().proxy == ""
    assert network.current_route() == route


def test_invalid_route_snapshot_does_not_fall_back_to_direct(monkeypatch):
    monkeypatch.setenv(network.ROUTE_ENV, '{"mode":"manual","proxy":""}')
    with pytest.raises(InputValidationError, match="停止连接"):
        network.current_route()


def test_network_settings_are_user_local_and_corruption_is_not_silent(tmp_path):
    path = tmp_path / "network.json"
    network.save_network_settings(NetworkSettings("manual", "127.0.0.1:7890"), path)
    assert json.loads(path.read_text())["proxy"] == "http://127.0.0.1:7890"
    assert "password" not in path.read_text()
    assert read_settings(path).mode == "manual"
    path.write_text("invalid json")
    with pytest.raises(InputValidationError, match="未自动改走直连"):
        read_settings(path)


@pytest.mark.parametrize("payload", [
    {"mode": "manual", "proxy": 123}, {"mode": [], "proxy": ""},
    {"mode": "direct", "proxy": False}, {"mode": "manual", "proxy": {}},
    [], None,
])
def test_malformed_settings_always_produce_actionable_errors(tmp_path, payload):
    path = tmp_path / "network.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(InputValidationError):
        read_settings(path)


@pytest.mark.parametrize("payload", [
    {"mode": [], "proxy": ""}, {"mode": "manual", "proxy": 0},
    {"mode": "direct", "proxy": False},
])
def test_malformed_worker_snapshot_fails_closed(monkeypatch, payload):
    monkeypatch.setenv(network.ROUTE_ENV, json.dumps(payload))
    with pytest.raises(InputValidationError):
        network.current_route()


def test_local_media_and_downloaded_projects_ignore_broken_proxy(tmp_path):
    video = tmp_path / "local.mp4"
    video.touch()
    project = tmp_path / "project"
    (project / "source").mkdir(parents=True)
    (project / "source" / "source_video.mp4").touch()
    with patch.object(network, "current_route", side_effect=InputValidationError("broken proxy")):
        assert network.route_for_sources([str(video), str(project)]) == NetworkRoute("direct")
        with pytest.raises(InputValidationError):
            network.route_for_sources([str(video), "https://media.test/video.mp4"])
        with pytest.raises(InputValidationError):
            network.route_for_sources([str(tmp_path / "missing-remote-project")])


@pytest.mark.parametrize(
    ("error", "code"),
    [
        ("HTTP Error 429: Too Many Requests", "rate_limit"),
        ("HTTP Error 403: Forbidden", "forbidden"),
        ("not available in your country", "region"),
        ("HTTP Error 401", "login"),
        ("HTTP Error 407", "proxy_auth"),
        ("HTTP Error 503", "server"),
        ("HTTP Error 410", "expired"),
        (socket.gaierror("getaddrinfo failed"), "dns"),
        (httpx.ConnectTimeout(""), "timeout"),
        (httpx.ProxyError(""), "proxy"),
        ("CERTIFICATE_VERIFY_FAILED", "tls"),
        ("Cloudflare captcha", "challenge"),
        ("unknown", "unknown"),
    ],
)
def test_network_diagnosis(error, code):
    assert classify_network_failure(error).code == code


def test_403_is_not_claimed_to_be_ip_block_or_geo_failure_and_secrets_are_not_echoed():
    failure = classify_network_failure("HTTP Error 403 https://site.test/a?token=SECRET")
    assert failure.code == "forbidden"
    assert "不能仅凭" in failure.message
    assert "SECRET" not in failure.message


def test_cache_cannot_reuse_inspection_from_another_exit_route(tmp_path):
    source = "https://media.test/demo.mp4?signature=secret"
    metadata = SourceMetadata(
        source_type="direct_media", source_input=source, video_id="demo", title="Demo"
    )
    with network.using_route(NetworkRoute("manual", "http://localhost:7890")):
        path = save_cached_inspection(source, metadata, cache_directory=tmp_path)
        assert load_cached_inspection(source, cache_directory=tmp_path) is not None
    with network.using_route(NetworkRoute("manual", "http://localhost:7891")):
        assert load_cached_inspection(source, cache_directory=tmp_path) is None
    assert "localhost" not in path.read_text()
    assert "secret" not in path.read_text()


@contextmanager
def recording_proxy():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b"demo")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_real_httpx_and_ytdlp_requests_share_proxy_not_ambient_environment(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "*")
    with recording_proxy() as (proxy, requests), network.using_route(NetworkRoute("manual", proxy)):
        target = "http://media.invalid/authorized.mp4"
        with httpx.Client(**network.current_route().httpx_options()) as client:
            assert client.get(target).content == b"demo"
        with youtube._youtube_dl({"quiet": True}) as ydl:
            assert ydl.urlopen(target).read() == b"demo"
        report = network.test_connection(target, network.current_route())
        assert "HTTP 200" in report
        assert "仍需实际解析" in report
    assert requests == [target, target, target]


def test_download_route_options_prevent_ffmpeg_environment_proxy_leak():
    with network.using_route(NetworkRoute("direct")), patch("yt_dlp.YoutubeDL") as factory:
        youtube._youtube_dl({})
    options = factory.call_args.args[0]
    assert options["proxy"] == ""
    assert options["external_downloader_args"]["ffmpeg_i"] == ["-http_proxy", ""]
    assert options["hls_prefer_native"] is True


def test_socks_uses_native_downloader_and_does_not_silently_fall_back_for_remote_preview():
    with network.using_route(NetworkRoute("manual", "socks5://127.0.0.1:1080")):
        with patch("yt_dlp.YoutubeDL") as factory:
            youtube._youtube_dl({})
        assert factory.call_args.args[0]["external_downloader"] == {"default": "native"}
        with pytest.raises(InputValidationError, match="HTTP 端口"):
            youtube._youtube_dl({"download_ranges": lambda: []})


def test_retry_reduces_fragment_concurrency_and_retains_partial_download(tmp_path):
    attempts = []

    def run(url, options):
        attempts.append(dict(options))
        if len(attempts) == 1:
            raise RuntimeError("HTTP Error 429: Too Many Requests")
        target = tmp_path / "download.mp4"
        target.touch()
        return target

    with (
        patch.object(youtube, "_run_youtube_download", side_effect=run),
        patch.object(youtube.time, "sleep") as sleep,
    ):
        result = youtube.download_media(
            "https://media.test/a.mp4",
            tmp_path,
            DownloadConfig(retry_attempts=2, retry_delay_seconds=3),
            source_description="test",
        )
    assert result.name == "source_video.mp4"
    assert attempts[0]["concurrent_fragment_downloads"] == 4
    assert attempts[1]["concurrent_fragment_downloads"] == 1
    sleep.assert_called_once_with(3)
