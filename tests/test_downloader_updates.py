from __future__ import annotations

import hashlib
import io
import json
import zipfile
from unittest.mock import MagicMock

import pytest

from youtube_localizer import downloader_updates as updates
from youtube_localizer.errors import LocalizerError


@pytest.mark.parametrize("corrupt", [False, True])
def test_engine_update_only_activates_hash_verified_and_self_tested_package(
    tmp_path, monkeypatch, corrupt
):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("yt_dlp/version.py", "__version__ = '2026.10.7'")
        archive.writestr("unrelated_package/__init__.py", "not installed")
    payload = buffer.getvalue()
    metadata = {
        "info": {"version": "2026.10.7", "requires_python": ">=3.11"},
        "urls": [
            {
                "filename": "yt_dlp-2026.10.7-py3-none-any.whl",
                "url": "https://files.pythonhosted.org/test.whl",
                "digests": {"sha256": "0" * 64 if corrupt else hashlib.sha256(payload).hexdigest()},
            }
        ],
    }
    response = MagicMock()
    response.json.return_value = metadata
    download = MagicMock()
    download.__enter__.return_value = download
    download.iter_bytes.return_value = [payload]
    run = MagicMock()
    # yt-dlp pads its date version, whereas PyPI normalizes away leading zeroes.
    run.return_value.stdout = "2026.10.07\n"
    monkeypatch.setattr(updates, "_root", lambda: tmp_path)
    monkeypatch.setattr(updates.httpx, "get", lambda *args, **kwargs: response)
    monkeypatch.setattr(updates.httpx, "stream", lambda *args, **kwargs: download)
    monkeypatch.setattr(updates, "run_command", run)
    if corrupt:
        with pytest.raises(LocalizerError, match="SHA-256"):
            updates.update_downloader()
        assert not (tmp_path / "active.json").exists()
        run.assert_not_called()
    else:
        assert updates.update_downloader() == "2026.10.7"
        assert json.loads((tmp_path / "active.json").read_text())["version"] == "2026.10.7"
        assert not (tmp_path / "2026.10.7" / "unrelated_package").exists()
        updates.rollback_downloader_update()
        assert not (tmp_path / "active.json").exists()
