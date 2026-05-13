"""Auto-finalize any article stuck in a non-terminal status > 1 hour.

Mid-pipeline crashes (network blips, OOM kills, orchestrator exceptions
caught at the wrong layer) leave articles in draft / qa_pending /
writing states. The Dashboard then shows them as "pending review" —
which violates the autonomous-operations principle: the operator should
not have to click anything.

Policy (decided 2026-05-13 P0):
  - draft / writing / qa_pending older than 1 hour → status='failed'
  - Per-row failure_reason annotated with the original stuck status
  - No pending_review state ever — that's also moved to failed if it
    somehow appears
  - Runs as the tail of every cron invocation; idempotent

The articles.status CHECK constraint already includes 'failed', so
this is a pure data-finalize, no schema change.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


STUCK_STATUSES = ("draft", "qa_pending", "writing", "pending_review")
STUCK_AGE_INTERVAL = "1 hour"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-age-hours", type=int, default=1)
    args = p.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            select id::text, slug, status, qa_score,
                   extract(epoch from now() - created_at)::int / 60 as age_min
              from articles
             where status = ANY(%s)
               and created_at < now() - interval '{args.max_age_hours} hour'
             order by created_at
            """,
            (list(STUCK_STATUSES),),
        )
        rows = cur.fetchall()

    if not rows:
        print(f"✓ no stuck articles older than {args.max_age_hours}h")
        return 0

    print(f"⚠️  found {len(rows)} stuck article(s) older than {args.max_age_hours}h:")
    for r in rows:
        print(f"   {r[2]:14s} age={r[4]:5d}min  qa={r[3]}  slug={r[1]}")

    if args.dry_run:
        print("  --dry-run; not modifying")
        return 0

    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            update articles
               set status = 'failed',
                   failure_reason = coalesce(failure_reason, '')
                     || ' [auto-finalize: stuck in '|| status ||' for '
                     || extract(epoch from now() - created_at)::int / 60
                     || ' min]'
             where status = ANY(%s)
               and created_at < now() - interval '{args.max_age_hours} hour'
            returning slug
            """,
            (list(STUCK_STATUSES),),
        )
        finalized = cur.fetchall()

    print(f"  ✓ finalized {len(finalized)} row(s) → status='failed'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
