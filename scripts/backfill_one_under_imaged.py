"""Find ONE published article with fewer than 4 inline images and backfill.

Designed to run as a tail step in the daily cron — it touches at most
one article per day so the cost stays bounded (~$0.27 / day max with
the +6 inline budget). The retrofit workflow (Phase 1.B) handled the
initial bulk; this script is the steady-state maintenance backstop for
any article that for whatever reason ends up with the legacy 1+2 image
layout.

Selection logic:
  1. List every status='published' article for the site.
  2. For each, count rows in the `images` table tagged to that article_id.
  3. Skip articles with image_count >= 4 (1 hero + ≥3 inline = healthy).
  4. Among the rest, pick the OLDEST by published_at — older content
     has been search-indexed the longest, so backfilling it has the
     biggest near-term SEO impact.
  5. Re-run ImageAgent with --force-regenerate semantics by deleting
     the article's existing rows from `images` first, then calling
     run_image_for_articles for that single slug.

Idempotent across crons: once an article has ≥4 images, it stops being
picked.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--budget-usd", type=float, default=0.40,
                   help="Pass through to run_image_for_articles")
    p.add_argument("--min-images", type=int, default=4,
                   help="Below this image count, article is a backfill candidate")
    p.add_argument("--inline", type=int, default=6,
                   help="Pass through; total = 1 hero + this many inline")
    args = p.parse_args()

    site_repo = os.getenv("SITE_REPO_PATH")
    if not site_repo:
        print("❌ SITE_REPO_PATH env var not set")
        return 2

    site_domain = os.getenv("SITE_DOMAIN", "ntecodex.com")
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select id from sites where domain = %s limit 1", (site_domain,)
        )
        row = cur.fetchone()
        if not row:
            print(f"❌ site {site_domain!r} not in sites")
            return 2
        site_id = row[0]

        # Articles with their image count, oldest first
        cur.execute(
            """
            select a.id, a.slug, a.article_type, a.published_at,
                   coalesce(c.cnt, 0) as image_count
              from articles a
              left join (
                  select article_id, count(*) as cnt
                    from images
                   group by article_id
              ) c on c.article_id = a.id
             where a.site_id = %s
               and a.status = 'published'
               and coalesce(c.cnt, 0) < %s
             order by a.published_at asc
             limit 1
            """,
            (str(site_id), args.min_images),
        )
        row = cur.fetchone()

    if not row:
        print(f"✓ all published articles already have ≥{args.min_images} images")
        return 0

    article_id, slug, atype, published_at, cnt = row
    print(f"🩹 Backfill candidate: {slug}")
    print(f"   type={atype}  published_at={published_at}  current_image_count={cnt}")

    # Clear stale image rows so the retrofit re-uploads them cleanly.
    # The on-disk WebP files will be overwritten by ImageAgent (it writes
    # to the same hero.webp / inline-N.webp paths).
    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "delete from images where article_id = %s",
            (str(article_id),),
        )
        deleted = cur.rowcount or 0
        print(f"   cleared {deleted} stale image row(s) from DB")

    # Invoke the existing retrofit path for this single slug. We bound
    # the run by --limit 1 + an additional safety filter: the script
    # already skips articles whose hero exists, so we add --force-regenerate
    # to ensure regeneration. We also rely on the article ordering — our
    # chosen article has the OLDEST published_at, so `--limit 1` lands
    # on it if it now has 0 images post-delete.
    #
    # Pass the SITE_REPO_PATH through; the existing run_image_for_articles
    # reads it from env.
    cmd = [
        sys.executable, "-m", "scripts.run_image_for_articles",
        "--force-regenerate",
        "--slug", slug,
        "--inline", str(args.inline),
        "--limit", "1",
        "--budget-usd", str(args.budget_usd),
    ]
    print(f"   → running: {' '.join(cmd)}")
    rc = subprocess.call(cmd)
    if rc != 0:
        print(f"⚠️  retrofit returned rc={rc}")
        return rc

    return 0


if __name__ == "__main__":
    sys.exit(main())
