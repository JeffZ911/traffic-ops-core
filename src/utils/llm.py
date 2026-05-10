"""
LLM Provider abstraction (per CODE-SPEC §3.1).

Decouples Agent business logic from any specific SDK so a future provider
(Anthropic, OpenAI) is a new class — Agents do not change.

Pricing is delegated to model_catalog_client.estimate_cost; we never
hard-code prices here.
"""

from __future__ import annotations

import os
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from src.db.model_catalog_client import estimate_cost as catalog_estimate_cost


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class LLMResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    duration_ms: int
    model: str
    raw: dict = {}


# ---------------------------------------------------------------------------
# Error sanitisation
# ---------------------------------------------------------------------------

# Match the API key pattern (AIza... 35-39 chars) and any `key=...` URL parameter
_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{30,40}")
_QUERY_KEY_RE = re.compile(r"([?&]key=)[^&\s\"']+", re.IGNORECASE)


def _scrub(msg: str) -> str:
    """Strip any fragment that could leak the GEMINI_API_KEY."""
    msg = _KEY_RE.sub("AIza***REDACTED***", msg)
    msg = _QUERY_KEY_RE.sub(r"\1***REDACTED***", msg)
    return msg


class LLMError(Exception):
    """Raised on any underlying SDK / API failure. Message is API-key-safe."""


# ---------------------------------------------------------------------------
# Base provider
# ---------------------------------------------------------------------------


class BaseLLMProvider(ABC):
    @abstractmethod
    def generate(
        self,
        prompt: str,
        model: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        json_mode: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        ...

    @abstractmethod
    def estimate_cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        ...


# ---------------------------------------------------------------------------
# Gemini implementation
# ---------------------------------------------------------------------------


class GeminiLLMProvider(BaseLLMProvider):
    """Google Gemini via the google-genai SDK (the unified one)."""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("api_key is required")
        from google import genai  # local import keeps import cost low

        self._client = genai.Client(api_key=api_key)

    def generate(
        self,
        prompt: str,
        model: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        json_mode: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        from google.genai import types

        config_kwargs: dict[str, Any] = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_prompt:
            config_kwargs["system_instruction"] = system_prompt
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"

        config = types.GenerateContentConfig(**config_kwargs)

        start = time.perf_counter()
        try:
            response = self._client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
        except Exception as e:
            raise LLMError(_scrub(f"{type(e).__name__}: {e}")) from None
        duration_ms = int((time.perf_counter() - start) * 1000)

        usage = getattr(response, "usage_metadata", None)
        tokens_in = getattr(usage, "prompt_token_count", 0) or 0
        tokens_out = getattr(usage, "candidates_token_count", 0) or 0

        text = getattr(response, "text", None) or ""

        try:
            cost = self.estimate_cost(model, tokens_in, tokens_out)
        except LookupError:
            # Don't fail the call just because pricing row is missing — return 0
            # but the cost will be visibly wrong, prompting investigation.
            cost = 0.0

        # Best-effort raw capture (may not be JSON-serialisable in all SDK versions)
        raw: dict = {}
        try:
            raw = response.to_json_dict() if hasattr(response, "to_json_dict") else {}
        except Exception:
            raw = {}

        return LLMResponse(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            duration_ms=duration_ms,
            model=model,
            raw=raw,
        )

    def estimate_cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        return catalog_estimate_cost(model, tokens_in, tokens_out)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


_PROVIDER_REGISTRY: dict[str, type[BaseLLMProvider]] = {
    "gemini": GeminiLLMProvider,
    # Future:
    # "anthropic": AnthropicLLMProvider,
    # "openai": OpenAILLMProvider,
}


def get_llm_provider(provider_name: str) -> BaseLLMProvider:
    cls = _PROVIDER_REGISTRY.get(provider_name)
    if cls is None:
        raise ValueError(
            f"Unknown LLM provider: {provider_name}. "
            f"Known: {sorted(_PROVIDER_REGISTRY)}"
        )
    api_key = os.getenv(f"{provider_name.upper()}_API_KEY")
    if not api_key:
        raise RuntimeError(f"{provider_name.upper()}_API_KEY not set in env")
    return cls(api_key=api_key)
