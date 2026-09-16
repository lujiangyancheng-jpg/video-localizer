"""Create privacy-conscious diagnostic bundles for support requests."""

from __future__ import annotations

import json
import platform
import re
import sys
import zipfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .doctor import run_doctor
from .models import ProjectPaths
from .utils.files import load_json

_SENSITIVE_KEY_PARTS = (
    "key",
    "token",
    "secret",
    "password",
    "authorization",
    "endpoint",
    "url",
    "directory",
    "path",
    "input",
    "output_file",
)
_SOURCE_IDENTIFIERS = {
    "source_input",
    "source_url",
    "thumbnail_url",
    "description",
    "title",
    "channel",
    "video_id",
    "upload_date",
}
_URL_PATTERN = re.compile(r"https?://[^\s'\"]+", re.IGNORECASE)
_WINDOWS_PATH_PATTERN = re.compile(r"(?:[A-Za-z]:\\|/)[^\s'\"]+")
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)((?:api[ _-]?key|token|secret|password|authorization)\s*[=:]\s*)[^\s,;]+"
)


def _redact_text(value: str) -> str:
    value = _URL_PATTERN.sub("<redacted-url>", value)
    value = _WINDOWS_PATH_PATTERN.sub("<redacted-path>", value)
    return _CREDENTIAL_PATTERN.sub(r"\1<redacted>", value)


def _redact_value(value: Any, *, key: str = "", metadata: bool = False) -> Any:
    normalized_key = key.casefold()
    if metadata and normalized_key in _SOURCE_IDENTIFIERS:
        return "<redacted>"
    if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(item_key): _redact_value(item_value, key=str(item_key), metadata=metadata)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item, key=key, metadata=metadata) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _json_file(
    project: ProjectPaths, path: Path, *, metadata: bool = False
) -> tuple[str, str] | None:
    if not path.is_file():
        return None
    try:
        payload = _redact_value(load_json(path), metadata=metadata)
        if isinstance(payload, dict) and isinstance(payload.get("source"), dict):
            payload["source"] = _redact_value(payload["source"], metadata=True)
    except (OSError, TypeError, ValueError):
        return None
    return f"project/{path.relative_to(project.root).as_posix()}", json.dumps(
        payload, ensure_ascii=False, indent=2
    ) + "\n"


def create_support_bundle(project: ProjectPaths, destination: Path | None = None) -> Path:
    """Write a zip with diagnostics, never source media, subtitles, or credentials."""
    if not project.root.is_dir() or not project.state_file.is_file():
        raise ValueError(f"Not a localization project directory: {project.root}")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    bundle = (destination or project.root / f"support-bundle-{timestamp}.zip").expanduser()
    if bundle.is_dir() or (destination is not None and not bundle.suffix):
        bundle = bundle / f"support-bundle-{timestamp}.zip"
    bundle.parent.mkdir(parents=True, exist_ok=True)

    doctor = [check.__dict__ for check in run_doctor(project.root)]
    diagnostics = {
        "application_version": __version__,
        "created_utc": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "doctor": doctor,
        "privacy": "Source media, subtitle text, source identifiers, paths, URLs, and credentials are excluded or redacted.",
    }
    entries: list[tuple[str, str]] = [
        ("diagnostics.json", json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n")
    ]
    for path, metadata in (
        (project.metadata, True),
        (project.state_file, False),
        (project.root / "config.resolved.json", False),
        (project.logs / "report.json", False),
        (project.logs / "subtitle_quality.json", False),
    ):
        if entry := _json_file(project, path, metadata=metadata):
            entries.append(entry)
    pipeline_log = project.logs / "pipeline.log"
    if pipeline_log.is_file():
        with suppress(OSError):
            entries.append(
                (
                    "project/logs/pipeline.log",
                    _redact_text(pipeline_log.read_text(encoding="utf-8", errors="replace")),
                )
            )

    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return bundle.resolve()
