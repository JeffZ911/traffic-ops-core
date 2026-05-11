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


def active_sites() -> list[tuple[UUID, str]]:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select id, domain from sites where status = 'active' order by domain"
        )
        return [(r[0], r[1]) for r in cur.fetchall()]


def run_one(
    site_id: UUID, domain: str, target_date: date, sources: tuple[str, ...]
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
            _, parsed = gsc.fetch(site_id, target_date)
            aggregate.merge_gsc(site_id, target_date, parsed)
            out["gsc"] = (
                f"ok (clicks={parsed.clicks}, impressions={parsed.impressions})"
                if parsed else "ok (no rows — zeros written)"
            )
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
        results = run_one(site_id, domain, target, sources)
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
