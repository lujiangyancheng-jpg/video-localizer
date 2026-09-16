"""Short-lived media inspection cache shared by the desktop preview and pipeline."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .models import SourceMetadata
from .onboarding import onboarding_state_directory
from .utils.files import atomic_write_json, load_json
from .utils.hashing import hash_text

INSPECTION_CACHE_SCHEMA_VERSION = 1
REMOTE_CACHE_TTL_SECONDS = 30 * 60
DIRECT_CACHE_TTL_SECONDS = 5 * 60
LOCAL_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60


def _normalized_source(source: str) -> str:
    value = source.strip()
    if value.casefold().startswith(("http://", "https://")):
        return value
    return os.path.normcase(str(Path(value).expanduser().resolve()))


def inspection_cache_path(
    source: str,
    *,
    environment: Mapping[str, str] | None = None,
    cache_directory: Path | None = None,
) -> Path:
    root = cache_directory or onboarding_state_directory(environment) / "inspection-cache"
    return root / f"{hash_text(_normalized_source(source))}.json"


def _local_fingerprint(source: str) -> dict[str, int] | None:
    path = Path(source).expanduser().resolve()
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def save_cached_inspection(
    source: str,
    metadata: SourceMetadata,
    *,
    environment: Mapping[str, str] | None = None,
    cache_directory: Path | None = None,
    now: float | None = None,
) -> Path:
    """Persist non-secret inspection fields without retaining signed source URLs."""
    payload = metadata.model_dump(mode="json")
    payload["source_input"] = ""
    payload["source_url"] = None
    path = inspection_cache_path(source, environment=environment, cache_directory=cache_directory)
    atomic_write_json(
        path,
        {
            "schema_version": INSPECTION_CACHE_SCHEMA_VERSION,
            "saved_at": time.time() if now is None else now,
            "source_hash": hash_text(_normalized_source(source)),
            "local_fingerprint": (
                _local_fingerprint(source) if metadata.source_type == "local" else None
            ),
            "metadata": payload,
        },
    )
    return path


def load_cached_inspection(
    source: str,
    *,
    environment: Mapping[str, str] | None = None,
    cache_directory: Path | None = None,
    now: float | None = None,
) -> SourceMetadata | None:
    """Return a fresh cached inspection, or ``None`` when it is stale or invalid."""
    normalized = _normalized_source(source)
    path = inspection_cache_path(source, environment=environment, cache_directory=cache_directory)
    try:
        data = load_json(path)
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != INSPECTION_CACHE_SCHEMA_VERSION:
        return None
    if data.get("source_hash") != hash_text(normalized):
        return None
    saved_at = data.get("saved_at")
    metadata_payload = data.get("metadata")
    if isinstance(saved_at, bool) or not isinstance(saved_at, (int, float)):
        return None
    if not isinstance(metadata_payload, dict):
        return None
    try:
        metadata = SourceMetadata.model_validate(metadata_payload)
    except (TypeError, ValueError):
        return None
    ttl = {
        "youtube": REMOTE_CACHE_TTL_SECONDS,
        "direct_media": DIRECT_CACHE_TTL_SECONDS,
        "local": LOCAL_CACHE_TTL_SECONDS,
    }.get(metadata.source_type)
    if ttl is None or (time.time() if now is None else now) - float(saved_at) > ttl:
        return None
    if metadata.source_type == "local":
        fingerprint = data.get("local_fingerprint")
        if not isinstance(fingerprint, dict) or fingerprint != _local_fingerprint(source):
            return None
        source_value = str(Path(source).expanduser().resolve())
    else:
        source_value = source.strip()
    return metadata.model_copy(update={"source_input": source_value, "source_url": source_value})


def cached_raw_metadata(metadata: SourceMetadata) -> dict[str, Any]:
    """Build the compact raw-metadata artifact used when an inspection request was reused."""
    return {
        "_localizer_inspection_cache": True,
        "id": metadata.video_id,
        "title": metadata.title,
        "channel": metadata.channel,
        "duration": metadata.duration,
        "width": metadata.width,
        "height": metadata.height,
        "fps": metadata.frame_rate,
    }
