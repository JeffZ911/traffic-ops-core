"""Submit URLs to IndexNow (Bing, Yandex, Seznam, Naver, DuckDuckGo).

Why this complements the sitemap
--------------------------------
IndexNow is a push protocol: instead of waiting for a crawler to
re-read a sitemap, you POST the changed URLs and participating engines
fetch them quickly. Google does NOT participate (they declined), but
Bing does — and Bing's index powers DuckDuckGo, ChatGPT Search, and
Copilot, so it's meaningful discovery surface for AI-era search.

Harmless to Google: IndexNow lives entirely on the Bing/Yandex side;
it neither helps nor hurts Google ranking.

How it works
------------
1. A 32-char key is stored in sites.config.indexnow_key.
2. The key is hosted at https://<host>/<key>.txt (committed in the
   site repo's public/). Engines fetch it to verify ownership.
3. This script POSTs {host, key, keyLocation, urlList} to
   api.indexnow.org. One submit covers all participating engines.

Usage:
    python -m scripts.indexnow_submit --site ntecodex.com            # today's articles
    python -m scripts.indexnow_submit --site pixelmatch.art --all    # every published URL
    python -m scripts.indexnow_submit --site ntecodex.com --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"
# IndexNow accepts up to 10,000 URLs per request.
MAX_URLS = 10_000


def _host_for(domain: str, niche: str) -> str:
    """The host that serves both the content and the key file."""
    return f"blog.{domain}" if niche == "ecommerce_tools" else domain


def _public_url(host: str, published_url: str, niche: str) -> str:
    if published_url.startswith("http"):
        return published_url
    path = published_url if published_url.startswith("/") else f"/{published_url}"
    if niche == "ecommerce_tools":
        prefix = "" if path.startswith("/blog") else "/blog"
        return f"https://{host}{prefix}{path}"
    return f"https://{host}{path}"


def collect_urls(cur, site_id: str, host: str, niche: str, all_urls: bool) -> list[str]:
    if all_urls:
        cur.execute(
            "select published_url from articles "
            "where site_id=%s and status='published' and published_url is not null",
            (site_id,),
        )
    else:
        cur.execute(
            "select published_url from articles "
            "where site_id=%s and status='published' and published_url is not null "
            "and published_at::date = %s",
            (site_id, date.today().isoformat()),
        )
    seen, urls = set(), []
    for (pu,) in cur.fetchall():
        u = _public_url(host, pu, niche)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls[:MAX_URLS]


def submit(host: str, key: str, urls: list[str]) -> int:
    body = json.dumps({
        "host": host,
        "key": key,
        "keyLocation": f"https://{host}/{key}.txt",
        "urlList": urls,
    }).encode()
    req = urllib.request.Request(
        INDEXNOW_ENDPOINT, data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            # 200 or 202 = accepted.
            return r.getcode()
    except urllib.error.HTTPError as e:
        print(f"  ❌ IndexNow {e.code}: {e.read().decode()[:160]}")
        return e.code


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--site", default=os.getenv("SITE_DOMAIN", "ntecodex.com"))
    p.add_argument("--all", action="store_true", help="Submit every published URL (not just today's)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id, config from sites where domain=%s", (args.site,))
        row = cur.fetchone()
        if not row:
            print(f"❌ site {args.site!r} not in sites")
            return 2
        site_id, config = row
        cfg = config or {}
        key = cfg.get("indexnow_key")
        niche = cfg.get("niche") or "gaming"
        if not key:
            print(f"  ⚠️  {args.site}: no indexnow_key in sites.config — skipping")
            return 0
        host = _host_for(args.site, niche)
        urls = collect_urls(cur, str(site_id), host, niche, args.all)

    print(f"📨 IndexNow {args.site}: {len(urls)} URL(s) "
          f"({'all' if args.all else 'today'}) → host={host}")
    if not urls:
        print("  nothing to submit")
        return 0
    if args.dry_run:
        for u in urls[:10]:
            print(f"   [dry] {u}")
        if len(urls) > 10:
            print(f"   ... +{len(urls)-10} more")
        return 0

    code = submit(host, key, urls)
    if code in (200, 202):
        print(f"  ✓ accepted ({code}) — Bing/Yandex/DuckDuckGo will fetch these")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
