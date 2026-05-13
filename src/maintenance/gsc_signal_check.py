"""Replace the publish-streak alert with REAL Google-traction signals.

The 'zero published in N days' alert was useful while we had a single
push-broken state. Now that the pipeline produces 30-70 articles/day,
the meaningful signal is GSC traction — Google index + impressions +
ranking — not how many MD files we wrote yesterday.

Two alerts:

  1. **GSC Stagnation** (warning email):
     If the last 7 days of GSC data show
       - total impressions <  IMPRESSIONS_FLOOR (default 10)
       - AND zero new keywords entered top-30
     fire the alert. This means we're shipping content but Google
     isn't surfacing it — could be indexing problem, could be E-E-A-T
     too low, could be over-saturated niche.

  2. **Positive-Signal** (info email):
     The FIRST TIME a keyword crosses into top-10, send a celebratory
     email. This is the leading indicator of escape velocity — when
     traffic compounds it does so through top-10 rankings.
     Dedup'd via daily_reports.data_snapshot.positive_signal.seen_top10
     so we don't re-fire for the same keyword.

The 'zero published streak' check (scripts/check_publish_streak.py)
stays in the codebase for emergency manual use, but is REMOVED from
the cron — at 72/day the streak threshold is meaningless.

Runs as the tail step of every cron (the file is small; querying GSC
is rate-limited but Google allows tens of thousands of searchanalytics
calls per day so 24× our usage is fine).
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


IMPRESSIONS_FLOOR = 10        # 7-day total threshold for stagnation
LOOKBACK_DAYS = 7
TOP10_POSITION = 10.0


def _gsc_query(svc, site_url: str, body: dict) -> dict:
    """Try sc-domain: then https:// property URL forms."""
    from src.collectors.gsc import _try_query
    return _try_query(svc, site_url, body)


def _site_property(site_id):
    from src.collectors.gsc import _site_property as _sp
    return _sp(site_id)


def _fetch_last_n_days(site_id, days: int = LOOKBACK_DAYS) -> dict:
    """Pull aggregate + per-query for the last `days` days."""
    try:
        from googleapiclient.discovery import build
        from src.utils.google_oauth import get_user_credentials
    except Exception as e:
        return {"error": f"libs unavailable: {e}", "queries": []}
    try:
        creds = get_user_credentials()
        svc = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        return {"error": f"oauth failed: {e}", "queries": []}
    try:
        site_url = _site_property(site_id)
    except Exception as e:
        return {"error": f"site_property: {e}", "queries": []}

    end = date.today() - timedelta(days=2)   # GSC ~2d lag
    start = end - timedelta(days=days - 1)
    try:
        rows = _gsc_query(svc, site_url, {
            "startDate": start.isoformat(), "endDate": end.isoformat(),
            "dimensions": ["query"], "rowLimit": 500,
        }).get("rows", []) or []
    except Exception as e:
        return {"error": f"query fetch: {e}", "queries": []}
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "queries": rows,
        "site_url": site_url,
    }


def _top10_keywords(queries: list) -> list[dict]:
    out = []
    for r in queries:
        keys = r.get("keys") or []
        if not keys:
            continue
        pos = float(r.get("position", 999))
        if pos <= TOP10_POSITION:
            out.append({
                "keyword": keys[0],
                "position": round(pos, 2),
                "impressions": int(r.get("impressions", 0)),
                "clicks": int(r.get("clicks", 0)),
            })
    return out


def _already_seen_top10(cur, site_id: str) -> set[str]:
    """Return set of keywords we've ALREADY emailed about reaching top-10.
    Stored in any daily_reports.data_snapshot.positive_signal.seen_top10
    across history."""
    cur.execute(
        """
        select coalesce(jsonb_agg(distinct kw), '[]'::jsonb)
          from daily_reports,
               jsonb_array_elements(coalesce(
                 data_snapshot->'positive_signal'->'seen_top10', '[]'::jsonb
               )) kw
         where site_id = %s
        """,
        (site_id,),
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return set()
    arr = row[0]
    return {str(x) for x in arr}


def _record_seen_top10(cur, site_id: str, new_seen: set[str]) -> None:
    """Append today's newly-seen top-10 keywords to today's daily_reports."""
    today = date.today()
    cur.execute(
        """
        insert into daily_reports (site_id, report_date, markdown, data_snapshot)
        values (%s, %s, %s, %s::jsonb)
        on conflict (site_id, report_date) do update set
          data_snapshot = coalesce(daily_reports.data_snapshot, '{}'::jsonb)
                       || jsonb_build_object('positive_signal',
                          coalesce(daily_reports.data_snapshot->'positive_signal','{}'::jsonb)
                          || jsonb_build_object('seen_top10',
                             coalesce(daily_reports.data_snapshot->'positive_signal'->'seen_top10','[]'::jsonb) || excluded.data_snapshot->'positive_signal'->'seen_top10'))
        """,
        (
            site_id, today,
            f"# positive_signal — {today.isoformat()}\n",
            json.dumps({"positive_signal": {"seen_top10": sorted(new_seen)}}),
        ),
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain='ntecodex.com' limit 1")
        site_id = str(cur.fetchone()[0])

    data = _fetch_last_n_days(site_id)
    if "error" in data:
        print(f"  ⚠️ GSC fetch issue: {data['error']}; skip checks")
        return 0

    queries = data.get("queries", [])
    total_impressions = sum(int(q.get("impressions", 0)) for q in queries)
    top10_now = _top10_keywords(queries)

    print(f"=== gsc_signal_check ===")
    print(f"  window: {data.get('window')}")
    print(f"  total queries surfaced: {len(queries)}")
    print(f"  total impressions (7d): {total_impressions}")
    print(f"  top-10 keywords (7d): {len(top10_now)}")
    for k in top10_now[:5]:
        print(f"    pos={k['position']:5.2f}  imp={k['impressions']:4d}  {k['keyword']!r}")

    with get_db_connection() as conn, conn.cursor() as cur:
        seen_before = _already_seen_top10(cur, site_id)

    new_top10 = [k for k in top10_now if k["keyword"] not in seen_before]
    if new_top10:
        print(f"  🎉 NEW top-10 keywords: {len(new_top10)}")

    # --- Positive-signal alert (only on new top-10s) ---
    if new_top10:
        body_lines = [
            f"🎉 First-time top-10 ranking on Google!",
            f"  window: {data.get('window')}",
            "",
            "New keywords reaching top-10:",
        ]
        for k in new_top10:
            body_lines.append(
                f"  - {k['keyword']!r} → position {k['position']}, "
                f"{k['impressions']} impressions, {k['clicks']} clicks"
            )
        body_lines.extend([
            "",
            "This is the leading indicator of organic-traffic escape velocity.",
            "Consider:",
            "- Reviewing the article for further deepening / rewriting",
            "- Seeding adjacent long-tail keywords (script: keyword_gardener)",
            "- Adding internal links from older articles to this top performer",
        ])
        body = "\n".join(body_lines)
        if args.dry_run:
            print("\n--- positive-signal email (dry-run) ---")
            print(body)
        else:
            try:
                send_alert(
                    subject=f"[ntecodex] 🎉 New top-10 keyword(s) — {len(new_top10)} first hits",
                    body=body, severity="info",
                )
                print(f"  ✓ positive-signal alert sent")
            except Exception as e:
                print(f"  ⚠️ alert send failed: {e}")
            # Record so we don't re-email
            with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
                _record_seen_top10(
                    cur, site_id,
                    set(seen_before) | {k["keyword"] for k in new_top10},
                )

    # --- Stagnation alert (only when window has very low impressions
    # AND no top-30 keywords). Dedup via report-date — at most one
    # stagnation email per calendar day. ---
    top30_count = sum(1 for q in queries if float(q.get("position", 999)) <= 30.0)
    stagnation = (total_impressions < IMPRESSIONS_FLOOR and top30_count == 0)
    if stagnation:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "select data_snapshot ? 'gsc_stagnation' from daily_reports "
                "where site_id = %s and report_date = current_date",
                (site_id,),
            )
            r = cur.fetchone()
            already_emailed = bool(r and r[0])
        if not already_emailed:
            body = (
                f"GSC stagnation — last 7 days:\n"
                f"  total impressions: {total_impressions} (< {IMPRESSIONS_FLOOR})\n"
                f"  keywords in top 30: {top30_count}\n"
                f"  window: {data.get('window')}\n\n"
                f"This means we're shipping content but Google isn't surfacing it.\n"
                f"Likely causes:\n"
                f"- Indexing lag for a new site (normal up to 30 days post-launch)\n"
                f"- E-E-A-T signals too weak (consider adding About / Author pages)\n"
                f"- Niche over-saturated by established competitors\n"
                f"- Recent content quality regression — check qa_failed rate\n"
            )
            if args.dry_run:
                print("\n--- stagnation email (dry-run) ---")
                print(body)
            else:
                try:
                    send_alert(
                        subject=f"[ntecodex] GSC stagnation — 7d impressions {total_impressions}",
                        body=body, severity="warning",
                    )
                except Exception as e:
                    print(f"  ⚠️ alert send failed: {e}")
                with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
                    cur.execute(
                        """
                        insert into daily_reports (site_id, report_date, markdown, data_snapshot)
                        values (%s, current_date,
                          '# gsc_stagnation\n',
                          jsonb_build_object('gsc_stagnation', %s::jsonb))
                        on conflict (site_id, report_date) do update set
                          data_snapshot = coalesce(daily_reports.data_snapshot, '{}'::jsonb)
                                       || jsonb_build_object('gsc_stagnation',
                                          excluded.data_snapshot->'gsc_stagnation')
                        """,
                        (site_id, json.dumps({
                            "total_impressions_7d": total_impressions,
                            "top30_count": top30_count,
                            "window": data.get("window"),
                        })),
                    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
