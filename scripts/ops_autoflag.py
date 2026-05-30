"""Auto-flag human-action items into ops_tasks when the pipeline detects
a condition only a human can fix — and auto-resolve them when cleared.

Runs once per cron (cheap, all DB/GSC reads). Each check is paired:
  - condition true  → upsert_open_task (idempotent, no dup spam)
  - condition false → resolve_open_task (auto-marks done)

Checks:
  1. GA4 property_id missing in sites.config        → card (per site)
  2. GSC property not accessible (sitemaps.list 403) → card (per site)
  3. OAuth token refresh failing                     → card (global)
  4. Budget > 80% of monthly cap                     → card (per site)
  5. ≥3 consecutive days with 0 published            → card (per site)

Usage:
  python -m scripts.ops_autoflag            # all active sites
  python -m scripts.ops_autoflag --site ntecodex.com
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection
from src.utils.ops_tasks import upsert_open_task, resolve_open_task

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _active_sites(site_filter: str | None):
    with get_db_connection() as conn, conn.cursor() as cur:
        if site_filter:
            cur.execute("select domain, config from sites where domain=%s", (site_filter,))
        else:
            cur.execute("select domain, config from sites where status='active' order by domain")
        return cur.fetchall()


def check_ga4(domain: str, cfg: dict) -> None:
    title = f"Fill GA4 Property ID — {domain}"
    if cfg.get("ga4_property_id"):
        resolve_open_task(title, site_domain=domain)
        return
    upsert_open_task(
        title,
        "Metrics collector can't fetch GA4 without the numeric Property ID.\n"
        "HOW: analytics.google.com → Admin → property → Property Settings → "
        "copy 'Property ID' (9-10 digit) → Dashboard /sites → this site's "
        "'GA4 property ID' field → Save.",
        priority="high", category="new-site", site_domain=domain,
    )


def check_budget(domain: str, cfg: dict) -> None:
    title = f"Budget >80% — {domain}"
    cap = float(cfg.get("monthly_budget_usd") or 0)
    if cap <= 0:
        return
    with get_db_connection() as conn, conn.cursor() as cur:
        # LLM (agent_runs) + image (images) cost, month-to-date.
        cur.execute(
            "select coalesce((select sum(cost_usd) from agent_runs "
            "  where site_id=(select id from sites where domain=%(d)s) "
            "    and created_at >= date_trunc('month', now())),0) "
            "+ coalesce((select sum(cost_usd) from images "
            "  where site_id=(select id from sites where domain=%(d)s) "
            "    and created_at >= date_trunc('month', now())),0)",
            {"d": domain},
        )
        spent = float(cur.fetchone()[0] or 0)
    pct = spent / cap if cap else 0
    if pct < 0.80:
        resolve_open_task(title, site_domain=domain)
        return
    upsert_open_task(
        title,
        f"Month-to-date spend ${spent:.2f} of ${cap:.0f} ({pct:.0%}). The "
        f"budget guard will pause content at 95%. Decide: raise "
        f"sites.config.monthly_budget_usd, or let it coast to month rollover.",
        priority="high", category="billing", site_domain=domain,
    )


def check_zero_published(domain: str) -> None:
    title = f"3+ days with 0 published — {domain}"
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from articles "
            "where site_id=(select id from sites where domain=%s) "
            "and status='published' and published_at >= %s",
            (domain, (date.today() - timedelta(days=3)).isoformat()),
        )
        recent = cur.fetchone()[0]
    if recent > 0:
        resolve_open_task(title, site_domain=domain)
        return
    upsert_open_task(
        title,
        "No articles published in the last 3 days. Check: cron runs green? "
        "keyword pool not exhausted? QA pass-rate collapse? Look at the "
        "Cron health table on Mission Control + recent agent_runs errors.",
        priority="high", category="infra", site_domain=domain,
    )


def check_gsc_access(domain: str) -> bool:
    """Return True if GSC property is accessible. Opens/clears a card."""
    title = f"Verify GSC property — {domain}"
    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from src.utils.google_oauth import get_user_credentials
        svc = build("searchconsole", "v1", credentials=get_user_credentials(), cache_discovery=False)
        try:
            svc.sitemaps().list(siteUrl=f"sc-domain:{domain}").execute()
            resolve_open_task(title, site_domain=domain)
            return True
        except HttpError as e:
            if e.resp.status in (403, 404):
                upsert_open_task(
                    title,
                    f"GSC can't access sc-domain:{domain} ({e.resp.status}). "
                    f"HOW: search.google.com/search-console → add domain "
                    f"property {domain} → verify via DNS TXT → ensure the "
                    f"OAuth Google account is listed as Owner.",
                    priority="high", category="new-site", site_domain=domain,
                )
            return False
    except Exception:
        return False


def check_indexing_stagnation(domain: str) -> None:
    """Day-7 stagnation alert. daily_indexing_worklist records an
    indexing_coverage payload each day. If we have ≥7 distinct days of data
    and the latest indexed ratio is still ~0%, the sitemap fix likely didn't
    take — open a diagnose card. Otherwise (rising, or too few days) clear it.
    """
    title = f"Indexing still ~0% after 7+ days — {domain}"
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select metric_date,
                   (payload->'indexing_coverage'->>'ratio')::float as ratio
              from metrics_raw
             where site_id = (select id from sites where domain=%s)
               and source = 'gsc'
               and payload ? 'indexing_coverage'
               and metric_date >= current_date - 21
             order by metric_date desc
            """,
            (domain,),
        )
        rows = cur.fetchall()

    # Dedupe to the latest ratio per day (worklist can run more than once).
    by_day: dict = {}
    for d, r in rows:
        if d not in by_day:
            by_day[d] = r
    days = sorted(by_day.keys(), reverse=True)

    if len(days) < 7:
        # Not enough history yet — never alarm, and clear any stale card.
        resolve_open_task(title, site_domain=domain)
        return

    latest_ratio = by_day[days[0]] or 0.0
    first_day = sorted(by_day.keys())[0]
    if latest_ratio >= 0.02:  # any real movement off zero → healthy enough
        resolve_open_task(title, site_domain=domain)
        return

    # STAGED ESCALATION (2026-05-30). A flat "still 0%" card gave the same
    # advice forever and bottomed out at "it may just need more time". But
    # the cause CHANGES with site age: early on it really is just crawl
    # lag; past a month with content flowing, the blocker is unambiguously
    # AUTHORITY, and the right action is off-page work (see the
    # [Authority · …] cards from seo_growth_loop), not more diagnosing.
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select current_date - min(created_at)::date from articles "
            "where site_id = (select id from sites where domain=%s)",
            (domain,),
        )
        row = cur.fetchone()
    age_days = int(row[0]) if row and row[0] is not None else 0

    base = (
        f"{len(days)} days of indexing samples since {first_day}, latest "
        f"still {latest_ratio:.0%} indexed. Site age: {age_days}d.\n"
    )
    if age_days < 14:
        priority = "normal"
        body = (
            "STAGE 1 (young domain, <14d) — likely just crawl lag, NOT "
            "broken. ACTION: in GSC, URL-inspect your 5 best articles + "
            "click 'Request indexing' for each. Confirm sitemap is "
            "submitted + downloaded with no errors. Then wait — Google "
            "rations crawl budget for new domains."
        )
    elif age_days <= 30:
        priority = "high"
        body = (
            "STAGE 2 (14-30d, still 0%) — crawl lag alone no longer "
            "explains this. Google has likely marked the corpus "
            "'Discovered – currently not indexed': a low-authority signal. "
            "ACTION: stop waiting, start EARNING authority. Do this week's "
            "[Authority · Community] + [Authority · Outreach] cards — the "
            "first few real backlinks are what unlock crawl budget. Also "
            "verify a 'Live test' passes in URL Inspection (rules out a "
            "technical block)."
        )
    else:
        priority = "high"
        body = (
            "STAGE 3 (30+ days, still 0%) — RED. This is the authority wall, "
            "full stop. More content makes it WORSE (content-farm signal). "
            "ACTION: (1) sustain the weekly authority cadence — community "
            "answers, linkable assets, outreach; (2) consider concentrating "
            "internal links onto 5-10 pillar pages instead of spreading thin; "
            "(3) if a competitor audit shows the niche is saturated by "
            "high-DA incumbents, this domain may need a longer authority "
            "runway than planned — set expectations accordingly. Check the "
            "[Funnel review] card for the action→outcome trend."
        )

    upsert_open_task(
        title, base + body,
        priority=priority, category="seo", site_domain=domain,
    )


def check_oauth() -> None:
    title = "OAuth token refresh failing"
    try:
        from src.utils.google_oauth import get_user_credentials
        get_user_credentials()  # raises on bad/expired refresh token
        resolve_open_task(title)
    except Exception as e:  # noqa: BLE001
        upsert_open_task(
            title,
            f"GSC/GA4 OAuth refresh failed: {type(e).__name__}. All collectors "
            f"+ sitemap resubmit will fail until fixed.\nHOW: re-run "
            f"`python -m scripts.oauth_setup`, update GOOGLE_OAUTH_REFRESH_TOKEN "
            f"in .env AND GitHub Secret.",
            priority="high", category="infra", site_domain=None,
        )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--site", default=None)
    args = p.parse_args()

    check_oauth()  # global, once

    sites = _active_sites(args.site)
    for domain, config in sites:
        cfg = config or {}
        check_ga4(domain, cfg)
        check_budget(domain, cfg)
        check_zero_published(domain)
        check_gsc_access(domain)
        check_indexing_stagnation(domain)
        print(f"  ✓ auto-flag checks done for {domain}")

    # Summarize current open auto cards.
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select count(*) from ops_tasks where status='open' and source='auto'")
        print(f"  open auto-flagged tasks: {cur.fetchone()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
