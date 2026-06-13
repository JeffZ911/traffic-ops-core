"""Daily index-coverage monitor — the "曝光天花板" gauge.

The binding constraint on impressions for a young site is not content volume
or quality — it's whether Google has INDEXED the pages at all. An unindexed
page earns exactly zero impressions. This collector samples each site's most
recent published URLs, asks GSC urlInspection for each one's coverageState,
and records the indexed / discovered-not-indexed / unknown split as ONE
metrics_raw row per site per day (payload key 'index_coverage').

The dashboard reads the trailing window of these rows and draws the
"unknown → discovered → indexed" curve per site, so the operator can watch
crawl/indexing progress day over day instead of guessing from impressions
(which lag indexing by days and settle 2-3 days late).

Read-only against GSC (urlInspection) — fully compliant, no write to Google.

Usage:
  python -m scripts.index_coverage                 # all sites
  python -m scripts.index_coverage --site quvii.com --sample 25
"""
from __future__ import annotations

import argparse
import socket
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from src.collectors.base import store_raw
from src.db.client import get_db_connection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DEFAULT_SITES = ("ntecodex.com", "quvii.com", "pixelmatch.art", "imade4u.com")
INDEXED_STATES = {"Submitted and indexed", "Indexed, not submitted in sitemap"}


def _public_url(domain: str, published_url: str) -> str:
    if published_url.startswith("http"):
        return published_url
    path = published_url if published_url.startswith("/") else f"/{published_url}"
    return f"https://{domain}{path}"


def _classify(state: str) -> str:
    if state in INDEXED_STATES:
        return "indexed"
    if "rawl" in state or "iscover" in state:      # crawled / discovered, not indexed
        return "discovered"
    return "unknown"                                # unknown to Google / error / excluded


def run_site(svc, cur, domain: str, sample: int) -> str:
    cur.execute("select id from sites where domain=%s", (domain,))
    row = cur.fetchone()
    if not row:
        return f"{domain}: no site row — skip"
    site_id = str(row[0])
    cur.execute(
        """select a.published_url from articles a
            where a.site_id=%s and a.status='published' and a.published_url is not null
            order by a.published_at desc nulls last limit %s""",
        (site_id, sample),
    )
    urls = [_public_url(domain, r[0]) for r in cur.fetchall()]
    if not urls:
        return f"{domain}: no published URLs — skip"

    prop = f"sc-domain:{domain}"
    counts = {"indexed": 0, "discovered": 0, "unknown": 0}
    for u in urls:
        try:
            ins = svc.urlInspection().index().inspect(
                body={"inspectionUrl": u, "siteUrl": prop}).execute()
            state = (ins.get("inspectionResult", {})
                        .get("indexStatusResult", {}).get("coverageState", "?"))
        except Exception:
            state = "?"
        counts[_classify(state)] += 1

    sampled = sum(counts.values())
    pct = round(100.0 * counts["indexed"] / sampled, 1) if sampled else 0.0
    store_raw(site_id, "gsc", date.today(), {"index_coverage": {
        "indexed": counts["indexed"], "discovered": counts["discovered"],
        "unknown": counts["unknown"], "sampled": sampled, "pct_indexed": pct,
        "at": date.today().isoformat(),
    }})
    return (f"{domain}: indexed {counts['indexed']}/{sampled} ({pct}%) · "
            f"discovered {counts['discovered']} · unknown {counts['unknown']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=None, help="single domain (default: all)")
    ap.add_argument("--sample", type=int, default=25,
                    help="how many of the newest published URLs to inspect")
    args = ap.parse_args()

    # Hard socket timeout so one slow GSC call can never hang the daily cron.
    socket.setdefaulttimeout(30)

    from googleapiclient.discovery import build
    from src.utils.google_oauth import get_user_credentials
    svc = build("searchconsole", "v1", credentials=get_user_credentials(),
                cache_discovery=False)

    sites = [args.site] if args.site else list(DEFAULT_SITES)
    print(f"📑 Index-coverage monitor (sample={args.sample} newest/site)")
    with get_db_connection() as conn, conn.cursor() as cur:
        for domain in sites:
            try:
                print("  ✓", run_site(svc, cur, domain, args.sample))
            except Exception as e:  # noqa: BLE001 — one site must not break the rest
                print(f"  ⚠️  {domain}: {type(e).__name__}: {str(e)[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
