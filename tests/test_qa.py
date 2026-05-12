"""Unit tests for QAAgent — focus on the honesty-placeholder filter
added 2026-05-11 (P0 二次修复 follow-up Task Z).

Mocks the LLM to return canned QA verdicts and exercises:
  - _is_honesty_placeholder regex (positive + negative cases)
  - _execute strips the placeholder from fabricated_terms post-hoc
  - articles with ONLY the honesty placeholder are NOT failed by the
    hard "any fabrication = fail" rule
  - articles with real fabrications + a placeholder still fail, but
    only on the real fabrications
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from src.agents.qa import QAAgent, _is_honesty_placeholder


# ----------- _is_honesty_placeholder ----------


def test_placeholder_canonical_form():
    assert _is_honesty_placeholder(
        "[Information not yet publicly available as of 2026-05-11]"
    )


def test_placeholder_case_insensitive():
    assert _is_honesty_placeholder(
        "[information NOT yet PUBLICLY available as of 2026-05-11]"
    )


def test_placeholder_without_brackets():
    assert _is_honesty_placeholder(
        "Information not yet publicly available as of 2026-05-11"
    )


def test_placeholder_without_date_suffix():
    assert _is_honesty_placeholder("Information not yet publicly available")


def test_placeholder_with_template_date():
    assert _is_honesty_placeholder(
        "[Information not yet publicly available as of YYYY-MM-DD]"
    )


def test_placeholder_embedded_in_phrase():
    """LLM sometimes quotes a fragment of the article containing the
    placeholder; the regex should still hit."""
    assert _is_honesty_placeholder(
        "use Sakura [Information not yet publicly available]"
    )


def test_real_fabrication_not_match():
    assert not _is_honesty_placeholder("Echo of Hethereau")
    assert not _is_honesty_placeholder("Frost Guardian")
    assert not _is_honesty_placeholder("Urban Vanguard")
    assert not _is_honesty_placeholder("Standard Resonance")


def test_empty_or_none():
    assert not _is_honesty_placeholder("")
    assert not _is_honesty_placeholder(None)
    assert not _is_honesty_placeholder(0)
    assert not _is_honesty_placeholder([])


# ----------- _execute filter behavior ----------


_SITE_CONFIG = {
    "game": {
        "name": "Neverness to Everness",
        "abbreviation": "NTE",
        "release_date": "2026-04-29",
    },
    "text_provider": {"qa_model": "gemini-3.1-pro-preview"},
    "qa_thresholds": {"min_quality_score": 7.0},
}


@dataclass
class FakeResp:
    text: str
    tokens_in: int = 100
    tokens_out: int = 100
    cost_usd: float = 0.001
    duration_ms: int = 500
    model: str = "gemini-3.1-pro-preview"
    grounding_sources: list = None

    def __post_init__(self):
        if self.grounding_sources is None:
            self.grounding_sources = []


def _qa_with_response(text: str) -> QAAgent:
    fake_llm = MagicMock()
    fake_llm.generate.return_value = FakeResp(text=text)
    return QAAgent(llm=fake_llm, site_config=_SITE_CONFIG)


def _base_input():
    return {
        "keyword": "test",
        "article_type": "build",
        "content_md": "## body",
        "outline": {},
        "word_count": 1000,
        "min_word_count": 800,
        "max_word_count": 2000,
    }


def test_execute_strips_honesty_placeholder_and_passes():
    """An article whose only `fabricated_terms` entry is the honesty
    placeholder should NOT fail the hard rule. The placeholder is
    moved to `_honesty_placeholder_stripped` for audit."""
    qa_json = json.dumps({
        "score_raw_12": 9.0,
        "score": 7.5,
        "passed": False,           # writer claimed False; we recompute
        "feedback": {
            "intent_match": 2,
            "info_density": 1,
            "structure": 2,
            "ai_pattern": 1,
            "seo": 1,
            "factual_accuracy": 2,
            "fabricated_terms": [
                "[Information not yet publicly available as of 2026-05-11]"
            ],
            "verified_terms": ["Nanally", "Hotori"],
            "issues": [],
            "suggestions": [],
        },
    })
    agent = _qa_with_response(qa_json)
    out = agent._execute(_base_input())

    assert out["feedback"]["fabricated_terms"] == []
    assert out["feedback"]["_honesty_placeholder_stripped"] == [
        "[Information not yet publicly available as of 2026-05-11]"
    ]
    # Score 7.5 ≥ threshold 7.0 AND fabricated_terms now empty → passed
    assert out["passed"] is True
    assert out["score"] == 7.5


def test_execute_real_fabrication_still_fails():
    """A real fabricated term should still trip the hard rule even
    when the placeholder is also present."""
    qa_json = json.dumps({
        "score_raw_12": 9.0,
        "score": 7.5,
        "passed": False,
        "feedback": {
            "intent_match": 2,
            "info_density": 1,
            "structure": 2,
            "ai_pattern": 1,
            "seo": 1,
            "factual_accuracy": 2,
            "fabricated_terms": [
                "[Information not yet publicly available as of 2026-05-11]",
                "Frost Guardian",
            ],
            "verified_terms": [],
            "issues": [],
            "suggestions": [],
        },
    })
    agent = _qa_with_response(qa_json)
    out = agent._execute(_base_input())

    assert out["feedback"]["fabricated_terms"] == ["Frost Guardian"]
    assert out["feedback"]["_honesty_placeholder_stripped"] == [
        "[Information not yet publicly available as of 2026-05-11]"
    ]
    assert out["passed"] is False


def test_execute_no_placeholder_does_not_add_audit_key():
    """When the LLM didn't flag any placeholders, we shouldn't write
    `_honesty_placeholder_stripped` to feedback."""
    qa_json = json.dumps({
        "score_raw_12": 9.0,
        "score": 7.5,
        "passed": True,
        "feedback": {
            "intent_match": 2,
            "info_density": 1,
            "structure": 2,
            "ai_pattern": 1,
            "seo": 1,
            "factual_accuracy": 2,
            "fabricated_terms": [],
            "verified_terms": ["Nanally"],
            "issues": [],
            "suggestions": [],
        },
    })
    agent = _qa_with_response(qa_json)
    out = agent._execute(_base_input())
    assert "_honesty_placeholder_stripped" not in out["feedback"]
    assert out["passed"] is True
