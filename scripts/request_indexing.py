"""Nudge Google to crawl specific URLs via the Indexing API.

⚠️  HONEST CAVEAT — read before relying on this
------------------------------------------------
There is NO public Google API to "Request Indexing" the way the GSC UI
button does. The options are:

  1. URL Inspection API  → READ-ONLY. Tells you a URL's index status;
     cannot request indexing. We use it here only to PICK targets
     (skip already-indexed URLs).

  2. Indexing API (indexing.googleapis.com) → can publish a
     urlNotification that nudges Googlebot to crawl a URL within hours.
     OFFICIALLY it supports only JobPosting + BroadcastEvent structured
     data. Using it for general content pages is a GRAY AREA — it
     usually works to trigger a crawl, but Google does not guarantee
     indexing and could ignore (or, in theory, act against) misuse.

The GREEN, fully-supported levers for "make Google discover content"
are: sitemap re-submit (scripts/resubmit_sitemap.py) + internal links
+ external backlinks. This script is the gray-area accelerator on top.

Requirements:
  - A service account JSON in env GOOGLE_SERVICE_ACCOUNT_JSON (or
    <SLUG>_GOOGLE_SERVICE_ACCOUNT_JSON), added as an Owner of the GSC
    property, with the Indexing API enabled in its GCP project.
  - Scope: https://www.googleapis.com/auth/indexing

Priority order for which URLs to nudge (daily quota ~200 default):
  1. Articles published today (freshest)
  2. clean-tier articles never seen by Google (highest quality first)
  3. any historical article currently "unknown to Google"

Usage:
  python -m scripts.request_indexing --site ntecodex.com --limit 150
  python -m scripts.request_indexing --site pixelmatch.art --limit 50
  python -m scripts.request_indexing --site ntecodex.com --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

INDEXING_SCOPE = "https://www.googleapis.com/auth/indexing"


def _service_account_json(site_slug: str) -> dict | None:
    """Find the service-account JSON: per-site env first, then generic."""
    for key in (f"{site_slug.upper()}_GOOGLE_SERVICE_ACCOUNT_JSON",
                "GOOGLE_SERVICE_ACCOUNT_JSON"):
        raw = os.getenv(key)
        if raw and raw.strip():
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                # Some setups store the path instead of the JSON body
                p = Path(raw)
                if p.exists():
                    return json.loads(p.read_text())
    return None


def _public_url(domain: str, published_url: str, niche: str) -> str:
    if published_url.startswith("http"):
        return published_url
    path = published_url if published_url.startswith("/") else f"/{published_url}"
    if niche == "ecommerce_tools":
        host = f"blog.{domain}"
        prefix = "" if path.startswith("/blog") else "/blog"
        return f"https://{host}{prefix}{path}"
    return f"https://{domain}{path}"


def _pick_targets(cur, site_id: str, domain: str, niche: str, limit: int) -> list[str]:
    """Build the prioritized URL list (today → clean tier → others)."""
    from datetime import date
    today = date.today().isoformat()
    cur.execute(
        """
        select published_url,
               coalesce(qa_feedback->>'editorial_tier','') as tier,
               (published_at::date = %s) as is_today,
               published_at
          from articles
         where site_id = %s and status='published' and published_url is not null
        """,
        (today, site_id),
    )
    rows = cur.fetchall()

    def rank(r) -> tuple:
        _url, tier, is_today, _pub = r
        return (
            0 if is_today else 1,             # today first
            0 if tier == "clean" else 1,      # clean next
            0 if tier == "note" else 1,       # then note
        )

    rows.sort(key=rank)
    urls: list[str] = []
    seen = set()
    for published_url, _tier, _today, _pub in rows:
        u = _public_url(domain, published_url, niche)
        if u not in seen:
            seen.add(u)
            urls.append(u)
        if len(urls) >= limit:
            break
    return urls


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--site", default=os.getenv("SITE_DOMAIN", "ntecodex.com"))
    p.add_argument("--limit", type=int, default=150)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id, config from sites where domain=%s", (args.site,))
        row = cur.fetchone()
        if not row:
            print(f"❌ site {args.site!r} not in sites")
            return 2
        site_id, config = row
        niche = (config or {}).get("niche") or "gaming"
        slug = (config or {}).get("site_slug") or args.site.split(".")[0]
        targets = _pick_targets(cur, str(site_id), args.site, niche, args.limit)

    print(f"🔎 {args.site}: {len(targets)} candidate URL(s) (limit {args.limit})")
    if args.dry_run:
        for u in targets[:15]:
            print(f"   [dry] {u}")
        if len(targets) > 15:
            print(f"   ... +{len(targets)-15} more")
        return 0

    sa = _service_account_json(slug)
    if not sa:
        print(f"  ⚠️  no service account JSON ({slug.upper()}_GOOGLE_SERVICE_ACCOUNT_JSON "
              f"or GOOGLE_SERVICE_ACCOUNT_JSON). Skipping — sitemap re-submit + "
              f"internal links are the primary levers; this accelerator is optional.")
        return 0

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as e:
        print(f"  ⚠️  missing dep: {e}")
        return 0

    creds = service_account.Credentials.from_service_account_info(
        sa, scopes=[INDEXING_SCOPE]
    )
    svc = build("indexing", "v3", credentials=creds, cache_discovery=False)

    ok = 0
    failed = 0
    for u in targets:
        try:
            svc.urlNotifications().publish(
                body={"url": u, "type": "URL_UPDATED"}
            ).execute()
            ok += 1
        except HttpError as e:
            failed += 1
            if e.resp.status in (401, 403):
                print(f"  ❌ {e.resp.status} — service account not authorized for "
                      f"Indexing API / not a GSC Owner. Stopping.")
                break
            if e.resp.status == 429:
                print(f"  ⏸ quota exhausted after {ok} URLs.")
                break
        except Exception as e:  # noqa: BLE001
            failed += 1

    print(f"  ✓ nudged {ok} URLs  (failed {failed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
