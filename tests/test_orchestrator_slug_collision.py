"""Unit tests for the slug-collision retry helpers in the orchestrator.

We test the helpers directly (`_create_article`, `_set_article`,
`_candidate_slug`) with a mocked DB cursor that simulates
UniqueViolation on the first N attempts. This keeps the tests fast and
DB-free.

What's covered:
  - _candidate_slug suffixing strategy (attempts 0..3)
  - _create_article succeeds on first attempt when no collision
  - _create_article retries through date / random / random+random and
    returns the slug that actually stuck
  - _create_article gives up after SLUG_COLLISION_MAX_RETRIES + 1
  - _set_article retries the same way when slug is in fields
  - _set_article does NOT retry when slug is absent (passes other
    UniqueViolations straight through)
  - _record_slug_rename swallows logging-side failures
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import UUID

import psycopg
import pytest

from src.pipeline.orchestrator import (
    SLUG_COLLISION_MAX_RETRIES,
    _candidate_slug,
    _create_article,
    _record_slug_rename,
    _set_article,
)


SITE_ID = UUID("11111111-1111-1111-1111-111111111111")


# ----------- _candidate_slug ----------


def test_candidate_slug_attempt_zero_passthrough():
    assert _candidate_slug("foo-bar", 0) == "foo-bar"


def test_candidate_slug_attempt_one_has_date():
    out = _candidate_slug("foo-bar", 1)
    # date suffix yyyymmdd is 8 digits
    assert out.startswith("foo-bar-")
    tail = out.split("foo-bar-", 1)[1]
    assert len(tail) == 8 and tail.isdigit()


def test_candidate_slug_attempt_two_has_date_and_random():
    out = _candidate_slug("foo-bar", 2)
    parts = out.split("-")
    # foo-bar-YYYYMMDD-xxxx → 4 parts
    assert parts[:2] == ["foo", "bar"]
    assert len(parts[2]) == 8 and parts[2].isdigit()
    assert len(parts[3]) == 4


def test_candidate_slug_attempt_three_has_two_random_segments():
    out = _candidate_slug("foo-bar", 3)
    parts = out.split("-")
    # foo-bar-YYYYMMDD-xxxx-yyyy
    assert len(parts) == 5
    assert len(parts[2]) == 8 and parts[2].isdigit()
    assert len(parts[3]) == 4 and len(parts[4]) == 4


def test_candidate_slug_clamps_length():
    long_base = "x" * 100
    out = _candidate_slug(long_base, 2)
    assert len(out) <= 80


# ----------- _create_article retry ----------


class _FakeCursor:
    """A cursor that raises UniqueViolation on the first `fail_n` calls
    to execute(...) and succeeds on attempt fail_n+1."""

    def __init__(self, fail_n: int = 0):
        self.fail_n = fail_n
        self.calls: list[tuple] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if len(self.calls) <= self.fail_n:
            raise psycopg.errors.UniqueViolation(
                'duplicate key value violates unique constraint "articles_site_id_slug_key"'
            )
        return None

    def fetchone(self):
        return None

    def fetchall(self):
        return []


def _patch_db(fail_n: int):
    """Patch get_db_connection to yield a context-manager whose cursor
    raises UniqueViolation `fail_n` times before succeeding.

    Each `with get_db_connection() as conn` reuses the SAME cursor so
    call counts persist across the retry loop.
    """
    cur = _FakeCursor(fail_n=fail_n)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = False
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    p = patch(
        "src.pipeline.orchestrator.get_db_connection", return_value=ctx
    )
    return p, cur


def test_create_article_first_attempt_succeeds():
    p, cur = _patch_db(fail_n=0)
    with p, patch("src.pipeline.orchestrator._record_slug_rename") as rec:
        aid, slug = _create_article(SITE_ID, "foo-bar", "Foo Bar", "build")
    assert slug == "foo-bar"
    assert len(cur.calls) == 1
    rec.assert_not_called()


def test_create_article_one_collision_then_succeeds():
    p, cur = _patch_db(fail_n=1)
    with p, patch("src.pipeline.orchestrator._record_slug_rename") as rec:
        aid, slug = _create_article(SITE_ID, "foo-bar", "Foo Bar", "build")
    assert slug.startswith("foo-bar-")
    # attempt 0 (raw) raised; attempt 1 (date) succeeded
    assert len(cur.calls) == 2
    rec.assert_called_once()
    args, _kwargs = rec.call_args
    assert args[2] == "foo-bar"      # intended
    assert args[3] == slug            # final
    assert args[4] == 1               # attempts


def test_create_article_two_collisions_then_succeeds():
    p, cur = _patch_db(fail_n=2)
    with p, patch("src.pipeline.orchestrator._record_slug_rename") as rec:
        aid, slug = _create_article(SITE_ID, "foo-bar", "Foo Bar", "build")
    parts = slug.split("-")
    # foo-bar-YYYYMMDD-xxxx
    assert len(parts) == 4
    assert len(parts[3]) == 4
    assert len(cur.calls) == 3
    rec.assert_called_once_with(SITE_ID, aid, "foo-bar", slug, 2)


def test_create_article_exhausts_retries_and_raises():
    p, cur = _patch_db(fail_n=SLUG_COLLISION_MAX_RETRIES + 5)  # forever fails
    with p:
        with pytest.raises(RuntimeError, match="slug collision retries exhausted"):
            _create_article(SITE_ID, "foo-bar", "Foo Bar", "build")
    # Should have tried exactly MAX_RETRIES + 1 times (attempts 0..MAX)
    assert len(cur.calls) == SLUG_COLLISION_MAX_RETRIES + 1


# ----------- _set_article retry ----------


def test_set_article_retries_on_slug_field():
    p, cur = _patch_db(fail_n=1)
    with p, patch("src.pipeline.orchestrator._record_slug_rename"):
        result = _set_article(
            UUID("22222222-2222-2222-2222-222222222222"),
            slug="some-slug",
            title="x",
        )
    assert result["final_slug"].startswith("some-slug-")
    # First UPDATE attempt raised, second succeeded, third call is the
    # site_id lookup for the rename log
    assert len(cur.calls) >= 2


def test_set_article_no_retry_when_slug_absent():
    """If slug isn't being set, a UniqueViolation should propagate
    immediately — it isn't our retry case."""
    p, cur = _patch_db(fail_n=1)
    with p:
        with pytest.raises(psycopg.errors.UniqueViolation):
            _set_article(
                UUID("22222222-2222-2222-2222-222222222222"),
                title="not-the-slug",
            )


def test_set_article_no_fields_is_noop():
    p, cur = _patch_db(fail_n=0)
    with p:
        result = _set_article(UUID("22222222-2222-2222-2222-222222222222"))
    assert result == {"final_slug": None}
    assert len(cur.calls) == 0


# ----------- _record_slug_rename swallows errors ----------


def test_record_slug_rename_swallows_logging_failures():
    cur = MagicMock()
    cur.execute.side_effect = Exception("alerts table missing or RLS")
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = False
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    with patch("src.pipeline.orchestrator.get_db_connection", return_value=ctx):
        # Must NOT raise even though the cursor execute does
        _record_slug_rename(SITE_ID, UUID("33333333-3333-3333-3333-333333333333"),
                            "intended", "final", 1)
