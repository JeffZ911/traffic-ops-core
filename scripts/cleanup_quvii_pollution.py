"""One-shot cleanup of quvii.com pool pollution.

Caused by 2026-05-27 cron run that ran keyword_gardener's affiliate_seed
step with niche-default fallback ('gaming'), generating gaming-themed
"best gaming chair for nte players" keywords into quvii's pool.
KeywordSelector then picked one ("how to adjust nte camera settings")
and produced an off-niche article + images.

This script removes:
  1. The polluted article row (article_type=camera_learn but title is
     about an in-game NTE camera — DELETE FROM articles ... CASCADE)
  2. Its images (images table rows)
  3. Polluted keywords inserted by affiliate_seed before the niche-aware
     fix landed (source='affiliate_seed' on quvii) — these never produced
     useful content; clearing them keeps the pool fully on-topic.
  4. Any keyword in quvii's pool whose text contains gaming tokens
     (nte / neverness / gacha / mmo / jrpg / boss / character build) —
     defense-in-depth in case other gardener calls polluted earlier.

Hard DELETE, no soft-delete. Per operator decision: "保持干净".
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from src.db.client import get_db_connection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


GAMING_TOKEN_PATTERNS = [
    "nte", "neverness", "gacha", "mmo", "jrpg",
    "in-game", "video game", "ingame", "ingame ",
    "boss guide", "character build", "tier list",
    "esper", "hoyoverse", "miHoYo", "honkai", "genshin", "wuthering",
]


def main() -> int:
    with get_db_connection() as conn, conn.cursor() as cur:
        # Resolve quvii site_id
        cur.execute("select id from sites where domain = 'quvii.com' limit 1")
        row = cur.fetchone()
        if not row:
            print("❌ quvii.com not found in sites table"); return 2
        site_id = str(row[0])
        print(f"quvii.com site_id = {site_id}")

        # ── 1. Polluted articles (gaming-themed under camera_* type) ──
        # We identify by title/slug containing gaming tokens. Quvii's
        # legitimate articles never reference NTE / gacha / MMO etc.
        ilike_clauses = " OR ".join([f"lower(coalesce(title,slug)) like '%{p}%'" for p in GAMING_TOKEN_PATTERNS])
        cur.execute(
            f"select id, slug, title, article_type, status from articles "
            f"where site_id = %s and ({ilike_clauses})",
            (site_id,),
        )
        bad_articles = cur.fetchall()
        if bad_articles:
            print(f"\n🗑  {len(bad_articles)} polluted articles to DELETE:")
            for a in bad_articles:
                print(f"   {a[0]}  {a[3]:20s}  {(a[2] or a[1])[:70]}")
            # Cascade: delete dependent rows first (images, agent_runs, article_keywords)
            article_ids = [str(a[0]) for a in bad_articles]
            placeholders = ",".join(["%s"] * len(article_ids))
            cur.execute(f"delete from images where article_id in ({placeholders})", article_ids)
            print(f"   ✓ images: deleted {cur.rowcount}")
            cur.execute(f"delete from article_keywords where article_id in ({placeholders})", article_ids)
            print(f"   ✓ article_keywords: deleted {cur.rowcount}")
            cur.execute(f"delete from agent_runs where article_id in ({placeholders})", article_ids)
            print(f"   ✓ agent_runs: deleted {cur.rowcount}")
            cur.execute(f"delete from articles where id in ({placeholders})", article_ids)
            print(f"   ✓ articles: deleted {cur.rowcount}")
        else:
            print("✓ no polluted articles found")

        # ── 2. Polluted keywords ──
        ilike_kw_clauses = " OR ".join([f"lower(keyword) like '%{p}%'" for p in GAMING_TOKEN_PATTERNS])
        cur.execute(
            f"select id, keyword, source from keywords "
            f"where site_id = %s and ({ilike_kw_clauses})",
            (site_id,),
        )
        bad_keywords = cur.fetchall()
        if bad_keywords:
            print(f"\n🗑  {len(bad_keywords)} polluted keywords to DELETE:")
            for kw in bad_keywords[:10]:
                print(f"   {kw[2]:20s}  {kw[1][:70]}")
            if len(bad_keywords) > 10:
                print(f"   ... + {len(bad_keywords) - 10} more")
            kw_ids = [str(kw[0]) for kw in bad_keywords]
            placeholders = ",".join(["%s"] * len(kw_ids))
            cur.execute(f"delete from article_keywords where keyword_id in ({placeholders})", kw_ids)
            cur.execute(f"delete from keywords where id in ({placeholders})", kw_ids)
            print(f"   ✓ keywords: deleted {cur.rowcount}")
        else:
            print("✓ no polluted keywords found")

        # ── 3. Verify clean state ──
        cur.execute("select count(*) from articles where site_id = %s", (site_id,))
        n_articles = cur.fetchone()[0]
        cur.execute("select count(*) from keywords where site_id = %s and status = 'planned'", (site_id,))
        n_planned = cur.fetchone()[0]
        print(f"\n📊 After cleanup:")
        print(f"   articles remaining (all status): {n_articles}")
        print(f"   keywords planned (ready to pick): {n_planned}")

    print("\n✓ cleanup complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
