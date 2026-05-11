"""Unit tests for RewriterAgent — exercise the prompt-building + output
shaping logic with a mocked LLM. Pure-Python, no DB / API calls.

Covers the pieces of agent logic that don't need a live database:
  - _word_count / _h2_count helpers
  - _primary_query_for title cleanup
  - get_model override (uses qa_model when rewriting_model absent)
  - target word/H2 math
  - end-to-end _execute happy path with a mocked LLM
  - failure path: empty rewrite output
  - failure path: no H2 headings in rewrite
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from src.agents.rewriter import (
    RewriterAgent,
    _h2_count,
    _primary_query_for,
    _word_count,
)


# --------------------------------------------------------------- helpers


@dataclass
class FakeLLMResponse:
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


class FakeLLM:
    """Drop-in replacement for BaseLLMProvider — returns whatever the
    queued list says, in order."""

    def __init__(self, responses: list[str]):
        self._queue = list(responses)
        self.calls: list[dict] = []

    def generate(self, prompt: str, model: str, **kwargs) -> FakeLLMResponse:
        self.calls.append({"prompt": prompt, "model": model, **kwargs})
        if not self._queue:
            raise RuntimeError("FakeLLM out of queued responses")
        return FakeLLMResponse(text=self._queue.pop(0), model=model)


# ----------- standalone helpers ----------


def test_word_count_excludes_sources_section():
    md = (
        "One two three four five.\n\n"
        "## Body\nsix seven eight.\n\n"
        "## Sources\n- ten\n- eleven\n"
    )
    # body words: one two three four five Body six seven eight = 9
    assert _word_count(md) == 9


def test_h2_count_skips_inline_hashes_inside_text():
    md = (
        "## First\n"
        "Talking about ##notahead in a sentence.\n"
        "## Second\n"
    )
    assert _h2_count(md) == 2


def test_h2_count_zero_when_only_h1_or_h3():
    md = "# Title\n### sub\n\nbody\n"
    assert _h2_count(md) == 0


def test_primary_query_strips_brand_suffix():
    assert _primary_query_for({"title": "Nanally Guide | NTE Codex"}) == "Nanally Guide"
    assert _primary_query_for({"title": "Nanally Build — NTE"}) == "Nanally Build"
    # No suffix: returned as-is
    assert _primary_query_for({"title": "Reroll Strategy"}) == "Reroll Strategy"
    # Falls back to slug if title missing
    assert _primary_query_for({"slug": "some-slug"}) == "some-slug"


# ----------- model selection ----------


def test_get_model_prefers_rewriting_model_when_set():
    agent = RewriterAgent(
        llm=FakeLLM([]),
        site_config={
            "text_provider": {
                "rewriting_model": "gemini-3.1-pro-preview",
                "qa_model": "gemini-3.1-pro-preview",
            },
        },
    )
    assert agent.get_model() == "gemini-3.1-pro-preview"


def test_get_model_falls_back_to_qa_model():
    agent = RewriterAgent(
        llm=FakeLLM([]),
        site_config={"text_provider": {"qa_model": "gemini-3.1-pro-preview"}},
    )
    assert agent.get_model() == "gemini-3.1-pro-preview"


def test_get_model_raises_when_neither_present():
    agent = RewriterAgent(llm=FakeLLM([]), site_config={"text_provider": {}})
    with pytest.raises(KeyError):
        agent.get_model()


# ----------- _execute happy + failure paths ----------


_ARTICLE_ROW = {
    "slug": "nanally-guide-nte",
    "title": "Nanally Guide NTE: Best Build, Skills, and Teams",
    "article_type": "character_db",
    "content_md": (
        "Intro paragraph.\n\n"
        "# Nanally Guide NTE\n\n"
        "## Skills\nfoo bar baz.\n\n"
        "## Build\nweapon stuff.\n\n"
        "## Sources\n- url1\n"
    ),
    "outline": {"sections": [{"h2": "Skills"}, {"h2": "Build"}]},
    "word_count": 8,
    "qa_score": 8.3,
    "qa_feedback": {},
    "primary_keyword": "nanally guide",
}


def _mock_db_row():
    """Return a context-manager + cursor mock that yields _ARTICLE_ROW."""
    cur = MagicMock()
    # description columns must match keys we read
    cols = MagicMock(name="d")
    cur.description = [
        MagicMock(name=k) for k in [
            "slug", "title", "article_type", "content_md", "outline",
            "word_count", "qa_score", "qa_feedback", "primary_keyword",
        ]
    ]
    for col, key in zip(cur.description, [
        "slug", "title", "article_type", "content_md", "outline",
        "word_count", "qa_score", "qa_feedback", "primary_keyword",
    ]):
        col.name = key
    cur.fetchone.return_value = tuple(
        _ARTICLE_ROW[k] for k in [
            "slug", "title", "article_type", "content_md", "outline",
            "word_count", "qa_score", "qa_feedback", "primary_keyword",
        ]
    )
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = False
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    return cm


_ANALYSIS_JSON = json.dumps({
    "missing_sections": [
        {"h2_title": "Team Synergy", "why": "competitors all cover this",
         "covered_by": ["site1.com"]}
    ],
    "shallow_sections": [
        {"existing_h2": "Build", "gap": "no weapon table",
         "competitor_url": "site2.com"}
    ],
    "stale_info": [],
    "competitor_urls": ["site1.com", "site2.com"],
})


_REWRITE_MD = (
    "Hook line here, intro paragraph two.\n\n"
    "# Nanally Guide NTE\n\n"
    "## Skills\nfoo bar baz extended with detail one two three four five six.\n\n"
    "## Build\nnow with a weapon table and concrete numbers and more words for length.\n\n"
    "## Team Synergy\nnew section about synergy with concrete examples and ample words filling.\n\n"
    "## Sources\n- url1\n- url2\n- url3\n"
)


@patch("src.agents.rewriter.get_db_connection")
def test_execute_happy_path(mock_get_db_connection):
    mock_get_db_connection.return_value = _mock_db_row()

    fake_llm = FakeLLM([_ANALYSIS_JSON, _REWRITE_MD])
    site_cfg = {
        "game": {"name": "Neverness to Everness", "abbreviation": "NTE",
                 "release_date": "2026-04-29"},
        "text_provider": {"qa_model": "gemini-3.1-pro-preview"},
        "content_plan": {"max_word_count": 4000, "min_word_count": 1200},
        "qa_thresholds": {"min_quality_score": 7.0},
    }
    agent = RewriterAgent(llm=fake_llm, site_config=site_cfg)

    out = agent._execute({
        "article_id": "00000000-0000-0000-0000-000000000001",
        "gsc_stats": {"position": 18.5, "impressions": 120, "ctr": 0.012},
        "old_qa_score": 8.3,
    })

    assert out["slug"] == "nanally-guide-nte"
    assert out["primary_query"] == "nanally guide"
    # _h2_count counts every ##-prefixed line including Sources.
    # Old: Skills + Build + Sources = 3; new: Skills + Build + Team Synergy + Sources = 4
    assert out["new_h2_count"] == 4
    assert out["old_h2_count"] == 3
    # Targets: 1.5x = 12 words, capped by max 4000, h2 must be ≥ 4
    assert out["targets"]["min_words"] == 12
    assert out["targets"]["h2_count"] >= 4
    assert "Team Synergy" in out["new_content_md"]
    # LLM was called exactly twice
    assert len(fake_llm.calls) == 2
    # Second call is the rewrite; we pass enable_search=True
    assert fake_llm.calls[0].get("enable_search") is True
    assert fake_llm.calls[1].get("enable_search") is True


@patch("src.agents.rewriter.get_db_connection")
def test_execute_fails_on_empty_rewrite(mock_get_db_connection):
    mock_get_db_connection.return_value = _mock_db_row()
    fake_llm = FakeLLM([_ANALYSIS_JSON, "   "])      # whitespace-only rewrite
    agent = RewriterAgent(
        llm=fake_llm,
        site_config={
            "game": {}, "text_provider": {"qa_model": "x"},
            "content_plan": {"max_word_count": 4000},
        },
    )
    with pytest.raises(RuntimeError, match="empty markdown"):
        agent._execute({
            "article_id": "00000000-0000-0000-0000-000000000001",
            "gsc_stats": {},
            "old_qa_score": 8.0,
        })


@patch("src.agents.rewriter.get_db_connection")
def test_execute_fails_when_rewrite_has_no_h2(mock_get_db_connection):
    mock_get_db_connection.return_value = _mock_db_row()
    no_h2 = "Lots of prose without any heading structure to speak of."
    fake_llm = FakeLLM([_ANALYSIS_JSON, no_h2])
    agent = RewriterAgent(
        llm=fake_llm,
        site_config={
            "game": {}, "text_provider": {"qa_model": "x"},
            "content_plan": {"max_word_count": 4000},
        },
    )
    with pytest.raises(RuntimeError, match="no H2"):
        agent._execute({
            "article_id": "00000000-0000-0000-0000-000000000001",
            "gsc_stats": {},
            "old_qa_score": 8.0,
        })


@patch("src.agents.rewriter.get_db_connection")
def test_execute_strips_leading_code_fence(mock_get_db_connection):
    mock_get_db_connection.return_value = _mock_db_row()
    fenced = "```markdown\n" + _REWRITE_MD + "\n```"
    fake_llm = FakeLLM([_ANALYSIS_JSON, fenced])
    agent = RewriterAgent(
        llm=fake_llm,
        site_config={
            "game": {}, "text_provider": {"qa_model": "x"},
            "content_plan": {"max_word_count": 4000},
        },
    )
    out = agent._execute({
        "article_id": "00000000-0000-0000-0000-000000000001",
        "gsc_stats": {},
        "old_qa_score": 8.0,
    })
    assert not out["new_content_md"].startswith("```")
    assert "Team Synergy" in out["new_content_md"]
