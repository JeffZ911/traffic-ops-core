"""Velocity guardrail — alert when daily pass-rate falls outside the
healthy band for 3 consecutive days.

Background (Phase 2.4 / 2026-05-12): the cron was accelerated to 24
attempts/day with a $150 budget. At the design target of 65% pass-rate
this publishes ~15 articles/day. If pass-rate drops too low we're
burning money on rejected drafts; if it stays very high we're leaving
throughput on the table by not running more cron.

Behavior:
  - look at the last 3 calendar days (today and the 2 before)
  - count qa_passed + qa_failed per day (anything else excluded)
  - pass_rate_per_day = qa_passed / (qa_passed + qa_failed)
  - if all 3 days < 50% → warning email "slowdown_alert"
  - if all 3 days > 75% → info email "headroom_alert" (could push to
    8 cron/day)
  - otherwise: no-op
  - emails dedup'd via daily_reports.data_snapshot.velocity_alert.last
    so we don't re-send the same alert on every cron of the same day

Idempotent / cheap: pure SQL + maybe one email. Runs as the tail step
of every cron — `continue-on-error: true` at the workflow level so a
DB or SMTP hiccup never blocks the deploy.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection
from src.utils.send_alert import send_alert


load_dotenv(Path(__file__).resolve().parents[2] / ".env")


SLOWDOWN_THRESHOLD = 0.50    # all 3 days below → slowdown alert
HEADROOM_THRESHOLD = 0.75    # all 3 days above → headroom alert
LOOKBACK_DAYS = 3


def _pass_rate_per_day(cur, site_id: str, days: int = LOOKBACK_DAYS) -> list[dict]:
    """Return [{date, pass, fail, total, rate}, ...] for the last N
    calendar days (today + previous days-1)."""
    today = date.today()
    out = []
    for d_offset in range(days - 1, -1, -1):
        d = today - timedelta(days=d_offset)
        cur.execute(
            """
            select sum(case when status in ('published', 'qa_passed') then 1 else 0 end)::int as p,
                   sum(case when status = 'qa_failed' then 1 else 0 end)::int as f
              from articles
             where site_id = %s and created_at::date = %s
            """,
            (site_id, d),
        )
        row = cur.fetchone()
        p = int(row[0] or 0)
        f = int(row[1] or 0)
        total = p + f
        rate = (p / total) if total > 0 else None
        out.append({"date": d.isoformat(), "pass": p, "fail": f,
                    "total": total, "rate": rate})
    return out


def _decide(stats: list[dict]) -> str:
    """Return one of 'slowdown', 'headroom', 'ok', 'insufficient_data'."""
    # Need each day to have at least 1 attempt
    if not all(d["total"] > 0 for d in stats):
        return "insufficient_data"
    rates = [d["rate"] for d in stats if d["rate"] is not None]
    if len(rates) != len(stats):
        return "insufficient_data"
    if all(r < SLOWDOWN_THRESHOLD for r in rates):
        return "slowdown"
    if all(r > HEADROOM_THRESHOLD for r in rates):
        return "headroom"
    return "ok"


def _last_alert_kind(cur, site_id: str) -> str | None:
    cur.execute(
        """
        select data_snapshot->'velocity_alert'->>'kind'
          from daily_reports
         where site_id = %s and data_snapshot ? 'velocity_alert'
         order by report_date desc limit 1
        """,
        (site_id,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _record_alert(cur, site_id: str, kind: str, stats: list[dict]) -> None:
    """Upsert today's daily_reports with velocity_alert payload."""
    today = date.today()
    payload = {
        "kind": kind,
        "at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
    }
    md = f"# velocity_alert: {kind} — {today.isoformat()}\n"
    cur.execute(
        """
        insert into daily_reports
          (site_id, report_date, markdown, data_snapshot)
        values (%s, %s, %s, %s::jsonb)
        on conflict (site_id, report_date) do update set
          data_snapshot = coalesce(daily_reports.data_snapshot, '{}'::jsonb)
                       || jsonb_build_object('velocity_alert',
                            excluded.data_snapshot -> 'velocity_alert')
        """,
        (site_id, today, md, json.dumps({"velocity_alert": payload})),
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Compute decision but don't send email or write DB")
    args = p.parse_args()

    import os
    site_domain = os.getenv("SITE_DOMAIN", "ntecodex.com")
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain = %s limit 1", (site_domain,))
        row = cur.fetchone()
        if not row:
            print(f"❌ site {site_domain!r} not in sites")
            return 2
        site_id = str(row[0])
        stats = _pass_rate_per_day(cur, site_id)
        decision = _decide(stats)
        last_kind = _last_alert_kind(cur, site_id)

    print(f"=== velocity_check ({LOOKBACK_DAYS}-day pass-rate) ===")
    for d in stats:
        rate = f"{d['rate']*100:.0f}%" if d['rate'] is not None else "—"
        print(f"  {d['date']}  pass={d['pass']:2d}  fail={d['fail']:2d}  rate={rate}")
    print(f"  decision: {decision}  last_alert_kind: {last_kind}")

    if decision in ("ok", "insufficient_data"):
        return 0

    # Dedup: don't re-send the same kind on the same calendar day if
    # we already wrote a velocity_alert today.
    if last_kind == decision:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "select report_date from daily_reports "
                "where site_id = %s and data_snapshot ? 'velocity_alert' "
                "order by report_date desc limit 1",
                (site_id,),
            )
            r = cur.fetchone()
        if r and r[0] == date.today():
            print(f"  already alerted today with kind={decision!r}; skipping")
            return 0

    rates_str = ", ".join(
        f"{d['date']} {d['rate']*100:.0f}%" for d in stats
    )
    if decision == "slowdown":
        subject = f"[ntecodex] velocity slowdown — 3-day pass-rate all <{SLOWDOWN_THRESHOLD*100:.0f}%"
        body = (
            f"3 consecutive days of pass-rate below {SLOWDOWN_THRESHOLD*100:.0f}%:\n"
            f"  {rates_str}\n\n"
            f"Likely causes:\n"
            f"- KeywordSelector is picking from a low-quality pool — check\n"
            f"  recently-seeded keywords for fabricated entities\n"
            f"- A specific game's wiki info has gone stale — check the\n"
            f"  per-game pass-rate breakdown in articles + agent_runs\n"
            f"- QAAgent threshold needs adjustment for new article_type mix\n\n"
            f"Recommendation: review the daily_reports.data_snapshot."
            f"velocity_alert.stats and the qa_feedback of failed articles."
        )
        severity = "warning"
    else:  # headroom
        subject = f"[ntecodex] velocity headroom — 3-day pass-rate all >{HEADROOM_THRESHOLD*100:.0f}%"
        body = (
            f"3 consecutive days of pass-rate above {HEADROOM_THRESHOLD*100:.0f}%:\n"
            f"  {rates_str}\n\n"
            f"Pipeline is healthy. Consider pushing throughput:\n"
            f"- Add 2 more cron/day to schedule (currently 6) for 8 cron/day\n"
            f"- Bump batch_smoke --count from 4 to 5\n"
            f"- Verify monthly_budget_usd is still adequate (current spend\n"
            f"  in the email's prior week)"
        )
        severity = "info"

    if args.dry_run:
        print("\n--- DRY RUN ---")
        print(f"subject: {subject}")
        print(f"body:\n{body}")
        return 0

    try:
        send_alert(subject=subject, body=body, severity=severity)
        print(f"  ✓ alert email sent ({decision})")
    except Exception as e:
        print(f"  ⚠️ alert send failed: {e}")

    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        _record_alert(cur, site_id, decision, stats)

    return 0


if __name__ == "__main__":
    sys.exit(main())
