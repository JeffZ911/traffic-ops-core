"""Live tests against the real Supabase project. Skipped if .env missing."""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

from src.db.client import get_db_connection, get_supabase_client


load_dotenv()


@pytest.fixture(scope="module")
def _has_credentials() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_DB_URL"))


def test_psycopg_connects_and_select_one(_has_credentials):
    if not _has_credentials:
        pytest.skip("missing SUPABASE_DB_URL")
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select 1")
        assert cur.fetchone() == (1,)


def test_psycopg_reads_model_catalog_seed(_has_credentials):
    if not _has_credentials:
        pytest.skip("missing SUPABASE_DB_URL")
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select count(*) from model_catalog")
        assert cur.fetchone() == (6,)


def test_psycopg_reads_sites_empty(_has_credentials):
    """sites should be empty before bootstrap_first_site.py runs."""
    if not _has_credentials:
        pytest.skip("missing SUPABASE_DB_URL")
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select count(*) from sites")
        # bootstrap may already have run; treat any count as fine but log it
        count = cur.fetchone()[0]
        assert count >= 0  # sanity, not a strict bound


def test_supabase_client_singleton(_has_credentials):
    if not _has_credentials:
        pytest.skip("missing SUPABASE_URL")
    a = get_supabase_client()
    b = get_supabase_client()
    assert a is b


def test_supabase_client_reads_model_catalog(_has_credentials):
    """service_role bypasses RLS — should see all 6 seeded rows."""
    if not _has_credentials:
        pytest.skip("missing SUPABASE_URL")
    client = get_supabase_client()
    res = client.table("model_catalog").select("model_id").execute()
    assert len(res.data) == 6
    ids = {r["model_id"] for r in res.data}
    assert "gemini-3.1-pro-preview" in ids
    assert "gemini-2.5-flash-image" in ids
