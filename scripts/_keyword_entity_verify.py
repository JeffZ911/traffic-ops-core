"""Reusable entity-verify gate for keyword_gardener.

Given a candidate keyword string and an LLM provider, decide whether the
keyword references real Neverness to Everness entities. Returns one of:

  - "real"       — keyword names a verified-real character/weapon/boss/mechanic
  - "general"    — abstract / category query (tier list, build, beginner) — keep
  - "fabricated" — keyword names something that doesn't verify in NTE search

Callers use it like:

    from scripts._keyword_entity_verify import verify_keyword, VerifyResult

    res = verify_keyword(provider, model, "minerva best build nte")
    if res.verdict == "archive":
        # drop + alert
        ...

The gate uses Google-grounded LLM. Cost ≈ $0.005 per call. The script
is intentionally conservative: any inconclusive search result counts as
fabricated. Better to drop a legitimate keyword (re-discoverable from
GSC long-tail later) than to admit a hallucinated entity that produces
a $0.40 qa_failed article.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from src.agents._json_extract import extract_json


VERIFY_PROMPT = """You are a fact-checker for a Neverness to Everness (NTE,
released 2026-04-29) guide site. The keyword below is a search query
(lowercase). It MAY contain specific entities (character / weapon / Arc
/ boss / mechanic NAMES) hidden in lowercase, OR it may be a general
topic query (tier list, build guide, beginner reroll, etc.).

Identify any tokens that LOOK like specific named entities (proper
names — not categories like "dps" / "healer" / "build" / "tier list"),
and verify EACH one against real NTE info via Google Search.

Keyword: {keyword!r}

Rules:
- A token is 'real' ONLY if you can find at least one credible source
  (official game site, prydwen.gg, Game8, Reddit r/NeverNess, IGN,
  GameSpot, or similar) that mentions this entity in NTE context.
- A token is 'fabricated' if search shows it does not exist in NTE,
  OR if your search is inconclusive. Be CONSERVATIVE — when in doubt,
  fabricated.
- If the keyword has no candidate named entities at all (pure category
  query like 'best dps build nte'), set entity_status='general'.

Reply ONLY with JSON (no markdown fence):
{{
  "candidate_nouns": ["..."],
  "per_noun": [{{"noun": "<name>", "verdict": "real|fabricated",
                 "evidence": "<one short sentence>"}}],
  "entity_status": "real" | "fabricated" | "general" | "mixed",
  "verdict": "keep" | "archive",
  "reason": "<one sentence>"
}}

Decision rules:
- No candidate nouns → entity_status='general', verdict='keep'.
- All candidate nouns 'real' → entity_status='real', verdict='keep'.
- ANY noun 'fabricated' → verdict='archive'.
"""


@dataclass
class VerifyResult:
    keyword: str
    entity_status: str               # 'real' | 'general' | 'fabricated' | 'mixed' | 'error'
    verdict: str                     # 'keep' | 'archive'
    fabricated_entities: list[str] = field(default_factory=list)
    candidate_nouns: list[str] = field(default_factory=list)
    reason: str = ""
    cost_usd: float = 0.0
    raw: dict[str, Any] | None = None


def verify_keyword(
    provider, model: str, keyword: str,
    max_tokens: int = 2000, temperature: float = 0.1,
) -> VerifyResult:
    """Call the LLM verify. On any error, returns entity_status='error'
    with verdict='keep' so the gardener fails open (we'd rather let a
    keyword through than block the entire top-up because the verify
    step crashed)."""
    kw = (keyword or "").strip()
    if not kw:
        return VerifyResult(
            keyword=keyword, entity_status="general", verdict="keep",
            reason="empty keyword",
        )
    prompt = VERIFY_PROMPT.format(keyword=kw)
    try:
        resp = provider.generate(
            prompt=prompt, model=model, max_tokens=max_tokens,
            temperature=temperature, json_mode=True, enable_search=True,
        )
    except Exception as e:
        return VerifyResult(
            keyword=kw, entity_status="error", verdict="keep",
            reason=f"verify call failed: {str(e)[:160]}",
        )
    cost = float(resp.cost_usd or 0)

    try:
        j = extract_json(resp.text)
    except Exception as e:
        return VerifyResult(
            keyword=kw, entity_status="error", verdict="keep",
            reason=f"verify JSON parse failed: {str(e)[:160]}",
            cost_usd=cost,
        )

    status = (j.get("entity_status") or "error").lower()
    verdict = (j.get("verdict") or "keep").lower()
    if verdict not in ("keep", "archive"):
        verdict = "keep"   # fail-open on weird verdicts
    fab = [
        n.get("noun") for n in (j.get("per_noun") or [])
        if isinstance(n, dict) and (n.get("verdict") == "fabricated")
    ]
    fab = [x for x in fab if x]
    return VerifyResult(
        keyword=kw,
        entity_status=status,
        verdict=verdict,
        fabricated_entities=fab,
        candidate_nouns=list(j.get("candidate_nouns") or []),
        reason=j.get("reason", "") or "",
        cost_usd=cost,
        raw=j,
    )
