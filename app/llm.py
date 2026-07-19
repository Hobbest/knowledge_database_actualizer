from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from functools import lru_cache
from typing import TypeVar

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Substrings that identify a rate-limit / quota error across providers.
_RATE_LIMIT_MARKERS = (
    "rate limit",
    "ratelimit",
    "rate_limit",
    "quota",
    "resource exhausted",
    "resource_exhausted",
    "too many requests",
    "insufficient_quota",
    "429",
)


def is_rate_limit_error(exc: BaseException) -> bool:
    """Best-effort detection of provider rate-limit / quota errors."""
    for attr in ("status_code", "code", "status", "http_status"):
        value = getattr(exc, attr, None)
        if value is not None and str(value) == "429":
            return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


def backoff_delay(attempt: int) -> float:
    """Exponential backoff delay (seconds) for the given 1-based retry attempt."""
    delay = settings.llm_retry_base_delay * (2 ** max(0, attempt - 1))
    return min(delay, settings.llm_retry_max_delay)


def call_with_retry(
    func: Callable[[], T],
    *,
    on_wait: Callable[[int, float, BaseException], None] | None = None,
) -> T:
    """Call ``func`` and retry on rate-limit errors with exponential backoff.

    Non-rate-limit errors propagate immediately. After ``llm_max_retries``
    exhausted retries the last rate-limit error propagates so callers can decide
    how to degrade (e.g. an extractive fallback).
    """
    attempt = 0
    while True:
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - re-raised unless it's a rate limit
            if not is_rate_limit_error(exc) or attempt >= settings.llm_max_retries:
                raise
            attempt += 1
            delay = backoff_delay(attempt)
            if on_wait is not None:
                on_wait(attempt, delay, exc)
            else:
                logger.warning(
                    "LLM rate limited; retrying in %.0fs (attempt %d/%d)",
                    delay,
                    attempt,
                    settings.llm_max_retries,
                )
            time.sleep(delay)


# Batch note drafts need headroom; single notes are smaller.
_DEFAULT_MAX_OUTPUT_TOKENS = 4096
_JSON_MAX_OUTPUT_TOKENS = 8192


class LLMProvider(ABC):
    @abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_mode: bool = False,
    ) -> str:
        raise NotImplementedError


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_mode: bool = False,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": _JSON_MAX_OUTPUT_TOKENS if json_mode else _DEFAULT_MAX_OUTPUT_TOKENS,
        }
        if json_mode:
            # OpenAI requires a top-level JSON object when this is set.
            kwargs["response_format"] = {"type": "json_object"}
        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_mode: bool = False,
    ) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=_JSON_MAX_OUTPUT_TOKENS if json_mode else _DEFAULT_MAX_OUTPUT_TOKENS,
            system=system or "You are a helpful assistant for knowledge management.",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        parts = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                parts.append(getattr(block, "text", ""))
        return "\n".join(parts)


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        from google import genai

        self.client = genai.Client(api_key=api_key)
        self.model = model

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_mode: bool = False,
    ) -> str:
        from google.genai import types

        config_kwargs: dict = {
            "system_instruction": system
            or "You are a helpful assistant for knowledge management.",
            "temperature": 0.3,
            "max_output_tokens": (
                _JSON_MAX_OUTPUT_TOKENS if json_mode else _DEFAULT_MAX_OUTPUT_TOKENS
            ),
        }
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"
        config = types.GenerateContentConfig(**config_kwargs)
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )
        return response.text or ""


class OllamaProvider(LLMProvider):
    """Local OpenAI-compatible chat via Ollama (or any compatible server)."""

    def __init__(self, model: str, base_url: str):
        from openai import OpenAI

        # Ollama does not require a real key; the client still wants a string.
        self.client = OpenAI(base_url=f"{base_url.rstrip('/')}/v1", api_key="ollama")
        self.model = model
        self.base_url = base_url

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_mode: bool = False,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""


def _llm_settings_key() -> tuple:
    return (
        settings.llm_enabled,
        (settings.llm_provider or "").lower(),
        settings.llm_model or "",
        settings.llm_api_key or "",
        settings.ollama_base_url,
    )


def _create_llm_provider(config: tuple) -> LLMProvider | None:
    enabled, provider, model, api_key, ollama_base_url = config
    if not enabled:
        return None
    if provider == "openai":
        return OpenAIProvider(api_key, model)
    if provider == "anthropic":
        return AnthropicProvider(api_key, model)
    if provider == "gemini":
        return GeminiProvider(api_key, model)
    if provider in {"ollama", "local"}:
        return OllamaProvider(model, ollama_base_url)
    raise ValueError(
        f"Unsupported LLM_PROVIDER '{settings.llm_provider}'. "
        "Use openai, anthropic, gemini, or ollama (local)."
    )


@lru_cache(maxsize=1)
def _cached_llm_provider(config: tuple) -> LLMProvider | None:
    return _create_llm_provider(config)


def get_llm_provider() -> LLMProvider | None:
    return _cached_llm_provider(_llm_settings_key())


def clear_llm_provider_cache() -> None:
    _cached_llm_provider.cache_clear()


def get_llm_provider_uncached() -> LLMProvider | None:
    """Build a fresh provider (tests / smoke scripts that patch settings)."""
    return _create_llm_provider(_llm_settings_key())


__all__ = [
    "LLMProvider",
    "AnthropicProvider",
    "GeminiProvider",
    "OpenAIProvider",
    "OllamaProvider",
    "get_llm_provider",
    "clear_llm_provider_cache",
    "get_llm_provider_uncached",
    "is_rate_limit_error",
    "backoff_delay",
    "call_with_retry",
]
