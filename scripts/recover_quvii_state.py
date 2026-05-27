"""One-shot recovery for quvii.com after multiple cancelled cron runs.

State problems addressed:
  1. Three cancelled crons each ran publish_articles (which marks DB
     status='published') BEFORE the Commit+push step. Cancellation killed
     the runner before push, leaving DB rows marked 'published' but the
     site git repo + CF Pages output empty of articles. This script
     rolls those rows back to 'qa_passed' so the next cron's
     publish_articles step re-emits the markdown + commits it.

  2. Three articles slipped through with NTE / gaming-themed slugs
     despite the niche being security_cameras. They (+ their images
     + agent_runs + article_keywords) are hard-deleted, plus any
     polluted keywords still in the pool.

  3. The daily article cap counter is implicit — it counts ALL articles
     created today regardless of status. Deleting the 3 polluted rows
     frees up cap headroom so the next cron can generate new content.

Run locally with QUVII_GEMINI_API_KEY in .env. Hard DELETE; no soft state.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from src.db.client import get_db_connection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


GAMING_TOKEN_PATTERNS = [
    "nte", "neverness", "gacha", "mmo", "jrpg",
    "in-game", "video game", "ingame",
    "boss guide", "character build", "tier list",
    "esper", "hoyoverse", "honkai", "genshin", "wuthering",
]


def main() -> int:
    with get_db_connection() as conn, conn.cursor() as cur:
        # Resolve quvii site_id
        cur.execute("select id from sites where domain = 'quvii.com' limit 1")
        row = cur.fetchone()
        if not row:
            print("❌ quvii.com not found"); return 2
        site_id = str(row[0])
        print(f"quvii.com site_id = {site_id}\n")

        # ── 1. Polluted articles (gaming-themed slugs/titles) ──
        like_patterns = [f"%{p}%" for p in GAMING_TOKEN_PATTERNS]
        like_sql = " OR ".join(["lower(coalesce(title,slug)) like %s"] * len(like_patterns))
        cur.execute(
            f"select id, slug, status, qa_score from articles "
            f"where site_id = %s and ({like_sql})",
            (site_id, *like_patterns),
        )
        bad_articles = cur.fetchall()
        if bad_articles:
            print(f"🗑  {len(bad_articles)} polluted articles to DELETE:")
            for a in bad_articles:
                print(f"   {a[0]}  {a[2]:12s}  qa={a[3] or '—':>4}  {a[1]}")
            article_ids = [str(a[0]) for a in bad_articles]
            placeholders = ",".join(["%s"] * len(article_ids))
            cur.execute(f"delete from images where article_id in ({placeholders})", article_ids)
            print(f"   ✓ images: -{cur.rowcount}")
            cur.execute(f"delete from article_keywords where article_id in ({placeholders})", article_ids)
            print(f"   ✓ article_keywords: -{cur.rowcount}")
            cur.execute(f"delete from agent_runs where article_id in ({placeholders})", article_ids)
            print(f"   ✓ agent_runs: -{cur.rowcount}")
            cur.execute(f"delete from articles where id in ({placeholders})", article_ids)
            print(f"   ✓ articles: -{cur.rowcount}")
        else:
            print("✓ no polluted articles")

        # ── 2. Roll back 'published' status on non-polluted articles ──
        # publish_articles selects status='qa_passed' AND published_at IS NULL.
        # Cancelled crons set status='published' + published_at=now() in DB but
        # the commit/push step never landed the markdown. Re-arm them.
        cur.execute(
            """
            update articles
               set status = 'qa_passed',
                   published_at = null,
                   published_url = null
             where site_id = %s
               and status = 'published'
               and created_at >= (now() at time zone 'utc')::date
               and id not in (
                 select article_id from images
                  where article_id is not null
                  group by article_id
                  having count(*) >= 6
                  limit 0
               )
            returning id, slug, qa_score
            """,
            (site_id,),
        )
        rearmed = cur.fetchall()
        if rearmed:
            print(f"\n🔁 {len(rearmed)} articles re-armed for publish (status → qa_passed):")
            for r in rearmed:
                print(f"   {r[1]:50s} qa={r[2] or '—'}")
        else:
            print("\n✓ no published-but-not-shipped articles to re-arm")

        # ── 3. Polluted keywords ──
        like_kw_sql = " OR ".join(["lower(keyword) like %s"] * len(like_patterns))
        cur.execute(
            f"select id, keyword, source from keywords "
            f"where site_id = %s and ({like_kw_sql})",
            (site_id, *like_patterns),
        )
        bad_keywords = cur.fetchall()
        if bad_keywords:
            print(f"\n🗑  {len(bad_keywords)} polluted keywords to DELETE:")
            for kw in bad_keywords[:8]:
                print(f"   [{kw[2]}] {kw[1][:60]}")
            if len(bad_keywords) > 8:
                print(f"   ... + {len(bad_keywords) - 8} more")
            kw_ids = [str(kw[0]) for kw in bad_keywords]
            placeholders = ",".join(["%s"] * len(kw_ids))
            cur.execute(f"delete from article_keywords where keyword_id in ({placeholders})", kw_ids)
            cur.execute(f"delete from keywords where id in ({placeholders})", kw_ids)
            print(f"   ✓ keywords: -{cur.rowcount}")
        else:
            print("\n✓ no polluted keywords")

        # ── 4. Verify post-state ──
        cur.execute(
            """
            select status, count(*) from articles where site_id = %s
              and created_at >= (now() at time zone 'utc')::date
            group by status order by status
            """,
            (site_id,),
        )
        print("\n📊 articles created today (UTC) — post-recovery:")
        for r in cur.fetchall():
            print(f"   {r[0]:15s} {r[1]}")

        cur.execute(
            "select count(*) from keywords where site_id = %s and status = 'planned'",
            (site_id,),
        )
        print(f"\n📊 planned keywords ready to pick: {cur.fetchone()[0]}")

    print("\n✓ recovery complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
