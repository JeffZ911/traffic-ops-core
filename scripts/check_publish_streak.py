"""Zero-published streak alert.

Runs at the tail of the daily cron. Queries `articles` for the count
of status='published' rows in the last N days (default 3). If the
count is zero — i.e., the cron has produced nothing publishable for
N straight days — sends a warning email so the operator notices
before a week of silent failure compounds.

The streak threshold is intentionally short (3 days):
  - 1 day = noisy (today's article can legitimately qa_fail)
  - 7 days = too late (a week of zero output is already alarming)
  - 3 days = "the system is consistently failing, not just unlucky once"

Email body includes the slug + qa_score + headline qa_feedback of the
last 3 attempted articles, so the operator can diagnose without
opening the dashboard.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection
from src.utils.send_alert import send_alert


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _published_count(cur, site_id: str, days: int) -> int:
    since = date.today() - timedelta(days=days)
    cur.execute(
        """
        select count(*)
          from articles
         where site_id = %s
           and status = 'published'
           and published_at >= %s
        """,
        (site_id, since),
    )
    return int(cur.fetchone()[0])


def _recent_attempts(cur, site_id: str, limit: int = 3):
    """Last N attempted articles (any non-published status), oldest first
    so the email reads chronologically."""
    cur.execute(
        """
        select slug, status, qa_score,
               coalesce(qa_feedback->'fabricated_terms', '[]'::jsonb) as fab,
               coalesce(qa_feedback->'issues', '[]'::jsonb) as issues,
               created_at
          from articles
         where site_id = %s
           and status != 'published'
           and created_at > now() - interval '5 days'
         order by created_at desc
         limit %s
        """,
        (site_id, limit),
    )
    return list(cur.fetchall())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=3,
                   help="Streak threshold; emit alert when this many "
                        "consecutive days have zero published articles.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute everything but don't send email.")
    args = p.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select id from sites where domain = 'ntecodex.com' limit 1"
        )
        row = cur.fetchone()
        if not row:
            print("❌ ntecodex.com not in sites")
            return 2
        site_id = str(row[0])

        n = _published_count(cur, site_id, args.days)
        recent = _recent_attempts(cur, site_id, limit=3)

    print(f"=== zero-published streak check ===")
    print(f"  threshold: {args.days} day(s)")
    print(f"  published in last {args.days}d: {n}")
    print(f"  recent attempts (last 5d, top 3):")
    for slug, status, qa, fab, issues, created in recent:
        fab_n = len(fab) if isinstance(fab, list) else 0
        print(f"    - {created.isoformat()[:19]}  {status:11s} qa={qa}  fab={fab_n}  {slug}")

    if n > 0:
        print(f"  ✓ pipeline is producing — no alert")
        return 0

    # n == 0 → streak alert
    body_lines = [
        f"Zero published articles in the last {args.days} days.",
        f"Check time: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Recent attempts (last 5 days):",
    ]
    if not recent:
        body_lines.append("  (none — pipeline may be entirely silent)")
    for slug, status, qa, fab, issues, created in recent:
        body_lines.append(
            f"  - {created.isoformat()[:19]}  {status}  qa_score={qa}  slug={slug}"
        )
        if isinstance(fab, list) and fab:
            body_lines.append(f"      fabricated_terms: {fab[:5]}")
        if isinstance(issues, list) and issues:
            body_lines.append(f"      first issue: {str(issues[0])[:200]}")

    body_lines.extend([
        "",
        "Likely causes:",
        "- All recent articles qa_failed — check qa_feedback for systematic patterns.",
        "- KeywordSelector picking blacklisted types — check selector output.",
        "- GitHub Actions workflow disabled or crashing — check Actions tab.",
        "",
        f"Run history: {os.environ.get('GITHUB_SERVER_URL','')}/{os.environ.get('GITHUB_REPOSITORY','')}/actions",
    ])
    body = "\n".join(body_lines)

    if args.dry_run:
        print()
        print("--- ALERT BODY (dry-run, not sent) ---")
        print(body)
        return 0

    try:
        send_alert(
            subject=f"[ntecodex] Zero published streak — {args.days} days",
            body=body,
            severity="warning",
        )
        print("  ✓ alert email sent")
    except Exception as e:
        print(f"  ❌ alert send failed: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
