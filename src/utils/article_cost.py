"""Roll up per-article cost + tokens into the articles row.

Why this exists
---------------
`agent_runs.cost_usd` captures the LLM (text) cost of each step, and the
`images` table captures per-image generation cost — but nothing ever wrote
the combined total back to `articles.total_cost_usd` / `total_tokens`. So the
dashboard's "avg cost / article" read $0 and the monthly budget guard
undercounted (it missed image cost entirely).

`recompute_article_cost` is the single source of truth: it sets
  total_cost_usd = sum(agent_runs.cost_usd) + sum(images.cost_usd)
  total_tokens   = sum(agent_runs.tokens_in + tokens_out)
for one article. Idempotent — safe to call after writing/QA and again after
image generation (the second call simply picks up the now-present image rows).

Image cost is taken from the `images` table (not agent_runs) because the
ImageAgent records cost per-image there; agent_runs image rows stay at 0, so
summing both sources never double-counts.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.db.client import get_db_connection


def recompute_article_cost(article_id: UUID | str) -> dict[str, Any]:
    """Recompute and persist total_cost_usd + total_tokens for one article.

    Returns the new {cost_usd, tokens} for logging. Best-effort: never raises
    on a missing article (returns zeros)."""
    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            update articles a set
              total_cost_usd =
                  coalesce((select sum(cost_usd) from agent_runs
                              where article_id = a.id), 0)
                + coalesce((select sum(cost_usd) from images
                              where article_id = a.id), 0),
              total_tokens =
                  coalesce((select sum(coalesce(tokens_in, 0) + coalesce(tokens_out, 0))
                              from agent_runs where article_id = a.id), 0)
            where a.id = %s
            returning total_cost_usd, total_tokens
            """,
            (str(article_id),),
        )
        row = cur.fetchone()
    if not row:
        return {"cost_usd": 0.0, "tokens": 0}
    return {"cost_usd": float(row[0] or 0), "tokens": int(row[1] or 0)}
