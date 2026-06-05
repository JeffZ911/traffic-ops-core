"""Daily collector runner — invoked from the content_daily workflow.

For each `sites` row with status='active':
  - GA4: fetch yesterday's traffic
  - GSC: fetch yesterday's clicks/impressions
  - Aggregate into metrics_daily

Designed to be resilient: any individual source failure is logged but does
NOT abort the run. content_daily.yml uses `continue-on-error: true` for
this step, so a transient GA4 outage doesn't break content publishing.

Usage:
    python -m scripts.run_collectors                # yesterday
    python -m scripts.run_collectors --date 2026-05-10
    python -m scripts.run_collectors --source ga4    # ga4 only
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
from uuid import UUID

from dotenv import load_dotenv

from src.collectors import aggregate, ga4, gsc
from src.db.client import get_db_connection


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# GSC finalizes performance data ~2-3 days late, so 'yesterday' is always in
# the empty lag window. Re-fetch a trailing window each run so the real numbers
# backfill once GSC settles them; refresh top-queries raw for a settled day.
GSC_LAG_DAYS = 3
GSC_BACKFILL_DAYS = 5


def active_sites() -> list[tuple[UUID, str]]:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select id, domain from sites where status = 'active' order by domain"
        )
        return [(r[0], r[1]) for r in cur.fetchall()]


def run_one(
    site_id: UUID, domain: str, target_date: date, sources: tuple[str, ...],
    gsc_backfill_days: int = GSC_BACKFILL_DAYS,
) -> dict[str, str]:
    """Return {source: 'ok' | f'fail: {msg}'} for the report."""
    out: dict[str, str] = {}

    if "ga4" in sources:
        try:
            _, parsed = ga4.fetch(site_id, target_date)
            aggregate.merge_ga4(site_id, target_date, parsed)
            out["ga4"] = (
                f"ok (sessions={parsed.sessions}, pv={parsed.pageviews})"
                if parsed else "ok (no rows — zeros written)"
            )
        except Exception as e:
            out["ga4"] = f"fail: {type(e).__name__}: {str(e)[:200]}"

    if "gsc" in sources:
        try:
            # Backfill a trailing window: settled (late) data overwrites the
            # zeros earlier runs wrote while inside GSC's 2-3 day lag. Only the
            # dates GSC returns are written — missing dates stay untouched
            # (NULL / prior value), never clobbered with a false 0.
            win_start = target_date - timedelta(days=gsc_backfill_days)
            ranged = gsc.fetch_range(site_id, win_start, target_date)
            for d, daily in ranged.items():
                aggregate.merge_gsc(site_id, d, daily)
            # Refresh the per-query raw payload for a settled day so keyword
            # expansion reads real top_queries (best-effort).
            try:
                gsc.fetch(site_id, target_date - timedelta(days=GSC_LAG_DAYS))
            except Exception:
                pass
            if ranged:
                latest = max(ranged)
                d = ranged[latest]
                out["gsc"] = (
                    f"ok (backfilled {len(ranged)} day(s); latest {latest}: "
                    f"impr={d.impressions}, clicks={d.clicks}, "
                    f"pos={d.avg_position:.1f})" if d.avg_position is not None
                    else f"ok (backfilled {len(ranged)} day(s))"
                )
            else:
                out["gsc"] = "ok (no settled GSC data in window yet)"
        except Exception as e:
            out["gsc"] = f"fail: {type(e).__name__}: {str(e)[:200]}"

    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None,
                   help="ISO date (default: yesterday in UTC)")
    p.add_argument(
        "--source", choices=("ga4", "gsc", "all"), default="all",
    )
    p.add_argument(
        "--gsc-backfill", type=int, default=GSC_BACKFILL_DAYS,
        help=f"GSC trailing-window days to re-fetch (default {GSC_BACKFILL_DAYS}; "
             "set high once, e.g. 35, to backfill history)",
    )
    args = p.parse_args()

    if args.date:
        target = date.fromisoformat(args.date)
    else:
        target = date.today() - timedelta(days=1)

    sources = ("ga4", "gsc") if args.source == "all" else (args.source,)

    print(f"📊 Collectors — date={target}  sources={sources}")
    sites = active_sites()
    print(f"   sites: {len(sites)}")
    print()

    overall_fail = 0
    for site_id, domain in sites:
        print(f"▶ {domain}  ({str(site_id)[:8]}…)")
        results = run_one(site_id, domain, target, sources,
                          gsc_backfill_days=args.gsc_backfill)
        for src, status in results.items():
            mark = "✅" if status.startswith("ok") else "❌"
            print(f"   {mark} {src}: {status}")
            if not status.startswith("ok"):
                overall_fail += 1
        print()

    print("=" * 78)
    print(f"Summary: {overall_fail} failure(s) across {len(sites)} sites × {len(sources)} sources")
    return 0  # never fail the workflow — workflow uses continue-on-error too


if __name__ == "__main__":
    sys.exit(main())
