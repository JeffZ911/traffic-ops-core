"""Live tests against model_catalog. Cleans up its own write."""

from __future__ import annotations

import math

import pytest

from src.db.client import get_db_connection
from src.db.model_catalog_client import (
    estimate_cost,
    get_model,
    list_active_models,
    mark_deprecated,
)
from src.models.model_catalog import Modality, ModelStatus


def test_get_model_hit():
    m = get_model("gemini", "gemini-3-flash-preview")
    assert m is not None
    assert m.model_id == "gemini-3-flash-preview"
    assert m.modality is Modality.text
    assert m.is_recommended is True


def test_get_model_miss():
    m = get_model("gemini", "this-model-does-not-exist-zzz")
    assert m is None


def test_list_active_models_total():
    """6 seeded rows: 3 text + 3 image. Status preview/active should match."""
    rows = list_active_models()
    # All 6 seed rows have status preview or active
    assert len(rows) == 6
    statuses = {r.status for r in rows}
    assert statuses <= {ModelStatus.preview, ModelStatus.active}


def test_list_active_models_text_only():
    rows = list_active_models(modality="text")
    assert len(rows) == 3
    assert all(r.modality is Modality.text for r in rows)


def test_list_active_models_image_only():
    rows = list_active_models(modality="image")
    assert len(rows) == 3
    assert all(r.modality is Modality.image for r in rows)


def test_estimate_cost_flash_text():
    """gemini-3-flash-preview: $0.30/1M in, $2.50/1M out
       1000 in + 500 out → 0.30/1M*1000 + 2.50/1M*500 = 0.0003 + 0.00125 = 0.00155"""
    cost = estimate_cost("gemini-3-flash-preview", tokens_in=1000, tokens_out=500)
    assert math.isclose(cost, 0.00155, rel_tol=1e-9)


def test_estimate_cost_unknown_raises():
    with pytest.raises(LookupError):
        estimate_cost("gemini-nope-9000", tokens_in=10, tokens_out=10)


def test_mark_deprecated_round_trip():
    """Insert a temp test model, mark it, verify, delete."""
    test_id = "__test_deprecate__"
    with get_db_connection() as conn, conn.cursor() as cur:
        # Clean up any prior leftover
        cur.execute("delete from model_catalog where model_id = %s", (test_id,))
        cur.execute(
            """
            insert into model_catalog
              (provider, model_id, display_name, modality, task_types, status)
            values ('gemini', %s, 'Temp Test Model', 'text', ARRAY['writing'], 'preview')
            """,
            (test_id,),
        )

    try:
        mark_deprecated(test_id, "smoke-test-fail: 404")
        m = get_model("gemini", test_id)
        assert m is not None
        assert m.status is ModelStatus.deprecated
        assert m.last_verify_error == "smoke-test-fail: 404"
        assert m.last_verified_at is not None
    finally:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("delete from model_catalog where model_id = %s", (test_id,))
