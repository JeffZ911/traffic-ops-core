"""
Read/write helpers for the model_catalog table.

All reads return Pydantic ModelCatalogEntry instances (never raw dicts) so
callers benefit from validation and type hints.
"""

from __future__ import annotations

from typing import Optional

from src.db.client import get_db_connection
from src.models.model_catalog import ModelCatalogEntry, Modality


_COLUMNS = (
    "id, provider, model_id, display_name, modality, task_types, tier, "
    "input_cost_per_1m, output_cost_per_1m, per_image_cost, context_window, "
    "supports_json_mode, status, is_recommended, released_at, deprecate_at, "
    "last_verified_at, last_verify_error, notes, added_at"
)


def _row_to_entry(cur, row: tuple) -> ModelCatalogEntry:
    cols = [d.name for d in cur.description]
    return ModelCatalogEntry.model_validate(dict(zip(cols, row)))


def get_model(provider: str, model_id: str) -> Optional[ModelCatalogEntry]:
    """Look up a single model by (provider, model_id). Returns None if absent."""
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"select {_COLUMNS} from model_catalog "
            f"where provider = %s and model_id = %s",
            (provider, model_id),
        )
        row = cur.fetchone()
        return _row_to_entry(cur, row) if row else None


def list_active_models(modality: Optional[str] = None) -> list[ModelCatalogEntry]:
    """List models whose status is 'active' or 'preview'. Optional modality filter."""
    sql = (
        f"select {_COLUMNS} from model_catalog "
        f"where status in ('active', 'preview')"
    )
    params: tuple = ()
    if modality is not None:
        # Validate the input against the enum so we never pass garbage to SQL
        Modality(modality)
        sql += " and modality = %s"
        params = (modality,)
    sql += " order by modality, tier nulls last, model_id"

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
        return [
            ModelCatalogEntry.model_validate(dict(zip(cols, r))) for r in rows
        ]


def estimate_cost(model_id: str, tokens_in: int, tokens_out: int) -> float:
    """
    Compute USD cost for a text generation call given the catalog price.

    Uses the per-1M-token columns (input_cost_per_1m, output_cost_per_1m).
    Image models are not handled here — use per_image_cost separately.

    Raises LookupError if the model is missing from the catalog (we should
    never silently zero-cost an unknown model).
    """
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select input_cost_per_1m, output_cost_per_1m "
            "from model_catalog where model_id = %s",
            (model_id,),
        )
        row = cur.fetchone()
        if not row:
            raise LookupError(f"model_id not in model_catalog: {model_id}")
        in_per_m, out_per_m = row

    cost = 0.0
    if in_per_m is not None:
        cost += float(in_per_m) * tokens_in / 1_000_000
    if out_per_m is not None:
        cost += float(out_per_m) * tokens_out / 1_000_000
    return cost


def mark_deprecated(model_id: str, error_msg: str) -> None:
    """
    Set status='deprecated' and record the failure reason. Used by the
    health-check workflow when a model_id stops responding.
    """
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "update model_catalog "
            "set status = 'deprecated', "
            "    last_verify_error = %s, "
            "    last_verified_at = now() "
            "where model_id = %s",
            (error_msg, model_id),
        )
