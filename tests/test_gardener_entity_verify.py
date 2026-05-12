"""Unit tests for the keyword_gardener entity-verify gate.

Covers both the standalone verify helper (scripts._keyword_entity_verify)
and the gate logic (_gate_keywords_by_entity_verify) — all with a mocked
LLM so the suite is fast and deterministic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from scripts._keyword_entity_verify import VerifyResult, verify_keyword


SITE_ID = UUID("11111111-1111-1111-1111-111111111111")


# ----------- FakeLLM helper ----------


@dataclass
class FakeResp:
    text: str
    tokens_in: int = 100
    tokens_out: int = 100
    cost_usd: float = 0.005
    duration_ms: int = 500
    model: str = "gemini-3.1-pro-preview"
    grounding_sources: list = None

    def __post_init__(self):
        if self.grounding_sources is None:
            self.grounding_sources = []


def _llm_returning(texts: list[str]):
    """Build a provider mock that returns each text in sequence."""
    provider = MagicMock()
    iterator = iter(texts)

    def gen(**kwargs):
        return FakeResp(text=next(iterator))

    provider.generate.side_effect = gen
    return provider


# ----------- verify_keyword ----------


def test_verify_keyword_general_query_keeps():
    body = json.dumps({
        "candidate_nouns": [],
        "per_noun": [],
        "entity_status": "general",
        "verdict": "keep",
        "reason": "pure category query",
    })
    provider = _llm_returning([body])
    res = verify_keyword(provider, "gemini-3.1-pro-preview", "best dps build nte")
    assert res.verdict == "keep"
    assert res.entity_status == "general"
    assert res.fabricated_entities == []


def test_verify_keyword_real_entity_keeps():
    body = json.dumps({
        "candidate_nouns": ["nanally"],
        "per_noun": [
            {"noun": "nanally", "verdict": "real",
             "evidence": "prydwen.gg lists Nanally as Anima fist DPS"},
        ],
        "entity_status": "real",
        "verdict": "keep",
        "reason": "Nanally is a verified NTE character",
    })
    provider = _llm_returning([body])
    res = verify_keyword(provider, "gemini-3.1-pro-preview", "nanally build nte")
    assert res.verdict == "keep"
    assert res.entity_status == "real"


def test_verify_keyword_fabricated_archives():
    body = json.dumps({
        "candidate_nouns": ["minerva"],
        "per_noun": [
            {"noun": "minerva", "verdict": "fabricated",
             "evidence": "no NTE results for character named Minerva"},
        ],
        "entity_status": "fabricated",
        "verdict": "archive",
        "reason": "Minerva does not exist in NTE",
    })
    provider = _llm_returning([body])
    res = verify_keyword(provider, "gemini-3.1-pro-preview", "minerva best build nte")
    assert res.verdict == "archive"
    assert res.entity_status == "fabricated"
    assert res.fabricated_entities == ["minerva"]


def test_verify_keyword_mixed_fabricated_archives():
    """Any fabricated noun → archive even if others are real."""
    body = json.dumps({
        "candidate_nouns": ["nanally", "frost guardian"],
        "per_noun": [
            {"noun": "nanally", "verdict": "real"},
            {"noun": "frost guardian", "verdict": "fabricated"},
        ],
        "entity_status": "mixed",
        "verdict": "archive",
        "reason": "frost guardian doesn't exist",
    })
    provider = _llm_returning([body])
    res = verify_keyword(
        provider, "gemini-3.1-pro-preview", "nanally vs frost guardian nte",
    )
    assert res.verdict == "archive"
    assert "frost guardian" in res.fabricated_entities


def test_verify_keyword_llm_error_fails_open():
    """If the LLM call itself raises, we keep the keyword (fail open).
    Better to admit one unverified keyword than block the whole gardener
    run on a transient provider error."""
    provider = MagicMock()
    provider.generate.side_effect = RuntimeError("provider down")
    res = verify_keyword(provider, "gemini-3.1-pro-preview", "nanally build nte")
    assert res.verdict == "keep"
    assert res.entity_status == "error"


def test_verify_keyword_bad_json_fails_open():
    provider = _llm_returning(["not even close to JSON"])
    res = verify_keyword(provider, "gemini-3.1-pro-preview", "nanally build nte")
    assert res.verdict == "keep"
    assert res.entity_status == "error"


def test_verify_keyword_weird_verdict_normalized_to_keep():
    body = json.dumps({
        "candidate_nouns": ["x"],
        "per_noun": [{"noun": "x", "verdict": "maybe"}],
        "entity_status": "unknown",
        "verdict": "????",
    })
    provider = _llm_returning([body])
    res = verify_keyword(provider, "gemini-3.1-pro-preview", "x test nte")
    assert res.verdict == "keep"   # weird verdicts fail open


# ----------- _needs_entity_verify ----------


def test_needs_entity_verify_pure_category_skipped():
    from scripts.keyword_gardener import _needs_entity_verify
    assert _needs_entity_verify("best dps build nte") is False
    assert _needs_entity_verify("nte tier list") is False
    assert _needs_entity_verify("how to reroll nte") is False
    assert _needs_entity_verify("nte beginner guide") is False


def test_needs_entity_verify_has_specific_token_triggers():
    from scripts.keyword_gardener import _needs_entity_verify
    assert _needs_entity_verify("nanally build nte") is True
    assert _needs_entity_verify("minerva best build nte") is True
    assert _needs_entity_verify("how to beat frost guardian nte") is True
    assert _needs_entity_verify("zerda character guide nte") is True


# ----------- _gate_keywords_by_entity_verify ----------


def test_gate_skips_llm_for_pure_category_queries():
    """A general-category query like 'best dps build nte' should NOT
    trigger an LLM call — saves $0.005 per such row."""
    from scripts.keyword_gardener import _gate_keywords_by_entity_verify
    provider = MagicMock()
    provider.generate.side_effect = AssertionError(
        "should not be called for general category queries"
    )
    items = [{"keyword": "best dps build nte"},
             {"keyword": "nte tier list"}]
    with patch(
        "scripts.keyword_gardener._log_verify_rejection"
    ):
        kept, cost, rej = _gate_keywords_by_entity_verify(
            provider, "model", items, SITE_ID, budget_usd=1.0,
        )
    assert len(kept) == 2
    assert cost == 0.0
    assert rej == 0


def test_gate_drops_fabricated_and_alerts():
    from scripts.keyword_gardener import _gate_keywords_by_entity_verify
    body_real = json.dumps({
        "candidate_nouns": ["nanally"],
        "per_noun": [{"noun": "nanally", "verdict": "real"}],
        "entity_status": "real", "verdict": "keep", "reason": "ok",
    })
    body_fab = json.dumps({
        "candidate_nouns": ["minerva"],
        "per_noun": [{"noun": "minerva", "verdict": "fabricated"}],
        "entity_status": "fabricated", "verdict": "archive",
        "reason": "no NTE results for Minerva",
    })
    provider = _llm_returning([body_real, body_fab])
    items = [{"keyword": "nanally build nte"},
             {"keyword": "minerva best build nte"}]
    with patch(
        "scripts.keyword_gardener._log_verify_rejection"
    ) as log_reject:
        kept, cost, rej = _gate_keywords_by_entity_verify(
            provider, "model", items, SITE_ID, budget_usd=1.0,
        )
    assert len(kept) == 1
    assert kept[0]["keyword"] == "nanally build nte"
    assert rej == 1
    assert cost > 0
    log_reject.assert_called_once()
    args, _ = log_reject.call_args
    assert args[0] == SITE_ID
    assert args[1] == "minerva best build nte"
    assert "minerva" in args[3]   # fabricated_entities


def test_gate_budget_cap_passes_remaining_through_unverified():
    """If the verify budget runs out mid-batch, remaining items flow
    through unverified rather than blocking the gardener entirely."""
    from scripts.keyword_gardener import _gate_keywords_by_entity_verify
    expensive_body = json.dumps({
        "candidate_nouns": ["foo"],
        "per_noun": [{"noun": "foo", "verdict": "real"}],
        "entity_status": "real", "verdict": "keep",
    })

    # Each generate call returns cost 0.30 — second call blows the cap
    @dataclass
    class HighCostResp:
        text: str
        tokens_in: int = 1
        tokens_out: int = 1
        cost_usd: float = 0.30
        duration_ms: int = 1
        model: str = "x"
        grounding_sources: list = None

        def __post_init__(self):
            if self.grounding_sources is None:
                self.grounding_sources = []

    provider = MagicMock()
    provider.generate.side_effect = [
        HighCostResp(text=expensive_body),
        HighCostResp(text=expensive_body),
        HighCostResp(text=expensive_body),
    ]
    items = [
        {"keyword": "nanally build nte"},
        {"keyword": "kira build nte"},
        {"keyword": "alice build nte"},
    ]
    with patch("scripts.keyword_gardener._log_verify_rejection"):
        kept, cost, rej = _gate_keywords_by_entity_verify(
            provider, "model", items, SITE_ID, budget_usd=0.40,
        )
    # First item verified (cost 0.30, still under 0.40) and kept.
    # Second item: cost was 0.30 + would exceed cap after, so we should
    # let it through unverified along with the rest.
    assert len(kept) == 3
    assert rej == 0
    # Cost should reflect what we actually spent on verified items
    assert cost > 0


def test_log_verify_rejection_swallows_db_errors():
    from scripts.keyword_gardener import _log_verify_rejection
    cur = MagicMock()
    cur.execute.side_effect = Exception("alerts table missing")
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = False
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    with patch("scripts.keyword_gardener.get_db_connection", return_value=ctx):
        # Must NOT raise
        _log_verify_rejection(SITE_ID, "foo", "reason", ["fab1"])
