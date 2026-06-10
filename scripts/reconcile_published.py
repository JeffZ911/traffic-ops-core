"""Reconcile articles.status='published' against LIVE reality.

The DB accumulated rows that say 'published' but whose pages no longer exist
(content purges / repo cleanups left rows behind) — ntecodex sampled ~33%
dead among distinct per-slug URLs. Dead rows poison every metric (publish
counts, per-article joins) and made the IndexNow backlog push advertise dead
URLs. This script:

  1. Fetches the site's live sitemap URL set (the deploy truth).
  2. For each published row with a PER-SLUG url (aggregated hub URLs like
     ntecodex /faq and /tier-list are BY DESIGN many-rows→one-page; skipped):
       - in sitemap            → keep (alive)
       - not in sitemap        → HEAD-check live (sitemap can lag):
            200 direct         → keep (alive, sitemap lag)
            else               → status='archived' (dead; published_url kept
                                  for history, row leaves all 'published' joins)
  3. Reports per-site counts. --dry-run previews without writing.

Usage:
  python -m scripts.reconcile_published --dry-run
  python -m scripts.reconcile_published                  # apply
  python -m scripts.reconcile_published --sites ntecodex.com
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SITEMAPS = {
    "ntecodex.com":   "https://ntecodex.com/sitemap.xml",
    "quvii.com":      "https://quvii.com/sitemap.xml",
    "pixelmatch.art": "https://pixelmatch.art/blog/sitemap.xml",
}
# Aggregated hub URLs: many article rows legitimately share one page.
AGGREGATED = {"/faq", "/tier-list"}
UA = {"User-Agent": "Mozilla/5.0 (reconcile-published)"}


def _fetch(url: str) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _sitemap_paths(domain: str) -> set[str]:
    """Normalized (no trailing slash) path set from the live sitemap."""
    xml = _fetch(SITEMAPS[domain])
    out = set()
    for loc in re.findall(r"<loc>([^<]+)</loc>", xml):
        p = re.sub(r"^https?://[^/]+", "", loc.strip()).rstrip("/") or "/"
        out.add(p)
    return out


def _alive(domain: str, path: str) -> bool:
    """HEAD both slash forms; alive = some form serves 200 directly."""
    base = f"https://{domain}{path.rstrip('/')}"
    for u in (base + "/", base):
        try:
            req = urllib.request.Request(u, method="HEAD", headers=UA)
            with urllib.request.urlopen(req, timeout=12) as r:
                if r.status == 200 and r.geturl() == u:
                    return True
        except Exception:
            continue
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default=",".join(SITEMAPS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    total_dead = 0
    for domain in [s.strip() for s in args.sites.split(",") if s.strip()]:
        if domain not in SITEMAPS:
            print(f"skip {domain}: no sitemap mapping"); continue
        try:
            live_paths = _sitemap_paths(domain)
        except Exception as e:
            print(f"❌ {domain}: sitemap fetch failed ({e}) — skipping site"); continue

        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("select id from sites where domain=%s", (domain,))
            site_id = str(cur.fetchone()[0])
            cur.execute(
                "select id, published_url from articles "
                "where site_id=%s and status='published' and published_url is not null",
                (site_id,),
            )
            rows = cur.fetchall()

        keep = lag_alive = 0
        dead: list[str] = []
        checked_cache: dict[str, bool] = {}
        for aid, pu in rows:
            path = re.sub(r"^https?://[^/]+", "", pu).rstrip("/") or "/"
            if path in AGGREGATED:
                keep += 1; continue
            if path in live_paths:
                keep += 1; continue
            if path not in checked_cache:
                checked_cache[path] = _alive(domain, path)
            if checked_cache[path]:
                lag_alive += 1
            else:
                dead.append(str(aid))

        print(f"▶ {domain}: published rows={len(rows)}  alive(sitemap)={keep}  "
              f"alive(live-check)={lag_alive}  DEAD={len(dead)}")
        total_dead += len(dead)
        if dead and not args.dry_run:
            with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(
                    "update articles set status='archived' where id = any(%s)",
                    (dead,),
                )
            print(f"   ✓ archived {len(dead)} dead 'published' row(s)")

    print(f"\n{'DRY RUN — nothing written. ' if args.dry_run else ''}"
          f"total dead rows: {total_dead}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
