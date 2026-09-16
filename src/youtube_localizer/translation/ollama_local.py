from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import LANGUAGE_NAMES
from ..errors import LocalizerError
from ..models import SubtitleCue
from ..resources import bundled_ollama_models, ollama_executable
from .base import TranslationContext, TranslationProvider
from .cache import TranslationCache

LOGGER = logging.getLogger(__name__)
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
BUNDLED_OLLAMA_ENDPOINT = "http://127.0.0.1:11436"


def validate_local_ollama_endpoint(endpoint: str) -> str:
    normalized = endpoint.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS:
        raise LocalizerError(
            "Local AI translation only accepts an Ollama endpoint on this computer "
            "(for example http://localhost:11434)."
        )
    return normalized


def _translation_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "translation": {"type": "string", "minLength": 1},
        },
        "required": ["translation"],
        "additionalProperties": False,
    }


class LocalOllamaProvider(TranslationProvider):
    """Translate complete subtitle paragraphs with a local Ollama model."""

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        auto_pull: bool,
        cache: TranslationCache,
        source_code: str,
        target_code: str,
        context_tokens: int = 4096,
        timeout: float = 600,
    ) -> None:
        self.endpoint = validate_local_ollama_endpoint(endpoint)
        if bundled_ollama_models() and self.endpoint in {
            "http://localhost:11434",
            "http://127.0.0.1:11434",
        }:
            self.endpoint = validate_local_ollama_endpoint(
                os.getenv("YOUTUBE_LOCALIZER_OLLAMA_ENDPOINT", BUNDLED_OLLAMA_ENDPOINT)
            )
        self.model = model.strip()
        if not self.model:
            raise LocalizerError("A local Ollama model name is required.")
        self.auto_pull = auto_pull
        self.cache = cache
        self.source_code = source_code
        self.target_code = target_code
        self.context_tokens = context_tokens
        self.timeout = timeout
        self._ensure_server()
        self._ensure_model()
        LOGGER.info("Local AI paragraph translation ready with Ollama model %s.", self.model)

    def _server_ready(self, *, timeout: float) -> bool:
        try:
            response = httpx.get(f"{self.endpoint}/api/tags", timeout=timeout)
            response.raise_for_status()
            return True
        except httpx.HTTPError:
            return False

    def _ensure_server(self) -> None:
        if self._server_ready(timeout=2):
            return
        executable = ollama_executable()
        if executable is None:
            raise LocalizerError(
                "Local AI translation could not find Ollama. Reinstall the offline package "
                "or install Ollama, then retry."
            )
        environment = os.environ.copy()
        if models := bundled_ollama_models():
            environment["OLLAMA_MODELS"] = str(models)
        parsed = urlparse(self.endpoint)
        environment["OLLAMA_HOST"] = f"127.0.0.1:{parsed.port or 11434}"
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(
                [str(executable), "serve"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise LocalizerError(f"Could not start the bundled Ollama service: {exc}") from exc
        for _ in range(80):
            if self._server_ready(timeout=1):
                LOGGER.info("Started local Ollama service with bundled runtime.")
                return
            time.sleep(0.25)
        raise LocalizerError("The bundled Ollama service did not become ready within 20 seconds.")

    def _get_models(self) -> set[str]:
        try:
            response = httpx.get(f"{self.endpoint}/api/tags", timeout=10)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LocalizerError(
                "Local AI translation could not reach Ollama. Install and start Ollama, then "
                "retry. Windows install: winget install Ollama.Ollama"
            ) from exc
        models = payload.get("models", []) if isinstance(payload, dict) else []
        return {
            str(item.get("name") or item.get("model"))
            for item in models
            if isinstance(item, dict) and (item.get("name") or item.get("model"))
        }

    def _ensure_model(self) -> None:
        installed = self._get_models()
        if self.model in installed or (
            ":" not in self.model and f"{self.model}:latest" in installed
        ):
            return
        if not self.auto_pull:
            raise LocalizerError(
                f"Local Ollama model {self.model!r} is not installed. Run: ollama pull {self.model}"
            )
        LOGGER.info(
            "Downloading local AI model %s for first use; this may take several minutes…",
            self.model,
        )
        try:
            response = httpx.post(
                f"{self.endpoint}/api/pull",
                json={"model": self.model, "stream": False},
                timeout=max(3600, self.timeout),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LocalizerError(
                f"Could not download local Ollama model {self.model!r}: {exc}"
            ) from exc
        LOGGER.info("Local AI model %s downloaded.", self.model)

    def _messages(
        self,
        cues: list[SubtitleCue],
        context: TranslationContext,
        *,
        correction: str = "",
    ) -> list[dict[str, str]]:
        source_name = LANGUAGE_NAMES.get(self.source_code, self.source_code)
        target_name = f"natural {LANGUAGE_NAMES.get(self.target_code, self.target_code)}"
        glossary = (
            "\nRequired terminology (use these target forms exactly):\n"
            + json.dumps(context.glossary, ensure_ascii=False)
            if context.glossary
            else ""
        )
        system = (
            f"You are a professional audiovisual translator from {source_name} to "
            f"{target_name}. Read every cue as one continuous spoken paragraph before "
            "translating. Produce one complete, fluent paragraph. Translate meaning and tone "
            "naturally instead of word by word. Prefer concise subtitle phrasing that stays "
            "comfortable to read on screen while preserving every fact. "
            "Preserve every fact, name, number, and relationship; do not summarize, omit, "
            "explain ambiguities, add translator notes, or add information. Correct obvious "
            "speech-recognition phrasing only when the surrounding context makes it clear. "
            "If a cue contains an obvious near-homophone or misspelling of the listed channel "
            "or speaker name, use the correct context name. "
            "The video title and channel are context only: never translate, repeat, or prepend "
            "them. Translate only the text between the subtitle paragraph markers. Return only "
            "the complete paragraph translation in the supplied JSON field." + glossary
        )
        separator = "" if self.source_code == "zh" else " "
        paragraph = separator.join(cue.text.strip() for cue in cues)
        user = (
            "Context only (do not output):\n"
            f"Video title: {context.title}\n"
            f"Channel/speaker: {context.channel}\n"
            f"Source language: {self.source_code}; target language: {self.target_code}\n"
            "<subtitle_paragraph>\n"
            f"{paragraph}\n"
            "</subtitle_paragraph>"
        )
        if correction:
            user += f"\nPrevious response was invalid: {correction}. Return only the translation."
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _request(self, cues: list[SubtitleCue], context: TranslationContext) -> str:
        correction = ""
        for attempt in range(2):
            try:
                response = httpx.post(
                    f"{self.endpoint}/api/chat",
                    json={
                        "model": self.model,
                        "messages": self._messages(cues, context, correction=correction),
                        "stream": False,
                        "think": False,
                        "format": _translation_schema(),
                        "options": {
                            "temperature": 0.1,
                            "num_ctx": self.context_tokens,
                            "num_predict": 1024,
                            "repeat_penalty": 1.1,
                        },
                        "keep_alive": "10m",
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
                response_payload = response.json()
                content = response_payload["message"]["content"]
                parsed = json.loads(content)
                translation = str(parsed["translation"]).strip()
                self._validate_translation(translation, cues)
                self._log_performance(response_payload)
                return translation
            except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                correction = str(exc)
                if attempt == 1:
                    raise LocalizerError(
                        f"Local AI paragraph translation failed after validation retry: {exc}"
                    ) from exc
        raise AssertionError("unreachable")

    def _validate_translation(self, translation: str, cues: list[SubtitleCue]) -> None:
        if not translation:
            raise ValueError("blank paragraph translation")
        source_length = sum(len(cue.text) for cue in cues)
        expansion_limit = 8 if self.target_code == "en" else 3
        maximum_length = max(240, source_length * expansion_limit)
        if len(translation) > maximum_length:
            raise ValueError(
                f"paragraph translation is implausibly long ({len(translation)} characters)"
            )
        if len(re.findall(r"(?:^|\s)\d+[.)]", translation)) > 12:
            raise ValueError("paragraph translation contains a runaway numbered sequence")

    def _log_performance(self, payload: dict[str, Any]) -> None:
        """Log Ollama's own token metrics when the local runtime provides them."""
        try:
            evaluated_tokens = int(payload.get("eval_count") or 0)
            evaluated_duration_ns = int(payload.get("eval_duration") or 0)
            prompt_tokens = int(payload.get("prompt_eval_count") or 0)
            total_duration_ns = int(payload.get("total_duration") or 0)
        except (TypeError, ValueError):
            return
        if evaluated_tokens <= 0 or evaluated_duration_ns <= 0:
            return
        tokens_per_second = evaluated_tokens / (evaluated_duration_ns / 1_000_000_000)
        total_seconds = total_duration_ns / 1_000_000_000
        LOGGER.info(
            "Local AI paragraph performance: %s output tokens at %.1f tok/s "
            "(%s prompt tokens, %.2f s total).",
            evaluated_tokens,
            tokens_per_second,
            prompt_tokens,
            total_seconds,
        )

    def translate_paragraph(
        self,
        cues: list[SubtitleCue],
        context: TranslationContext,
    ) -> str:
        payload = {
            "provider": "ollama-paragraph-v1",
            "model": self.model,
            "source_code": self.source_code,
            "target_code": self.target_code,
            "title": context.title,
            "texts": [{"id": cue.id, "text": cue.text} for cue in cues],
            "glossary": context.glossary,
        }
        key = self.cache.key(payload)
        cached = self.cache.get(key)
        translation = str(cached.get("translation", "")).strip() if isinstance(cached, dict) else ""
        if translation:
            try:
                self._validate_translation(translation, cues)
            except ValueError:
                translation = ""
        if not translation:
            translation = self._request(cues, context)
            self.cache.put(key, {"translation": translation})
        return translation

    def translate_batch(
        self,
        cues: list[SubtitleCue],
        context: TranslationContext,
    ) -> list[SubtitleCue]:
        from .offline import split_group_translation

        translation = self.translate_paragraph(cues, context)
        parts = split_group_translation(
            translation,
            cues,
            source_code=self.source_code,
            target_code=self.target_code,
        )
        if parts is None:
            raise LocalizerError("Could not project the local AI paragraph onto cue timings.")
        return [
            cue.model_copy(update={"text": text}) for cue, text in zip(cues, parts, strict=True)
        ]
