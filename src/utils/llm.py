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
    # Search grounding (only populated when generate(enable_search=True))
    search_used: bool = False
    grounding_sources: list[dict] = []   # [{"uri": str, "title": str}, ...]


# Per Gemini pricing 2026-05: dynamic Google Search grounding adds a flat
# fee per request (≈$0.035). Bumped slightly conservatively.
SEARCH_GROUNDING_USD_PER_CALL = 0.035


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
# Model auto-fallback table — survives a Google model retirement
# ---------------------------------------------------------------------------
#
# When `generate(model=X)` raises a 404 / NOT_FOUND / model-deprecated error,
# the provider retries with the next entry in MODEL_FALLBACKS[X] before giving
# up. This is the "理论上应该自动切换" lever — no DB edit, no redeploy needed
# to recover when Google quietly retires a -preview SKU.
#
# Each chain is ordered most→least preferred. Keep all fallbacks inside the
# same tier (text → text, image → image) so behavior doesn't degrade
# unexpectedly. Add a new entry here every time we discover a new retired
# model + a working replacement.
MODEL_FALLBACKS: dict[str, list[str]] = {
    # Text — flash tier
    "gemini-3.1-flash-lite-preview": ["gemini-3.1-flash-preview", "gemini-3-flash-preview"],
    "gemini-3.1-flash-preview":      ["gemini-3-flash-preview", "gemini-2.5-flash"],
    "gemini-3-flash-preview":        ["gemini-2.5-flash"],
    # Text — pro tier
    "gemini-3.1-pro-preview":        ["gemini-2.5-pro"],
    # Image
    "gemini-3.1-flash-image-preview": ["gemini-2.5-flash-image"],
    "gemini-3-pro-image-preview":     ["gemini-2.5-flash-image"],
}


def _is_model_unavailable(err: Exception) -> bool:
    """Return True if the error indicates the model itself is gone/unknown."""
    s = str(err).lower()
    return any(
        k in s for k in (
            "not_found", "not found",
            "404",
            "model is not available", "no longer available",
            "deprecated", "is not supported",
        )
    )


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
        enable_search: bool = False,
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
        enable_search: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        from google.genai import types

        # Note: response_mime_type=application/json is incompatible with
        # google_search tool. When both are requested, drop json_mode and
        # rely on prompt instructions for JSON shape (the agent code parses).
        if enable_search and json_mode:
            json_mode = False

        config_kwargs: dict[str, Any] = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_prompt:
            config_kwargs["system_instruction"] = system_prompt
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"
        if enable_search:
            config_kwargs["tools"] = [
                types.Tool(google_search=types.GoogleSearch())
            ]

        config = types.GenerateContentConfig(**config_kwargs)

        # Auto-fallback chain: if the configured model is retired (404 /
        # NOT_FOUND / deprecated), retry with the next entry in
        # MODEL_FALLBACKS[model] before raising. Prevents one Google
        # model retirement from cascading into "whole pipeline dead until
        # operator fixes DB" — see 2026-05-26 gemini-3.1-flash-lite-preview
        # incident for the case this guards against.
        attempted: list[str] = []
        last_err: Exception | None = None
        try_models = [model] + MODEL_FALLBACKS.get(model, [])

        start = time.perf_counter()
        response = None
        for m in try_models:
            attempted.append(m)
            try:
                response = self._client.models.generate_content(
                    model=m,
                    contents=prompt,
                    config=config,
                )
                if m != model:
                    # Log via stderr (test envs capture stdout but pipeline
                    # logs are stderr-friendly). Importing logging at module
                    # top adds startup cost; print is fine for this rare path.
                    import sys
                    print(
                        f"⚠️  LLM auto-fallback: '{model}' unavailable, used '{m}' instead. "
                        f"Update sites.config to point at '{m}' directly to silence this.",
                        file=sys.stderr,
                    )
                model = m  # so cost estimate + response.model report the actual
                break
            except Exception as e:
                last_err = e
                if not _is_model_unavailable(e):
                    # Hard failure (auth, rate limit, network) — don't burn
                    # through the fallback list on something fallback can't fix.
                    raise LLMError(_scrub(f"{type(e).__name__}: {e}")) from None
                # Else: model gone, try next in chain.
                continue

        if response is None:
            assert last_err is not None
            raise LLMError(
                _scrub(
                    f"All models in fallback chain unavailable. Tried: {attempted}. "
                    f"Last error: {type(last_err).__name__}: {last_err}"
                )
            ) from None
        duration_ms = int((time.perf_counter() - start) * 1000)

        usage = getattr(response, "usage_metadata", None)
        tokens_in = getattr(usage, "prompt_token_count", 0) or 0
        tokens_out = getattr(usage, "candidates_token_count", 0) or 0

        text = getattr(response, "text", None) or ""

        try:
            cost = self.estimate_cost(model, tokens_in, tokens_out)
        except LookupError:
            cost = 0.0
        if enable_search:
            cost += SEARCH_GROUNDING_USD_PER_CALL

        # Extract grounding metadata if search was used
        sources: list[dict] = []
        search_used = False
        if enable_search:
            try:
                cands = getattr(response, "candidates", None) or []
                for cand in cands:
                    gm = getattr(cand, "grounding_metadata", None)
                    if not gm:
                        continue
                    chunks = getattr(gm, "grounding_chunks", None) or []
                    for ch in chunks:
                        web = getattr(ch, "web", None)
                        if web is None:
                            continue
                        uri = getattr(web, "uri", None)
                        title = getattr(web, "title", None)
                        if uri:
                            sources.append({"uri": uri, "title": title or ""})
                            search_used = True
            except Exception:
                # Don't break the call over metadata-shape changes
                pass

        # Best-effort raw capture
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
            search_used=search_used,
            grounding_sources=sources,
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
