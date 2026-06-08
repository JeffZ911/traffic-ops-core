"""imade4u content pipeline — the QDF loop with a Shopify-blog publish target.

For each run: pick the top planned gift topics from the keyword pool, generate
a distinct image-rich gift guide (real product links + photos), gate on quality,
publish to the Shopify blog, and record the article + advance the keyword. No
Astro, no repo — the publish hop is the Shopify Admin API.

Quality gate (prevents the old thin/duplicate pattern):
  - word_count >= --min-words
  - >= --min-links real product links (build_article already scrubs fakes)
  - title not a near-duplicate of a recently published article

Usage:
  python -m scripts.content_imade4u --count 2            # publish DRAFTS (review)
  python -m scripts.content_imade4u --count 2 --live     # publish LIVE
"""
from __future__ import annotations

import argparse
import re
from datetime import date

from dotenv import load_dotenv

from src.db.client import get_db_connection
from src.integrations.shopify_blog import publish_article
from scripts.gift_sample import build_article

load_dotenv()


def _parse_notes(notes: str) -> dict:
    """notes = 'type=occasion_guide|match=necklace,bracelet|tags=mothers-day,jewelry'"""
    out = {}
    for part in (notes or "").split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=2)
    ap.add_argument("--min-words", type=int, default=700)
    ap.add_argument("--min-links", type=int, default=3)
    ap.add_argument("--model", default="gemini-3.1-pro-preview")
    ap.add_argument("--live", action="store_true", help="publish live (default: draft)")
    args = ap.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain='imade4u.com'")
        row = cur.fetchone()
        if not row:
            print("❌ imade4u tenant missing"); return 2
        site_id = str(row[0])
        cur.execute(
            "select id, keyword, notes from keywords where site_id=%s and status='planned' "
            "order by priority_score desc nulls last, created_at asc limit %s",
            (site_id, args.count),
        )
        picks = cur.fetchall()
        cur.execute("select lower(title) from articles where site_id=%s and status='published'", (site_id,))
        published_titles = {_norm(r[0]) for r in cur.fetchall()}

    if not picks:
        print("  no planned topics — run bootstrap_imade4u or keyword top-up"); return 0

    made = 0
    for kid, topic, notes in picks:
        n = _parse_notes(notes)
        match = [m for m in (n.get("match", "")).split(",") if m]
        tags = [t for t in (n.get("tags", "")).split(",") if t]
        atype = n.get("type", "buying_guide")
        print(f"▶ {topic[:64]}  [{atype}]")

        if _norm(topic) in published_titles:
            print("   ⏭  near-duplicate of a published title — skipping");
            _set_kw(kid, "skipped"); continue

        art = build_article(topic, match, tags, model=args.model)
        if not art:
            print("   ⚠️  generation failed — left planned for retry"); continue
        # quality gate
        if art["word_count"] < args.min_words or art["n_product_links"] < args.min_links:
            print(f"   ❌ QA fail (words={art['word_count']}, links={art['n_product_links']})")
            _set_kw(kid, "planned"); continue

        r = publish_article(
            title=art["title"], content_md=art["body_md"], tags=art["tags"],
            summary=art["summary"], meta_title=art["meta_title"],
            meta_description=art["meta_description"], image_url=art["hero"],
            image_alt=art["title"], published=args.live,
        )
        _record(site_id, kid, art, r, atype, live=args.live)
        made += 1
        state = "LIVE" if args.live else "DRAFT"
        print(f"   ✓ {state}: {art['title']}")
        print(f"     words={art['word_count']} links={art['n_product_links']} "
              f"imgs={art['n_images']} cost=${art['cost_usd']:.4f}")
        print(f"     {r.url if args.live else r.admin_url}")

    print(f"\n  done — {made}/{len(picks)} published ({'live' if args.live else 'drafts'})")
    return 0


def _set_kw(keyword_id, status: str) -> None:
    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("update keywords set status=%s, last_used_at=now() where id=%s",
                    (status, str(keyword_id)))


def _record(site_id, keyword_id, art: dict, r, atype: str, *, live: bool) -> None:
    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into articles (site_id, slug, title, article_type, content_md,
                                  word_count, status, published_url, published_at,
                                  total_tokens, total_cost_usd, qa_attempts)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,0)
            returning id
            """,
            (site_id, r.handle, art["title"], atype, art["body_md"], art["word_count"],
             "published" if live else "qa_passed", r.url,
             date.today() if live else None, art["cost_usd"]),
        )
        aid = cur.fetchone()[0]
        cur.execute(
            "insert into article_keywords (article_id, keyword_id, is_primary) "
            "values (%s,%s,true) on conflict do nothing",
            (str(aid), str(keyword_id)),
        )
        cur.execute("update keywords set status='completed', last_used_at=now() where id=%s",
                    (str(keyword_id),))


if __name__ == "__main__":
    raise SystemExit(main())
