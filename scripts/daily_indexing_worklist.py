"""Build the daily 'GSC request-indexing' worklist card for a site.

There is NO compliant API to request indexing (the GSC button has no
public endpoint; the Indexing API is JobPosting-only). But for the
first few weeks of a young site it's worth manually clicking ~10
URLs/day in the GSC URL Inspection tool. This script does the tedious
part — figuring out WHICH 10 — so the operator just opens /todos and
works the list.

Flow:
  1. urlInspection (READ-ONLY, fully compliant) a prioritized batch of
     published URLs → read coverageState.
  2. Estimate indexed ratio from the inspected sample.
  3. If indexed ratio > 80% → the site no longer needs hand-holding:
     resolve the worklist card and open a one-time "you can stop" card.
  4. Else → collect URLs that are unknown / not-indexed, take the top
     10 by priority (clean tier → newest → has published_url), and
     upsert a single ops_tasks card listing them with steps.

Priority within the inspection batch:
  clean tier first, then note, then others; newest first within tier.

Quota: caps at --max-inspect (default 40) urlInspection calls/run —
well under GSC's per-site daily limit.

Usage:
  python -m scripts.daily_indexing_worklist --site ntecodex.com
  python -m scripts.daily_indexing_worklist --site pixelmatch.art --max-inspect 30
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection
from src.collectors.base import store_raw
from src.utils.ops_tasks import upsert_open_task, resolve_open_task

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

INDEXED_STATES = {"Submitted and indexed", "Indexed, not submitted in sitemap"}
STOP_THRESHOLD = 0.80
# GSC's real per-property quota is ~10-12 request-indexing clicks/day and it
# is not stable. This is a LOW-value first-few-weeks nudge (it only asks Google
# to *look* sooner — it can't beat the authority wall), so keep it short: 5/day
# is a 2-minute task the operator can always finish, and it's fine to skip.
DAILY_REQUEST_CAP = 5


def _public_url(domain: str, published_url: str, niche: str) -> str:
    if published_url.startswith("http"):
        return published_url
    path = published_url if published_url.startswith("/") else f"/{published_url}"
    if niche == "ecommerce_tools":
        host = f"blog.{domain}"
        prefix = "" if path.startswith("/blog") else "/blog"
        return f"https://{host}{prefix}{path}"
    return f"https://{domain}{path}"


def _candidates(cur, site_id: str, domain: str, niche: str, cap: int) -> list[str]:
    from datetime import date
    cur.execute(
        """
        select published_url,
               coalesce(qa_feedback->>'editorial_tier','') as tier,
               published_at
          from articles
         where site_id=%s and status='published' and published_url is not null
        """,
        (site_id,),
    )
    rows = cur.fetchall()
    tier_rank = {"clean": 0, "note": 1, "strong": 2}

    def rank(r):
        _u, tier, pub = r
        return (tier_rank.get(tier, 3), -(pub.timestamp() if pub else 0))

    rows.sort(key=rank)
    out, seen = [], set()
    for pu, _t, _p in rows:
        u = _public_url(domain, pu, niche)
        if u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= cap:
            break
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--site", default=os.getenv("SITE_DOMAIN", "ntecodex.com"))
    p.add_argument("--max-inspect", type=int, default=40)
    args = p.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id, config from sites where domain=%s", (args.site,))
        row = cur.fetchone()
        if not row:
            print(f"❌ site {args.site!r} not in sites")
            return 2
        site_id, config = row
        niche = (config or {}).get("niche") or "gaming"
        candidates = _candidates(cur, str(site_id), args.site, niche, args.max_inspect)

    if not candidates:
        print("  no published URLs to inspect")
        return 0

    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from src.utils.google_oauth import get_user_credentials

    prop = f"sc-domain:{args.site}"
    svc = build("searchconsole", "v1", credentials=get_user_credentials(), cache_discovery=False)

    inspected = 0
    indexed = 0
    unknown_urls: list[str] = []
    for url in candidates:
        try:
            r = svc.urlInspection().index().inspect(
                body={"inspectionUrl": url, "siteUrl": prop}
            ).execute()
            state = (r.get("inspectionResult", {})
                      .get("indexStatusResult", {}).get("coverageState", "?"))
            inspected += 1
            if state in INDEXED_STATES:
                indexed += 1
            else:
                unknown_urls.append(url)
        except HttpError as e:
            if e.resp.status in (401, 403):
                print(f"  ❌ {e.resp.status} inspecting {args.site} — token/property issue. Stop.")
                return 1
            if e.resp.status == 429:
                print(f"  ⏸ urlInspection quota hit after {inspected}")
                break

    ratio = indexed / inspected if inspected else 0.0
    print(f"📋 {args.site}: inspected {inspected}, indexed {indexed} ({ratio:.0%}), "
          f"{len(unknown_urls)} need attention")

    # ── Record today's indexed-% so /viability can chart the trend and
    # ops_autoflag can detect Day-7 stagnation. Stored under source='gsc'
    # (this IS GSC URL-Inspection data; the source CHECK constraint forbids
    # a new value, so we tag it with a distinct payload key instead — no
    # schema change). Append-only; the readers dedupe to one point/day.
    from datetime import date as _date
    if inspected > 0:
        store_raw(
            site_id, "gsc", _date.today(),
            {"indexing_coverage": {
                "inspected": inspected,
                "indexed": indexed,
                "ratio": round(ratio, 4),
                "unknown": len(unknown_urls),
                "date": _date.today().isoformat(),
            }},
        )
        print(f"  ↳ recorded indexing_coverage to metrics_raw")

    worklist_title = f"Daily GSC request-indexing — {args.site}"
    stop_title = f"GSC indexing healthy (>80%) — {args.site}: stop manual requests"

    # 80% rule: graduate off manual requesting.
    if inspected >= 10 and ratio >= STOP_THRESHOLD:
        resolve_open_task(worklist_title, site_domain=args.site)
        upsert_open_task(
            stop_title,
            f"Inspected sample shows {ratio:.0%} indexed (≥80%). Manual "
            f"request-indexing is no longer worth your time — sitemap "
            f"auto-resubmit + IndexNow now keep discovery fresh. "
            f"Dismiss this card to acknowledge.",
            priority="low", category="seo", site_domain=args.site,
        )
        print(f"  ✓ {args.site} graduated (≥80% indexed) — worklist card resolved")
        return 0

    if not unknown_urls:
        resolve_open_task(worklist_title, site_domain=args.site)
        print(f"  ✓ {args.site}: nothing unknown in sample — worklist card cleared")
        return 0

    top = unknown_urls[:DAILY_REQUEST_CAP]
    numbered = "\n".join(f"  {i+1}. {u}" for i, u in enumerate(top))
    detail = (
        f"{len(top)} URLs Google hasn't indexed yet (inspected sample: "
        f"{ratio:.0%} indexed).\n"
        f"GSC 每天配额约 10-12 个/property, 做完这 {len(top)} 个即可, "
        f"明天继续剩余的。\n\n"
        f"HOW: open search.google.com/search-console → select {args.site} → "
        f"paste each URL in the top search bar → wait for inspection → click "
        f"'Request Indexing'. Then mark this card Done.\n\n"
        f"URLs (clean-tier + newest first):\n{numbered}"
    )
    upsert_open_task(
        worklist_title, detail,
        priority="normal", category="seo-reqidx", site_domain=args.site,
    )
    print(f"  ✓ {args.site}: worklist card refreshed with {len(top)} URLs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
