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
from scripts.gift_sample import build_article, _products

load_dotenv()


_QA_PROMPT = """You are the factual-safety reviewer for iMade4U's gift blog —
the blog of a REAL revenue-bearing store, so a fabricated claim costs trust
and risks Google quality penalties. Today's date: {today}.

ARTICLE TOPIC: "{topic}"

THE WRITER WAS ONLY GIVEN these real products (title — handle), nothing more
(no prices, no materials beyond what titles say, no inventory):
{products}

ARTICLE (markdown):
{body}

Review STRICTLY for:
1. FABRICATED EXTERNAL FACTS — any specific celebrity moment, named viral
   trend presented as a documented fact, statistic, study, or news event
   stated affirmatively. General style/aesthetic talk ("minimalist jewelry is
   trending") is fine; "as seen on <celebrity> at <event>" or "searches grew
   240%" is a fabrication unless hedged as opinion.
2. INVENTED PRODUCT ATTRIBUTES — specific prices, materials, dimensions,
   shipping promises, or features NOT derivable from the product titles above.
3. INTENT MATCH — does the article actually deliver what the topic promises?
4. QUALITY — distinct, specific gift ideas; no filler repetition.

Reply ONLY with JSON (no fence):
{{"factual_safety": <0-10>, "intent_match": <0-10>, "quality": <0-10>,
  "hard_fail": <true if ANY fabricated external fact or invented price/spec>,
  "issues": ["<specific issue>", ...]}}
"""


def _qa_gate(topic: str, body_md: str, product_lines: str,
             model: str = "gemini-3.1-pro-preview") -> dict:
    """LLM factual-safety gate (the missing third link of the chain:
    热点选词 + AI复盘闭环 + 事实门). Returns the verdict dict; on any LLM
    error returns a conservative FAIL so an unreviewed article never ships."""
    import json as _json
    from datetime import date as _date
    from src.utils.llm import get_llm_provider
    try:
        resp = get_llm_provider("gemini").generate(
            prompt=_QA_PROMPT.format(today=_date.today().isoformat(), topic=topic,
                                     products=product_lines, body=body_md[:24000]),
            model=model, max_tokens=2800, temperature=0.1, json_mode=True)
        t = (resp.text or "").strip()
        if t.startswith("```"):
            t = t.split("\n", 1)[-1].rsplit("```", 1)[0]
        i, j = t.find("{"), t.rfind("}")
        v = _json.loads(t[i:j + 1])
        v["cost_usd"] = float(resp.cost_usd or 0)
        return v
    except Exception as e:  # noqa: BLE001
        return {"factual_safety": 0, "intent_match": 0, "quality": 0,
                "hard_fail": True, "issues": [f"QA gate error: {type(e).__name__}"],
                "cost_usd": 0.0}


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
        # 20h cooldown on recently-attempted topics: a topic that just failed
        # the QA gate would otherwise stay top-of-queue and block every run.
        cur.execute(
            "select id, keyword, notes from keywords where site_id=%s and status='planned' "
            "and (last_used_at is null or last_used_at < now() - interval '20 hours') "
            # PRIMARY SOURCE = the external keyword-strategy library: consume it
            # first (real GSC-backed quick-wins), self-generated topics only after
            # it drains. Then the freshness bonus for seasonal/trend topics.
            "order by (source='external_strategy') desc, "
            "(coalesce(priority_score,0) + case when source in ('trend','gift_seasonal') then "
            "  case when created_at >= now()-interval '1 day' then 150 "
            "       when created_at >= now()-interval '3 days' then 80 "
            "       when created_at >= now()-interval '7 days' then 30 else 0 end "
            "  else 0 end) desc, created_at asc limit %s",
            # Over-fetch: many planned topics target product categories the
            # catalog doesn't stock (canvas/wall-art/keychain) and get skipped
            # by the catalog-reality gate below, so we need a deeper candidate
            # pool to still reach `count` viable topics in one run.
            (site_id, max(args.count * 6, 12)),
        )
        picks = cur.fetchall()
        cur.execute("select lower(title) from articles where site_id=%s and status='published'", (site_id,))
        published_titles = {_norm(r[0]) for r in cur.fetchall()}
        # Sibling guides for internal linking — build the blog link mesh that
        # nudges the pos-10-18 pages toward page 1.
        cur.execute("""select title, published_url from articles
                        where site_id=%s and status='published' and published_url is not null
                        order by published_at desc limit 30""", (site_id,))
        siblings = [{"title": t, "url": u if u.startswith("http") else "https://imade4u.com" + u}
                    for t, u in cur.fetchall()]

    if not picks:
        print("  no planned topics — run bootstrap_imade4u or keyword top-up"); return 0

    made = 0
    skipped_catalog = 0
    for kid, topic, notes in picks:
        if made >= args.count:
            break
        n = _parse_notes(notes)
        match = [m for m in (n.get("match", "")).split(",") if m]
        tags = [t for t in (n.get("tags", "")).split(",") if t]
        atype = n.get("type", "buying_guide")
        print(f"▶ {topic[:64]}  [{atype}]")

        if _norm(topic) in published_titles:
            print("   ⏭  near-duplicate of a published title — skipping");
            _set_kw(kid, "skipped"); continue

        # EXTERNAL-STRATEGY handle lock: if the keyword carries a trusted
        # target_product_handle (notes handle=…), resolve that exact product and
        # make it the PRIMARY product the article is built around — bypassing the
        # fuzzy match= picker. A resolved handle also satisfies catalog-reality
        # (we have a guaranteed real product), so skip the recall gate.
        pin = None
        if n.get("handle"):
            try:
                from src.integrations.shopify_product import get_product_by_handle
                p = get_product_by_handle(n["handle"])
                if p and p.get("status") == "active":
                    pin = p
            except Exception:
                pin = None

        # CATALOG-REALITY gate (pre-generation). A topic whose match terms recall
        # fewer than min_links REAL products can NEVER pass the post-write link
        # gate — generating it just burns an LLM call and leaves the pipeline
        # publishing nothing (imade4u went silent 06-17 this way: the keyword
        # generator drifted into categories we don't stock — canvas, wall-art,
        # keychains). Skip such topics up front so the run reaches a viable one.
        # A pinned handle already guarantees a real product → skip the gate.
        if pin is None:
            recall = len(_products(match)) if match else 0
            if recall < args.min_links:
                print(f"   ⏭  catalog can't support it (recall={recall} < {args.min_links} real products) — skipping")
                _set_kw(kid, "skipped"); skipped_catalog += 1; continue

        # Rank the RIGHT products to the top. The stored match is a broad
        # category ("necklace"); for a specific title ("Photo Projection
        # Necklaces") that ranks generic name necklaces level with the actual
        # photo-projection product, so the writer features the wrong items and
        # the intent gate fails. Folding the topic into the match adds its
        # distinctive token ("projection") so _products' hit-count ranking
        # floats the on-topic products first. Recall gate stays on the broad
        # term above (so we don't let no-stock topics like "canvas" back in).
        match_ranked = match + [topic]
        # pass sibling guides (excluding this very topic) for internal linking
        rel = [s for s in siblings if _norm(s["title"]) != _norm(topic)][:8]
        # external quick-win keyword → put its exact phrase in the title/H1
        keyword = topic if n.get("type") == "external" else None
        art = build_article(topic, match_ranked, tags, model=args.model, related=rel,
                            pin_product=pin, keyword=keyword)
        if not art:
            print("   ⚠️  generation failed — left planned for retry"); continue
        # mechanical gate
        if art["word_count"] < args.min_words or art["n_product_links"] < args.min_links:
            print(f"   ❌ QA fail (words={art['word_count']}, links={art['n_product_links']})")
            _set_kw(kid, "planned"); continue
        # FACTUAL-SAFETY gate (LLM) — the third link of the chain (热点选词 +
        # AI复盘闭环 + 事实门). Blocks fabricated celebrity/trend/statistic
        # claims and invented product prices/specs from reaching the live
        # revenue store. Conservative: gate errors = fail.
        from scripts.gift_sample import _products as _prods
        # Same enriched ranking the writer saw, so the gate judges intent
        # against the on-topic products (not the generic-category recall).
        plines = "\n".join(f"  - {p['title']} — {p['handle']}" for p in _prods(match_ranked))
        verdict = _qa_gate(topic, art["body_md"], plines)
        fs, im, q = (verdict.get("factual_safety", 0), verdict.get("intent_match", 0),
                     verdict.get("quality", 0))
        if verdict.get("hard_fail") or fs < 7 or im < 6:
            print(f"   ❌ 事实门 FAIL (safety={fs} intent={im} quality={q}): "
                  f"{'; '.join(verdict.get('issues', [])[:2])[:140]}")
            _set_kw(kid, "planned"); continue
        print(f"   ✅ 事实门 pass (safety={fs} intent={im} quality={q} "
              f"${verdict.get('cost_usd', 0):.4f})")

        r = publish_article(
            title=art["title"], content_md=art["body_md"], tags=art["tags"],
            summary=art["summary"], meta_title=art["meta_title"],
            meta_description=art["meta_description"], image_url=art["hero"],
            image_alt=art["title"], published=args.live,
            products=art.get("products"),
        )
        _record(site_id, kid, art, r, atype, live=args.live, qa=verdict)
        made += 1
        state = "LIVE" if args.live else "DRAFT"
        print(f"   ✓ {state}: {art['title']}")
        print(f"     words={art['word_count']} links={art['n_product_links']} "
              f"imgs={art['n_images']} cost=${art['cost_usd']:.4f}")
        print(f"     {r.url if args.live else r.admin_url}")

    print(f"\n  done — {made}/{args.count} published ({'live' if args.live else 'drafts'})"
          f"{f', {skipped_catalog} skipped (catalog gap)' if skipped_catalog else ''}")
    return 0


def _set_kw(keyword_id, status: str) -> None:
    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("update keywords set status=%s, last_used_at=now() where id=%s",
                    (status, str(keyword_id)))


def _record(site_id, keyword_id, art: dict, r, atype: str, *, live: bool,
            qa: dict | None = None) -> None:
    import json as _json
    qa_score = None
    qa_feedback = None
    if qa:
        qa_score = round((qa.get("factual_safety", 0) + qa.get("intent_match", 0)
                          + qa.get("quality", 0)) / 3.0, 1)
        qa_feedback = _json.dumps({k: qa[k] for k in
                                   ("factual_safety", "intent_match", "quality",
                                    "hard_fail", "issues") if k in qa})
    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into articles (site_id, slug, title, article_type, content_md,
                                  word_count, status, published_url, published_at,
                                  total_tokens, total_cost_usd, qa_attempts,
                                  qa_score, qa_feedback)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,1,%s,%s::jsonb)
            returning id
            """,
            (site_id, r.handle, art["title"], atype, art["body_md"], art["word_count"],
             "published" if live else "qa_passed", r.url,
             date.today() if live else None, art["cost_usd"], qa_score, qa_feedback),
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
