"""Verify optional components independently of the base installer tier."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .utils.hashing import hash_file


@dataclass(frozen=True)
class InstalledComponent:
    key: str
    name: str
    installed: bool
    repair_needed: bool
    size_bytes: int
    detail: str


def installation_root() -> Path | None:
    if configured := os.getenv("YOUTUBE_LOCALIZER_HOME"):
        candidate = Path(configured).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
    for parent in Path(sys.executable).resolve().parents[:4]:
        if (parent / "package-tier.txt").is_file():
            return parent
    return None


def _tree_size(*paths: Path) -> int:
    total = 0
    for path in paths:
        if path.is_file():
            total += path.stat().st_size
        elif path.is_dir():
            for item in path.rglob("*"):
                try:
                    if item.is_file():
                        total += item.stat().st_size
                except OSError:
                    continue
    return total


def component_inventory(root: Path | None = None) -> tuple[InstalledComponent, ...]:
    root = root or installation_root()
    if root is None:
        return ()
    models = root / "models"
    runtime = root / "runtime"

    def nonempty(path: Path) -> bool:
        return path.is_file() and path.stat().st_size > 0

    def component(
        key: str,
        name: str,
        storage: tuple[Path, ...],
        required: tuple[Path, ...],
        detail: str,
        *,
        alternatives: tuple[Path, ...] = (),
    ) -> InstalledComponent:
        size = _tree_size(*storage)
        present = size > 0 or any(path.exists() for path in storage)
        complete = all(nonempty(path) for path in required) and (
            not alternatives or any(nonempty(path) for path in alternatives)
        )
        repair_needed = present and not complete
        if repair_needed:
            detail += "（检测到残缺文件，请修复）"
        return InstalledComponent(key, name, complete, repair_needed, size, detail)

    small = models / "faster-whisper-small"
    medium = models / "faster-whisper-medium"
    local_models = models / "ollama"
    local_runtime = runtime / "ollama"
    super_resolution = runtime / "super-resolution"
    manifest = local_models / "manifests" / "registry.ollama.ai" / "library" / "qwen3" / "4b"
    local_required = [local_runtime / "ollama.exe", manifest]
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            for layer in [data["config"], *data["layers"]]:
                digest = str(layer["digest"])
                if (
                    not digest.startswith("sha256:")
                    or len(digest) != 71
                    or any(c not in "0123456789abcdef" for c in digest[7:])
                ):
                    raise ValueError("Invalid layer")
                local_required.append(local_models / "blobs" / digest.replace(":", "-"))
            if not data["layers"]:
                raise ValueError("No layers")
        except (OSError, ValueError, KeyError, TypeError):
            local_required.append(local_models / "invalid-manifest")
    return (
        component(
            "whisper-small",
            "Whisper Small",
            (small,),
            (small / "model.bin", small / "config.json", small / "tokenizer.json"),
            "多数电脑推荐的字幕识别模型",
            alternatives=(small / "vocabulary.txt", small / "vocabulary.json"),
        ),
        component(
            "whisper-medium",
            "Whisper Medium",
            (medium,),
            (medium / "model.bin", medium / "config.json", medium / "tokenizer.json"),
            "更高识别质量，需要更多内存或显存",
            alternatives=(medium / "vocabulary.txt", medium / "vocabulary.json"),
        ),
        component(
            "local-ai",
            "Local AI Qwen3:4b",
            (local_models, local_runtime),
            tuple(local_required),
            "无需 API 的段落翻译",
        ),
        component(
            "super-resolution",
            "AI Super Resolution",
            (super_resolution,),
            (
                super_resolution / "waifu2x-ncnn-vulkan.exe",
                super_resolution / "models-upconv_7_photo" / "noise1_scale2.0x_model.param",
                super_resolution / "models-upconv_7_photo" / "noise1_scale2.0x_model.bin",
                super_resolution / "models-cunet" / "noise1_scale2.0x_model.param",
                super_resolution / "models-cunet" / "noise1_scale2.0x_model.bin",
            ),
            "通用实拍与动画画质增强",
        ),
    )


def verify_optional_components(root: Path, *, selected: str | None = None) -> list[str]:
    verified: list[str] = []

    def require(path: Path) -> None:
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"Installed component file is missing or empty: {path}")

    def digest(path: Path, expected: str) -> None:
        require(path)
        if hash_file(path).lower() != expected.lower():
            raise ValueError(f"Installed component checksum mismatch: {path}")

    for name, expected in {
        "small": "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
        "medium": "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae",
    }.items():
        if selected is not None and selected != f"whisper-{name}":
            continue
        directory = root / "models" / f"faster-whisper-{name}"
        marker = root / "models" / f"model-pack-{name.title()}.json"
        if directory.exists() or marker.exists():
            digest(directory / "model.bin", expected)
            for filename in ("config.json", "tokenizer.json"):
                require(directory / filename)
            vocabulary = directory / "vocabulary.txt"
            require(vocabulary if vocabulary.is_file() else directory / "vocabulary.json")
            verified.append(f"Whisper {name}")

    models = root / "models" / "ollama"
    runtime = root / "runtime" / "ollama" / "ollama.exe"
    if selected in {None, "local-ai"} and (
        models.exists()
        or runtime.exists()
        or (root / "models" / "model-pack-local-ai.json").exists()
    ):
        require(runtime)
        manifest = models / "manifests" / "registry.ollama.ai" / "library" / "qwen3" / "4b"
        require(manifest)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        layers = [data["config"], *data["layers"]]
        if not data["layers"]:
            raise ValueError("Local AI model manifest has no layers.")
        for layer in layers:
            value = layer["digest"]
            if not isinstance(value, str) or not value.startswith("sha256:"):
                raise ValueError("Invalid local AI layer digest.")
            sha = value.removeprefix("sha256:")
            if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                raise ValueError("Invalid local AI layer digest.")
            digest(models / "blobs" / f"sha256-{sha}", sha)
        verified.append("Local AI Qwen3:4b")
    super_resolution = root / "runtime" / "super-resolution"
    if selected in {None, "super-resolution"} and super_resolution.exists():
        require(super_resolution / "waifu2x-ncnn-vulkan.exe")
        manifest = root / "models" / "super-resolution-pack.json"
        if manifest.is_file():
            data = json.loads(manifest.read_text(encoding="utf-8-sig"))
            expected = data.get("executable_sha256", "")
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("Invalid super-resolution executable checksum.")
            digest(super_resolution / "waifu2x-ncnn-vulkan.exe", expected)
        for model in ("models-upconv_7_photo", "models-cunet"):
            for suffix in ("param", "bin"):
                require(super_resolution / model / f"noise1_scale2.0x_model.{suffix}")
        verified.append("AI Super Resolution")
    return verified
