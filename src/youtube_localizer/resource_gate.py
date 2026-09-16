from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from time import monotonic
from typing import Literal

from filelock import FileLock

from .hardware import NvidiaGPU, SystemResources, detect_system_resources, query_nvidia_gpus

LOGGER = logging.getLogger(__name__)
WorkloadKind = Literal["compute", "encoder"]


@dataclass(frozen=True)
class ResourceSchedule:
    mode: Literal["serialized", "split"]
    detail: str


def choose_resource_schedule(resources: SystemResources, gpus: list[NvidiaGPU]) -> ResourceSchedule:
    """Choose safe cross-process lanes from stable total hardware capacity.

    A compute job and NVENC render may overlap only on a machine with enough system and GPU
    memory. Whisper and local translation always stay in the same compute lane.
    """
    gpu_memory = max(
        (gpu.total_memory_mib or 0 for gpu in gpus),
        default=0,
    )
    enough_memory = resources.memory_mib is not None and resources.memory_mib >= 24 * 1024
    enough_gpu = gpu_memory >= 10 * 1024
    if enough_memory and enough_gpu:
        return ResourceSchedule(
            "split",
            "high-headroom mode: AI compute and video encoding may overlap",
        )
    reason = (
        "less than 24 GiB system memory" if not enough_memory else "less than 10 GiB GPU memory"
    )
    return ResourceSchedule("serialized", f"safe mode: {reason}")


@lru_cache(maxsize=1)
def detected_resource_schedule() -> ResourceSchedule:
    override = os.getenv("LOCALIZE_STUDIO_RESOURCE_MODE", "").strip().casefold()
    if override in {"serialized", "split"}:
        return ResourceSchedule(override, f"explicit {override} resource mode")  # type: ignore[arg-type]
    return choose_resource_schedule(detect_system_resources(), query_nvidia_gpus())


def heavy_workload_lock_path(
    kind: WorkloadKind = "compute", *, schedule: ResourceSchedule | None = None
) -> Path:
    """Store the cross-process lock outside project and OneDrive folders."""
    if os.name == "nt" and (local_app_data := os.getenv("LOCALAPPDATA")):
        root = Path(local_app_data) / "YouTube Chinese Localizer"
    else:
        root = Path(tempfile.gettempdir()) / "youtube-chinese-localizer"
    active = schedule or detected_resource_schedule()
    lane = kind if active.mode == "split" else "heavy-workload"
    return root / f"{lane}.lock"


@contextmanager
def heavy_workload_slot(label: str, *, kind: WorkloadKind = "compute") -> Iterator[None]:
    """Schedule GPU/encoder-heavy work safely across localizer processes.

    Low-headroom systems use one shared lane. High-headroom NVIDIA systems may overlap one AI
    compute job with one encoder job, while still serializing multiple AI jobs.
    """
    schedule = detected_resource_schedule()
    path = heavy_workload_lock_path(kind, schedule=schedule)
    path.parent.mkdir(parents=True, exist_ok=True)
    started = monotonic()
    lock = FileLock(str(path))
    LOGGER.info(
        "Waiting for the %s performance lane for %s (%s).",
        path.stem,
        label,
        schedule.detail,
    )
    with lock:
        waited = monotonic() - started
        if waited >= 0.2:
            LOGGER.info(
                "Acquired the %s performance lane for %s after %.1f seconds.",
                path.stem,
                label,
                waited,
            )
        else:
            LOGGER.info("Using the %s performance lane for %s.", path.stem, label)
        yield
