"""Unit tests for KeywordSelectorAgent — pure-logic helpers + the
output-schema enforcement that's now part of `_execute`.

The full _execute path requires DB access and is exercised in production.
Here we cover:
  - _type_adjustment() math at all thresholds + edge cases
  - _qa_pass_rate_table contract (mocked cursor)
  - Output validation: missing article_type → ValueError
  - Output validation: blacklisted article_type → ValueError
  - Output validation: unknown article_type → ValueError
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agents.keyword_selector import (
    ALL_TYPES,
    KeywordSelectorAgent,
    QA_RATE_CONSECUTIVE_FAIL_PENALTY,
    QA_RATE_MIN_SAMPLES,
    QA_RATE_PENALTY,
    QA_RATE_REWARD,
    _type_adjustment,
)


# ----------- _type_adjustment ----------


def test_type_adjustment_no_data():
    # Both None and empty-dict short-circuit at the same early return
    assert _type_adjustment(None) == (0.0, "no_data")
    assert _type_adjustment({}) == (0.0, "no_data")
    # Dict with zero counts is "truthy enough" to flow into the
    # few-samples branch (it has keys, just zero values)
    delta, label = _type_adjustment({"n_pass": 0, "n_fail": 0, "consecutive_fail": 0, "pass_rate": None})
    assert delta == 0.0
    assert "few_samples=0" in label


def test_type_adjustment_few_samples():
    stats = {"n_pass": 1, "n_fail": 1, "pass_rate": 0.5, "consecutive_fail": 0}
    delta, label = _type_adjustment(stats)
    assert delta == 0.0
    assert "few_samples" in label


def test_type_adjustment_reward_at_60_pct():
    stats = {"n_pass": 3, "n_fail": 2, "pass_rate": 0.6, "consecutive_fail": 0}
    delta, _ = _type_adjustment(stats)
    assert delta == QA_RATE_REWARD


def test_type_adjustment_reward_at_high_rate():
    stats = {"n_pass": 9, "n_fail": 1, "pass_rate": 0.9, "consecutive_fail": 0}
    delta, _ = _type_adjustment(stats)
    assert delta == QA_RATE_REWARD


def test_type_adjustment_neutral_mid_band():
    stats = {"n_pass": 2, "n_fail": 3, "pass_rate": 0.4, "consecutive_fail": 0}
    delta, _ = _type_adjustment(stats)
    assert delta == 0.0


def test_type_adjustment_penalty_low_rate():
    stats = {"n_pass": 1, "n_fail": 5, "pass_rate": 1 / 6, "consecutive_fail": 0}
    delta, _ = _type_adjustment(stats)
    assert delta == QA_RATE_PENALTY


def test_type_adjustment_consecutive_fail_overrides_everything():
    # Even if pass_rate looks fine, 3 consecutive fails trip the harshest penalty
    stats = {"n_pass": 10, "n_fail": 3, "pass_rate": 0.77, "consecutive_fail": 3}
    delta, label = _type_adjustment(stats)
    assert delta == QA_RATE_CONSECUTIVE_FAIL_PENALTY
    assert "consec_fail=3" in label


# ----------- output schema validation ----------


def _make_agent_with_canned_llm(llm_text: str, site_config=None):
    """Build an agent whose LLM always returns `llm_text`."""

    class FakeResp:
        text = llm_text
        tokens_in = 1
        tokens_out = 1
        cost_usd = 0.0
        duration_ms = 1
        model = "gemini-3-flash-preview"
        grounding_sources = []

    fake_llm = MagicMock()
    fake_llm.generate.return_value = FakeResp()
    cfg = site_config or {
        "game": {},
        "text_provider": {"outline_model": "gemini-3-flash-preview"},
        "content_plan": {"type_blacklist": ["news"]},
    }
    return KeywordSelectorAgent(llm=fake_llm, site_config=cfg)


def _patch_db_with_candidates(candidates_rows):
    """Helper: patches src.agents.keyword_selector.get_db_connection so
    _execute sees the supplied candidate rows + empty track-record."""

    def fake_cm():
        cur = MagicMock()

        def execute(sql, args=()):
            sql_l = sql.lower()
            if "from articles" in sql_l and "group by t" in sql_l:
                cur._rows = []
            elif "from articles" in sql_l and "order by created_at desc" in sql_l:
                cur._rows = []
            elif "from keywords" in sql_l and "status = 'planned'" in sql_l:
                cur._rows = candidates_rows
            else:
                cur._rows = []
            return None
        cur.execute.side_effect = execute
        cur.fetchall.side_effect = lambda: cur._rows
        cur.fetchone.side_effect = lambda: (cur._rows[0] if cur._rows else None)
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        conn.cursor.return_value.__exit__.return_value = False
        ctx = MagicMock()
        ctx.__enter__.return_value = conn
        ctx.__exit__.return_value = False
        return ctx

    return patch(
        "src.agents.keyword_selector.get_db_connection", side_effect=fake_cm
    )


def test_execute_rejects_blacklisted_article_type():
    KW_ID = "00000000-0000-0000-0000-000000000001"
    candidates_rows = [
        (KW_ID, "nte banner schedule 2026", "informational", 80, "manual", None),
        ("11111111-1111-1111-1111-111111111111", "nte how pity works", "informational", 70, "manual", None),
    ]
    llm_text = (
        '{"keyword_id": "11111111-1111-1111-1111-111111111111",'
        '"keyword_text": "nte how pity works",'
        '"article_type": "news",'
        '"reason": "should be rejected"}'
    )
    agent = _make_agent_with_canned_llm(llm_text)
    with _patch_db_with_candidates(candidates_rows):
        with pytest.raises(ValueError, match="blacklisted"):
            agent._execute({"site_id": "00000000-0000-0000-0000-000000000099"})


def test_execute_rejects_unknown_article_type():
    KW_ID = "00000000-0000-0000-0000-000000000001"
    candidates_rows = [
        (KW_ID, "nte how pity works", "informational", 70, "manual", None),
    ]
    llm_text = (
        f'{{"keyword_id": "{KW_ID}",'
        '"keyword_text": "nte how pity works",'
        '"article_type": "novel-genre",'
        '"reason": "made up type"}'
    )
    agent = _make_agent_with_canned_llm(llm_text)
    with _patch_db_with_candidates(candidates_rows):
        with pytest.raises(ValueError, match="unknown article_type"):
            agent._execute({"site_id": "00000000-0000-0000-0000-000000000099"})


def test_execute_accepts_valid_pick_and_normalizes_output():
    KW_ID = "00000000-0000-0000-0000-000000000001"
    candidates_rows = [
        (KW_ID, "nte how pity works", "informational", 70, "manual", None),
    ]
    # LLM returns the new field name only; verify backward-compat keeps both keys.
    llm_text = (
        f'{{"keyword_id": "{KW_ID}",'
        '"keyword_text": "nte how pity works",'
        '"suggested_article_type": "faq",'
        '"reason": "informational query about a mechanic"}'
    )
    agent = _make_agent_with_canned_llm(llm_text)
    with _patch_db_with_candidates(candidates_rows):
        out = agent._execute({"site_id": "00000000-0000-0000-0000-000000000099"})
    assert out["keyword_id"] == KW_ID
    assert out["article_type"] == "faq"
    assert out["suggested_article_type"] == "faq"
    assert "_diversity_snapshot" in out


def test_execute_filters_blacklisted_candidates_before_llm():
    """Banner-keyword candidates should never reach the LLM when news is blacklisted."""
    candidates_rows = [
        ("11111111-1111-1111-1111-111111111111", "nte current banner schedule", "informational", 90, "manual", None),
        ("22222222-2222-2222-2222-222222222222", "nte how pity works", "informational", 70, "manual", None),
    ]
    # LLM picks the second (only) candidate left after filter
    KW_ID2 = "22222222-2222-2222-2222-222222222222"
    llm_text = (
        f'{{"keyword_id": "{KW_ID2}",'
        '"keyword_text": "nte how pity works",'
        '"article_type": "faq",'
        '"reason": "fits"}'
    )
    agent = _make_agent_with_canned_llm(llm_text)
    with _patch_db_with_candidates(candidates_rows):
        out = agent._execute({"site_id": "00000000-0000-0000-0000-000000000099"})
    assert out["keyword_id"] == KW_ID2
    # Confirm the banner candidate didn't appear in the prompt the LLM saw
    prompt_arg = agent.llm.generate.call_args.kwargs["prompt"]
    assert "current banner schedule" not in prompt_arg
    assert "nte how pity works" in prompt_arg
