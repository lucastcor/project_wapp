"""Native Ollama transport. No cloud fallback or unbounded thinking generation."""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from functools import lru_cache
from threading import BoundedSemaphore
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.logging import get_logger, log_event
from app.llm.providers.business_provider import BusinessLLMProvider

logger = get_logger(__name__)


class OllamaProviderError(Exception):
    pass


@dataclass(frozen=True)
class Generation:
    output_text: str


@lru_cache(maxsize=8)
def _generation_slots(limit: int) -> BoundedSemaphore:
    return BoundedSemaphore(limit)


def validate_structured_output(value: Any, schema: dict[str, Any]) -> None:
    """Validate the deliberately small JSON Schema vocabulary used by our prompts."""
    kinds = schema.get("type", [])
    kinds = [kinds] if isinstance(kinds, str) else kinds
    matches = {
        "object": isinstance(value, dict),
        "string": isinstance(value, str),
        "null": value is None,
        "number": isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
    }
    if not any(matches.get(kind, False) for kind in kinds):
        raise OllamaProviderError("Model returned an invalid structured value type.")
    if "enum" in schema and value not in schema["enum"]:
        raise OllamaProviderError("Model returned an invalid enum value.")
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and (value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf))):
        raise OllamaProviderError("Model returned an out-of-range number.")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if not set(schema.get("required", [])) <= value.keys():
            raise OllamaProviderError("Model omitted a required field.")
        if schema.get("additionalProperties") is False and value.keys() - properties.keys():
            raise OllamaProviderError("Model returned unexpected fields.")
        for key, item in value.items():
            if key in properties:
                validate_structured_output(item, properties[key])


def validate_ollama_configuration() -> None:
    parsed = urlparse(settings.ollama_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise OllamaProviderError("OLLAMA_BASE_URL must be an HTTP(S) endpoint without embedded credentials.")
    if not settings.ollama_model.strip():
        raise OllamaProviderError("OLLAMA_MODEL is required.")
    if (settings.llm_timeout_seconds <= 0 or settings.ollama_num_ctx < 2048
            or settings.ollama_num_predict <= 0 or settings.ollama_max_concurrency < 1):
        raise OllamaProviderError("Invalid Ollama timeout, context, output or concurrency limits.")


def probe_ollama() -> dict[str, Any]:
    """Read-only readiness probe: never load, pull or generate a model."""
    try:
        validate_ollama_configuration()
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(f"{settings.ollama_base_url}/api/tags")
            response.raise_for_status()
            models = {item.get("name") for item in response.json().get("models", [])}
        found = settings.ollama_model in models or f"{settings.ollama_model}:latest" in models
        return {"ok": found, "detail": "Modelo local disponível." if found else "OLLAMA_MODEL não está instalado no servidor configurado."}
    except Exception as exc:
        return {"ok": False, "detail": f"Ollama indisponível ou configuração inválida ({type(exc).__name__})."}


class OllamaProvider(BusinessLLMProvider):
    def __init__(self) -> None:
        validate_ollama_configuration()
        self.model = settings.ollama_model
        self.finetuned_model = None

    def _get_reply_model(self) -> str:
        return self.model

    def _generate(self, *, model: str, input: str, text: dict[str, Any] | None = None) -> Generation:
        schema = text["format"]["schema"] if text else None
        payload: dict[str, Any] = {
            "model": model, "stream": False, "think": False,
            "keep_alive": settings.ollama_keep_alive,
            "messages": [{"role": "user", "content": input}],
            "options": {"temperature": 0, "num_ctx": settings.ollama_num_ctx,
                        "num_predict": settings.ollama_num_predict},
        }
        if schema:
            payload["format"] = schema
        slots = _generation_slots(settings.ollama_max_concurrency)
        start = time.monotonic()
        if not slots.acquire(timeout=settings.llm_timeout_seconds):
            raise OllamaProviderError("Local inference concurrency limit reached.")
        try:
            remaining = settings.llm_timeout_seconds - (time.monotonic() - start)
            if remaining <= 0:
                raise OllamaProviderError("Local inference deadline exceeded.")
            with httpx.Client(timeout=remaining, trust_env=False) as client:
                response = client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
                response.raise_for_status()
                result = response.json()
            if result.get("done") is not True or result.get("done_reason") == "length":
                raise OllamaProviderError("Local generation was incomplete or truncated.")
            content = str(result.get("message", {}).get("content") or "").strip()
            if not content or "<think>" in content or "</think>" in content:
                raise OllamaProviderError("Local generation did not return a final answer.")
            if schema:
                validate_structured_output(json.loads(content), schema)
            log_event(logger, logging.INFO, "llm.generation.completed", provider="ollama", model=model,
                      duration_ms=round((time.monotonic() - start) * 1000), output_tokens=result.get("eval_count"))
            return Generation(output_text=content)
        finally:
            slots.release()
