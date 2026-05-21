"""Re-submit a site's sitemap to Google Search Console.

Why this exists
---------------
GSC re-downloads a sitemap on its own schedule, which for a young /
low-authority domain can be *weeks* apart. ntecodex's sitemap was
submitted once on 2026-05-13 (42 URLs) and Google never re-fetched it
— so 146 of 188 live URLs are "unknown to Google". Calling
sitemaps.submit() forces an immediate re-download, surfacing every new
URL to Googlebot's discovery queue.

This is the ONE legitimate, Google-blessed API lever for "make Google
re-discover my content". (The old google.com/ping?sitemap= endpoint was
deprecated in 2023; the URL Inspection API is read-only and cannot
request indexing; the Indexing API is officially JobPosting/Broadcast
only.) Sitemap re-submit + internal links + backlinks is the real
discovery toolkit.

Requires WRITE scope: https://www.googleapis.com/auth/webmasters
(NOT .readonly). If the refresh token only has readonly, this 403s with
a clear message — re-run scripts.oauth_setup to upgrade.

Usage:
    python -m scripts.resubmit_sitemap --site ntecodex.com
    python -m scripts.resubmit_sitemap --site pixelmatch.art
    python -m scripts.resubmit_sitemap --all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _sitemap_url_for(domain: str, config: dict) -> str:
    """Where the sitemap actually lives.

    Ecommerce sites deploy under blog.<domain> (Astro base=/blog), so
    their sitemap is at https://blog.<domain>/sitemap.xml. Gaming sites
    serve at the apex.
    """
    niche = (config or {}).get("niche") or "gaming"
    if niche == "ecommerce_tools":
        return f"https://blog.{domain}/sitemap.xml"
    return f"https://{domain}/sitemap.xml"


def _gsc_property_for(domain: str) -> str:
    # Domain-property form covers apex + all subdomains.
    return f"sc-domain:{domain}"


def resubmit(domain: str, config: dict) -> int:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from src.utils.google_oauth import get_user_credentials, WRITE_SCOPES

    sitemap_url = _sitemap_url_for(domain, config)
    prop = _gsc_property_for(domain)

    creds = get_user_credentials(scopes=WRITE_SCOPES)
    svc = build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    # Submit (idempotent — re-submitting an existing sitemap just bumps
    # lastSubmitted and triggers a re-download).
    try:
        svc.sitemaps().submit(siteUrl=prop, feedpath=sitemap_url).execute()
    except HttpError as e:
        if e.resp.status in (401, 403):
            print(f"  ❌ {domain}: {e.resp.status} — token lacks WRITE scope "
                  f"(webmasters, not .readonly). Re-run scripts.oauth_setup "
                  f"and update GOOGLE_OAUTH_REFRESH_TOKEN.")
            return 1
        print(f"  ❌ {domain}: submit failed {e.resp.status} {str(e)[:120]}")
        return 1

    # Read back the freshly-updated submission record.
    try:
        info = svc.sitemaps().get(siteUrl=prop, feedpath=sitemap_url).execute()
        last_sub = info.get("lastSubmitted", "?")
        contents = info.get("contents", [{}])
        n = contents[0].get("submitted", "?") if contents else "?"
        print(f"  ✓ {domain}: re-submitted {sitemap_url}")
        print(f"      lastSubmitted={last_sub[:19] if last_sub != '?' else '?'}  "
              f"declared_urls={n}")
    except HttpError:
        print(f"  ✓ {domain}: submitted {sitemap_url} (readback skipped)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--site", help="Single site domain (e.g. ntecodex.com)")
    p.add_argument("--all", action="store_true", help="All active sites")
    args = p.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        if args.all:
            cur.execute("select domain, config from sites where status='active' order by domain")
            rows = cur.fetchall()
        elif args.site:
            cur.execute("select domain, config from sites where domain=%s", (args.site,))
            rows = cur.fetchall()
        else:
            # Default: SITE_DOMAIN env (cron passes it), else ntecodex.
            import os
            dom = os.getenv("SITE_DOMAIN", "ntecodex.com")
            cur.execute("select domain, config from sites where domain=%s", (dom,))
            rows = cur.fetchall()

    if not rows:
        print("  no matching sites")
        return 2

    print(f"📤 Re-submitting {len(rows)} sitemap(s) to GSC")
    rc = 0
    for domain, config in rows:
        rc |= resubmit(domain, config or {})
    return rc


if __name__ == "__main__":
    sys.exit(main())
