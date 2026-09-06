"""Verify optional components independently of the base installer tier."""

from __future__ import annotations

import json
from pathlib import Path

from .utils.hashing import hash_file


def verify_optional_components(root: Path) -> list[str]:
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
    if (
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
    return verified
