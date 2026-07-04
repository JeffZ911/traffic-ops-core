"""imade4u PRODUCT-page SEO harvester — the Tier-1 win.

imade4u's PRODUCT pages hold pos 1 while blog pages sit pos 6-19, so the money
page is the PDP. This is the store analogue of ctr_optimizer (which serves the
Astro MEDIA sites): take the GSC queries already earning impressions but ranking
in the LAGGARD band (pos 15-70) that name an in-stock product ATTRIBUTE, resolve
each to its Shopify product, and rewrite that product's SEO title_tag /
description_tag to front-load the exact attribute long-tail it's appearing for.

SAFETY (these are LIVE revenue pages):
- DRAFT-FIRST. Default is PROPOSE-only: it writes NOTHING to Shopify, stores each
  proposal in metrics_raw ('product_seo_proposal') and prints the before/after.
  Only `--apply` performs the write (needs the write_products scope).
- NEVER the merchandising title (buyer-facing name) — only the SEO metafields.
- NEVER a page ranking pos < --protect-above (default 10) — protect the winners.
- 30-day per-product cooldown (metrics_raw 'product_seo_rewrite').

Usage:
  python -m scripts.product_ctr_optimizer                 # propose only
  python -m scripts.product_ctr_optimizer --apply         # write approved (scope req.)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from src.collectors.base import store_raw
from src.db.client import get_db_connection
from src.utils.llm import get_llm_provider

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SITE = "imade4u.com"
# The proven low-competition NARROWER — a query must name one of these to be a
# Tier-1 candidate (mirrors gift_keywords._ATTRIBUTE_HINTS).
_ATTRS = ("sterling silver", "14k gold", "gold vermeil", "rose gold", "gold",
          "photo projection", "projection", "birthstone", "baguette", "infinity",
          "coordinate", "gps", "handwriting", "engraved", "engravable", "celestial",
          "zodiac", "octagon", "minimalist", "dainty", "bubble", "bar", "birth flower")

_PROMPT = """You are an ecommerce SEO copywriter for iMade4U, a personalized-gifts
store. This PRODUCT page RANKS on Google at position {pos:.0f} and earned {impr}
impressions in 2 weeks — but sits on page 2-4, so almost no clicks. Rewrite ONLY
its SEO title and meta description to front-load the EXACT attribute long-tail
buyers are typing, so it climbs and earns the click.

PRODUCT (merchandising name — do NOT change this): {title}
PRODUCT TYPE: {ptype}
TOP REAL QUERIES it already appears for (what buyers type — WIN these):
{queries}
TODAY: {today}

Rules:
- SEO title <= 60 chars. FRONT-LOAD the winning attribute phrase from the real
  queries (material/gemstone/shape/technique + product). Add ONE buyer hook: a
  recipient/occasion ("for Mom", "Anniversary") or "iMade4U". No ALL CAPS.
- Meta description 140-160 chars: what the buyer GETS (personalization, the
  attribute, who it's for), plain and warm, 1-2 real query terms, soft CTA.
- NEVER invent materials/prices/specs not implied by the product name + queries.
  If the name doesn't say "sterling silver", don't claim it.

Reply ONLY JSON (no fence): {{"title": "...", "description": "..."}}
"""


def _recent(site_id: str) -> set[str]:
    """Handles applied OR proposed in the last 30 days — so a propose-only run
    doesn't re-propose (and re-spend an LLM call on) the same pages every day."""
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """select coalesce(payload->'product_seo_rewrite'->>'handle',
                               payload->'product_seo_proposal'->>'handle')
                 from metrics_raw
                where site_id=%s
                  and (payload ? 'product_seo_rewrite' or payload ? 'product_seo_proposal')
                  and fetched_at >= now() - interval '30 days'""",
            (site_id,))
        return {r[0] for r in cur.fetchall() if r[0]}


def _handle_from_url(url: str) -> str | None:
    m = re.search(r"/products/([a-z0-9-]+)", url)
    return m.group(1) if m else None


def _seo_metafields(product_id: int) -> tuple[str, str]:
    """Current (title_tag, description_tag) for a product, best-effort."""
    from src.integrations.shopify_blog import _api
    try:
        mfs = _api("GET", f"products/{product_id}/metafields.json").get("metafields", [])
    except Exception:
        return "", ""
    t = d = ""
    for m in mfs:
        if m.get("namespace") == "global" and m.get("key") == "title_tag":
            t = m.get("value") or ""
        elif m.get("namespace") == "global" and m.get("key") == "description_tag":
            d = m.get("value") or ""
    return t, d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=3)
    ap.add_argument("--min-impressions", type=int, default=3)
    ap.add_argument("--protect-above", type=float, default=10.0,
                    help="never touch a page ranking better (lower) than this position")
    ap.add_argument("--pos-max", type=float, default=70.0)
    ap.add_argument("--apply", action="store_true",
                    help="WRITE approved rewrites to Shopify (needs write_products scope); "
                         "default is propose-only (writes nothing)")
    ap.add_argument("--model", default="gemini-3.1-pro-preview")
    args = ap.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain=%s", (SITE,))
        row = cur.fetchone()
        if not row:
            print(f"❌ {SITE} missing"); return 2
        site_id = str(row[0])

    from googleapiclient.discovery import build
    from src.utils.google_oauth import get_user_credentials
    from src.integrations import shopify_product as sp
    svc = build("searchconsole", "v1", credentials=get_user_credentials(),
                cache_discovery=False)
    prop = f"sc-domain:{SITE}"
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=14)

    try:
        rows = svc.searchanalytics().query(siteUrl=prop, body={
            "startDate": start.isoformat(), "endDate": end.isoformat(),
            "dimensions": ["page"], "rowLimit": 500}).execute().get("rows", [])
    except Exception as e:  # noqa: BLE001 — never crash the content pipeline
        print(f"  ⚠️  GSC page query failed ({type(e).__name__}) — skip run"); return 0
    done = _recent(site_id)
    cands = []
    for r in rows:
        url = r["keys"][0]
        if "/products/" not in url:
            continue
        h = _handle_from_url(url)
        impr, pos, clk = int(r["impressions"]), float(r["position"]), int(r["clicks"])
        if (h and h not in done and impr >= args.min_impressions
                and args.protect_above < pos <= args.pos_max):
            cands.append((impr, pos, clk, url, h))
    cands.sort(key=lambda x: -x[0])
    if not cands:
        print("  no laggard product-page candidates in the window — done")
        return 0
    print(f"  {len(cands)} product candidate(s) at pos {args.protect_above:.0f}-{args.pos_max:.0f}; "
          f"taking top {args.cap}  [{'APPLY' if args.apply else 'PROPOSE-only'}]")

    llm = get_llm_provider("gemini")
    today = date.today()
    made = 0
    for impr, pos, clk, url, h in cands[: args.cap]:
        prod = sp.get_product_by_handle(h)   # ~700 products → resolve by handle, not a paged list
        if not prod:
            print(f"  ⏭  no product for handle {h} — skip"); continue
        # top real queries for this page — MUST name an in-stock attribute
        try:
            qresp = svc.searchanalytics().query(siteUrl=prop, body={
                "startDate": start.isoformat(), "endDate": end.isoformat(),
                "dimensions": ["query"], "rowLimit": 10,
                "dimensionFilterGroups": [{"filters": [
                    {"dimension": "page", "operator": "equals", "expression": url}]}],
            }).execute().get("rows", [])
        except Exception as e:  # noqa: BLE001 — one flaky query must not abort the run
            print(f"  ⏭  {h}: GSC query failed ({type(e).__name__}) — skip"); continue
        qtexts = [q["keys"][0] for q in qresp]
        blob = " ".join(qtexts).lower()
        # Tier-1 = a genuine LONG-TAIL, not a bare head term Etsy owns. Qualify
        # if any real query is >=3 words (specific) OR names an attribute; skip
        # only when the page ranks solely for 2-word head terms.
        has_longtail = any(len(q.split()) >= 3 for q in qtexts) or any(a in blob for a in _ATTRS)
        if not qtexts or not has_longtail:
            print(f"  ⏭  {h}: only head-term/anonymized queries (skip)"); continue
        queries = "\n".join(f"  - {q['keys'][0]} ({int(q['impressions'])} imp, pos {q['position']:.0f})"
                            for q in qresp) or "  (anonymized)"

        try:
            r2 = llm.generate(prompt=_PROMPT.format(
                pos=pos, impr=impr, title=prod.get("title", h), ptype=prod.get("product_type", ""),
                queries=queries, today=today.isoformat()),
                model=args.model, max_tokens=3500, temperature=0.4, json_mode=True)
        except Exception as e:  # noqa: BLE001 — LLM hiccup on one page must not abort
            print(f"  ⏭  {h}: LLM failed ({type(e).__name__}) — skip"); continue
        t = (r2.text or "").strip()
        if t.startswith("```"):
            t = t.split("\n", 1)[-1].rsplit("```", 1)[0]
        i, j = t.find("{"), t.rfind("}")
        try:
            obj = json.loads(t[i:j + 1])
        except Exception:
            print(f"  ⚠️  parse failed for {h}"); continue
        new_t = (obj.get("title") or "").strip()
        new_d = (obj.get("description") or "").strip()
        if not new_t or len(new_t) > 70 or not new_d:
            print(f"  ⚠️  bad rewrite for {h}"); continue

        cur_t, cur_d = _seo_metafields(prod["id"])
        print(f"\n  ▸ {h}  (pos {pos:.0f}, {impr} imp)")
        print(f"      title: {cur_t or '(none)'}\n          → {new_t}")
        print(f"      desc : {(cur_d or '(none)')[:70]}\n          → {new_d[:70]}")

        proposal = {"handle": h, "product_id": prod["id"], "url": url,
                    "pos": round(pos, 1), "impr": impr, "top_queries": qtexts[:5],
                    "old_title": cur_t, "new_title": new_t,
                    "old_desc": cur_d, "new_desc": new_d, "at": today.isoformat()}
        if args.apply:
            try:
                sp.set_product_seo(prod["id"], meta_title=new_t, meta_description=new_d)
                store_raw(site_id, "gsc", today, {"product_seo_rewrite": proposal})
                print("      ✅ APPLIED to Shopify")
                made += 1
            except Exception as e:
                print(f"      ❌ apply failed (scope? {type(e).__name__}): {str(e)[:100]}")
        else:
            store_raw(site_id, "gsc", today, {"product_seo_proposal": proposal})
            made += 1
    verb = "applied" if args.apply else "proposed (review, then --apply)"
    print(f"\n  ✓ {made} product SEO rewrite(s) {verb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
