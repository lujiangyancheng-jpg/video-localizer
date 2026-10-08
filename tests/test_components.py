from __future__ import annotations

import hashlib
import threading
from unittest.mock import MagicMock

import pytest

from youtube_localizer import components
from youtube_localizer.errors import LocalizerError


@pytest.mark.parametrize("corrupt", [False, True])
def test_component_download_verifies_every_part_before_install(tmp_path, monkeypatch, corrupt):
    monkeypatch.setattr(components, "onboarding_state_directory", lambda: tmp_path)
    names = components.component_asset_names("whisper-small")
    payloads = {name: name.encode() for name in names}
    assets = [
        {
            "name": name,
            "size": len(payloads[name]),
            "digest": "sha256:" + hashlib.sha256(payloads[name]).hexdigest(),
            "browser_download_url": f"{components.REPOSITORY_URL}/releases/download/v{components.model_compatibility_version()}/{name}",
        }
        for name in names
    ]
    response = MagicMock()
    response.json.return_value = {"assets": assets}
    monkeypatch.setattr(components.httpx, "get", lambda *args, **kwargs: response)

    def stream(_method, url, **kwargs):
        response = MagicMock()
        response.__enter__.return_value = response
        name = url.rsplit("/", 1)[1]
        response.iter_bytes.return_value = [
            b"corrupted" if corrupt and name.endswith(".bin") else payloads[name]
        ]
        return response

    monkeypatch.setattr(components.httpx, "stream", stream)
    run = MagicMock()
    monkeypatch.setattr(components, "run_command", run)
    if corrupt:
        with pytest.raises(LocalizerError, match="SHA-256"):
            components.install_component(
                "whisper-small", tmp_path / "app", progress=lambda _: None, cancel=threading.Event()
            )
        run.assert_not_called()
    else:
        components.install_component(
            "whisper-small", tmp_path / "app", progress=lambda _: None, cancel=threading.Event()
        )
        assert run.call_count == 1
        command = run.call_args.args[0]
        assert str(command[0]).endswith(names[0])
        assert f"/DIR={tmp_path / 'app'}" in command
