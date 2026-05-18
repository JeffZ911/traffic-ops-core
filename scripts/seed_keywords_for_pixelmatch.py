"""Seed planned keywords for pixelmatch.art across the 4-platform × 4-type matrix.

Mirrors `seed_keywords_for_game.py` but targets the ecommerce niche.
Output keywords carry `notes='platform=<slug>|<article_type>|...'` so
KeywordSelector's existing notes-prefix logic surfaces them with
per-platform pass-rate weighting (no schema change).

Usage:
  python -m scripts.seed_keywords_for_pixelmatch --platform amazon_fba --count 15
  python -m scripts.seed_keywords_for_pixelmatch --platform all --count 60 --dry-run

Cost: ~$0.50-1.00 per 50 candidates with entity-verify on (gemini
flash for seed-gen + pro for verify).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.agents._json_extract import extract_json
from src.db.client import get_db_connection
from src.utils.llm import get_llm_provider


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


PLATFORMS = ["amazon_fba", "shopify", "etsy", "tiktok_shop"]


SEED_PROMPT = """You are an SEO researcher for {brand_name}, a SaaS that
batch-generates AI product images for ecommerce sellers. Generate
exactly {count} long-tail search keywords that real {audience_label}
are using today.

Topical scope — **hard quota** across these four content buckets.
You MUST distribute the {count} keywords across types like this:
  - tool_guide:    ~35%  → roughly {count_tool} keywords
       ("how to remove product photo background", "batch resize amazon images",
        "white background photo editor for {audience_short}")
  - vs_comparison: ~25%  → roughly {count_vs} keywords
       ("photoroom vs canva for amazon", "pebblely vs photoroom 2026",
        "best ai product photo tool {audience_short} 2026")
  - use_case:      ~25%  → roughly {count_uc} keywords
       ("amazon seller increased ctr with ai product photos",
        "shopify dropshipper lifestyle photos workflow",
        "etsy print on demand seller doubled sales with ai mockups")
  - policy_guide:  ~15%  → roughly {count_pg} keywords
       ("amazon main image requirements 2026", "etsy listing photo guidelines",
        "tiktok shop image policy", "shopify product image SEO best practices")

THIS IS A HARD CONSTRAINT. If you return all keywords as one type
the seed will be rejected and re-run, wasting budget. Spread across
all four types per the percentages above.

Per-platform terminology and constraints (current as of 2026):
{platform_block}

Keyword rules:
- 3-9 words, lowercase, search-engine intent (no "best [tool] for me?").
- Must include at least one of these signals: a verb ("how to", "remove",
  "resize", "compare"), a platform name ({platform_names}), or a year/version.
- Avoid pure brand-only queries ("pixelmatch") — those will be served
  by the homepage, not the blog.
- 60-90 priority_score: 85+ for high commercial-intent (vs_comparison,
  policy_guide); 70-80 for tool_guide; 60-75 for use_case.

Reply ONLY with a JSON array (no markdown fence):
[
  {{"keyword":        "<lowercase 3-9 word query>",
    "intent":         "informational | commercial | how-to | reference",
    "article_type":   "tool_guide | vs_comparison | use_case | policy_guide",
    "platform":       "amazon_fba | shopify | etsy | tiktok_shop | multi",
    "priority_score": <int 60-90>,
    "notes":          "<one-line why this keyword + which platform context>"
  }}
]
"""


# Per-platform context blob the LLM sees in the prompt. Tunes vocabulary
# (FBA vs DTC vs POD vs short-video) without code branching.
PLATFORM_CONTEXT: dict[str, dict[str, str]] = {
    "amazon_fba": {
        "audience_label": "Amazon FBA sellers and brand registry owners",
        "audience_short": "Amazon",
        "context": (
            "Main image must be pure white #FFFFFF, 1000x1000+ pixels, "
            "product fills 85%+. A+ Content for Brand Registry. "
            "Common pain: listings rejected for 'busy background', "
            "infringement on competitor lifestyle images, manual "
            "photoshop bottleneck when expanding to variant SKUs."
        ),
    },
    "shopify": {
        "audience_label": "Shopify store owners and DTC brands",
        "audience_short": "Shopify",
        "context": (
            "Recommended product photo size 2048x2048 px, square ratio. "
            "Multiple photo angles needed for store theme galleries. "
            "Common pain: dropshippers need to differentiate from "
            "AliExpress stock photos; brand owners need lifestyle "
            "shots without a $5k photoshoot."
        ),
    },
    "etsy": {
        "audience_label": "Etsy sellers and print-on-demand creators",
        "audience_short": "Etsy",
        "context": (
            "Listing photos 2000x2000 px minimum, 10 photo slots, "
            "lifestyle mockups crucial for handmade and POD listings. "
            "Common pain: showing how a digital-download print would "
            "look framed in a real room; vendor-fatigue from generic "
            "mockup generators."
        ),
    },
    "tiktok_shop": {
        "audience_label": "TikTok Shop sellers and short-video creators",
        "audience_short": "TikTok Shop",
        "context": (
            "Product cover 1:1 minimum 800x800, in-video thumbnail 9:16. "
            "Live-shopping requires vertical hero shots. Common pain: "
            "shop products rejected for 'misleading visuals' if the "
            "thumbnail doesn't match the actual product."
        ),
    },
}


def _build_platform_block(platforms: list[str]) -> str:
    lines: list[str] = []
    for p in platforms:
        ctx = PLATFORM_CONTEXT.get(p, {})
        lines.append(
            f"\n[{p}] {ctx.get('audience_label', p)}:\n"
            f"  {ctx.get('context', '(no context)')}"
        )
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--platform", default="all",
                   help="One of amazon_fba, shopify, etsy, tiktok_shop, or 'all'")
    p.add_argument("--count", type=int, default=60,
                   help="Total candidates LLM should propose")
    p.add_argument("--budget-usd", type=float, default=1.50)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--site-domain", default="pixelmatch.art")
    args = p.parse_args()

    platforms = PLATFORMS if args.platform == "all" else [args.platform]
    if any(p not in PLATFORMS for p in platforms):
        print(f"❌ unknown platform; choose from {PLATFORMS} or 'all'")
        return 2

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select id, config from sites where domain = %s limit 1",
            (args.site_domain,),
        )
        row = cur.fetchone()
        if not row:
            print(f"❌ site {args.site_domain!r} not found — run the SQL "
                  f"migration in docs/migrations/004_pixelmatch_site.sql first")
            return 2
        site_id, config = row

    if (config.get("niche") or "gaming") != "ecommerce_tools":
        print(f"⚠️  site.config.niche != 'ecommerce_tools' "
              f"(got {config.get('niche')!r}). Continuing anyway, but "
              f"the agents won't use ecommerce prompts at run time.")

    brand = config.get("brand") or {}
    brand_name = brand.get("name") or "PixelMatch"

    text_cfg = config.get("text_provider") or {}
    model = (
        text_cfg.get("keyword_research_model")
        or text_cfg.get("outline_model")
        or "gemini-3-flash-preview"
    )

    audience_label = ", ".join(
        PLATFORM_CONTEXT[p]["audience_label"] for p in platforms
    )
    audience_short = " / ".join(
        PLATFORM_CONTEXT[p]["audience_short"] for p in platforms
    )
    platform_names = ", ".join(PLATFORM_CONTEXT[p]["audience_short"] for p in platforms)

    prompt = SEED_PROMPT.format(
        brand_name=brand_name,
        count=args.count,
        count_tool=round(args.count * 0.35),
        count_vs=round(args.count * 0.25),
        count_uc=round(args.count * 0.25),
        count_pg=round(args.count * 0.15),
        audience_label=audience_label,
        audience_short=audience_short,
        platform_block=_build_platform_block(platforms),
        platform_names=platform_names,
    )

    print(f"🌱 Seeding {args.count} keywords for pixelmatch.art "
          f"(platforms: {platforms})")
    print(f"   model={model} budget=${args.budget_usd}")

    provider = get_llm_provider("gemini")
    resp = provider.generate(
        prompt=prompt, model=model, max_tokens=8000,
        temperature=0.4, json_mode=True, enable_search=True,
    )
    cost = float(resp.cost_usd or 0)
    print(f"   seed-gen cost: ${cost:.4f}")

    text = resp.text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            wrapped = extract_json("{\"items\": " + text + "}")
            data = wrapped.get("items", [])
        except Exception:
            print(f"❌ couldn't parse LLM output:\n{text[:500]}")
            return 1
    if isinstance(data, dict):
        for k in ("keywords", "items", "results"):
            if k in data and isinstance(data[k], list):
                data = data[k]; break
    if not isinstance(data, list):
        print(f"❌ unexpected shape: {type(data).__name__}")
        return 1
    print(f"   LLM proposed {len(data)} candidates")

    # Dedup against existing keywords
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select lower(keyword) from keywords where site_id = %s",
            (str(site_id),),
        )
        existing = {r[0] for r in cur.fetchall()}

    kept: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        kw = (item.get("keyword") or "").strip().lower()
        if not kw or kw in existing:
            continue
        kept.append(item)
        existing.add(kw)
    print(f"   ✓ {len(kept)} unique candidates after dedup")

    if args.dry_run:
        print()
        print(f"--- dry run preview (all {len(kept)}) ---")
        for item in kept:
            print(
                f"  pri={item.get('priority_score', '?')}  "
                f"plat={item.get('platform','?'):11s}  "
                f"type={item.get('article_type','?'):14s}  "
                f"{item.get('keyword')!r}"
            )

        # Distribution summary — surface skew so the operator can
        # eyeball whether the LLM honored the prompt's 35/25/25/15
        # type mix before paying for a real seed.
        print()
        print("--- distribution: platform × type ---")
        from collections import Counter
        by_type = Counter(i.get("article_type", "?") for i in kept)
        by_platform = Counter(i.get("platform", "?") for i in kept)
        by_combo = Counter(
            (i.get("platform", "?"), i.get("article_type", "?")) for i in kept
        )
        print(f"  by type:     " + "  ".join(f"{k}={v}" for k, v in by_type.most_common()))
        print(f"  by platform: " + "  ".join(f"{k}={v}" for k, v in by_platform.most_common()))
        print()
        print("  grid (rows=platform, cols=type):")
        types = sorted({t for _, t in by_combo})
        plats = sorted({p for p, _ in by_combo})
        header = "    " + " ".join(f"{t[:12]:>12s}" for t in types) + "   total"
        print(header)
        for p in plats:
            row = [f"{p:11s}"] + [
                f"{by_combo.get((p, t), 0):>12d}" for t in types
            ] + [f"{by_platform[p]:>7d}"]
            print("    " + " ".join(row))
        return 0

    inserted = 0
    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        for item in kept:
            kw = (item.get("keyword") or "").strip()
            if not kw:
                continue
            platform = item.get("platform") or "multi"
            atype = item.get("article_type") or "tool_guide"
            # Encode platform + article_type in notes so KeywordSelector
            # and orchestrator can read them without a schema change
            # (same pattern as game-axis seeding).
            notes = (
                f"platform={platform}|{atype}|"
                + (item.get("notes") or "")[:280]
            )
            try:
                cur.execute(
                    """
                    insert into keywords
                      (site_id, keyword, intent, priority_score,
                       source, notes, status)
                    values (%s, %s, %s, %s, 'pixelmatch_seed', %s, 'planned')
                    on conflict (site_id, keyword) do nothing
                    """,
                    (
                        str(site_id), kw.lower(), item.get("intent"),
                        int(item.get("priority_score") or 70), notes,
                    ),
                )
                if cur.rowcount:
                    inserted += 1
            except Exception as e:
                print(f"   ⚠️  insert skip {kw!r}: {e}")

    print(f"   ✅ inserted {inserted} keyword(s) for pixelmatch.art")
    print(f"   cost: ${cost:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
