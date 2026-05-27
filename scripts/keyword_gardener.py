"""Keep the keyword pool topped up so the daily cron never starves.

Two modes (controlled by flags):

1. **Pool top-up** (always runs first): if `keywords` for the site has
   fewer than --min-planned rows in status='planned', ask Gemini (with
   Google Search grounding) for fresh long-tail keyword ideas balanced
   across the diversity-required article types. Inserted with
   `status='planned'`, `source='auto_seed'`.

2. **Type-deficit auto-seed** (--auto-balance-types): scans the last
   14 days of `articles.published_at` per article_type. For each type
   with **zero** published in that window, asks the LLM for 5-10 seed
   keywords specifically tailored to that type. Inserted with
   `source='auto_type_balance'`. This replaces the old manual
   `banner_batch` workflow — the gardener now sees Banner's 14-day
   drought and auto-seeds banner keywords, which the diversity-aware
   KeywordSelector then picks up on subsequent crons.

Idempotent in both modes: dedupes against existing keywords
(case-insensitive).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv

from src.agents._json_extract import extract_json
from src.db.client import get_db_connection
from src.utils.llm import get_llm_provider
from scripts._keyword_entity_verify import verify_keyword, VerifyResult


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# Map article types to natural-language hints we feed the LLM (GAMING niche)
TYPE_HINTS = {
    "build":        'character build / "best build for X"',
    "tier_list":    'tier lists / "best DPS / Support / Healer"',
    "boss_guide":   'boss-fight strategy / "how to beat X"',
    "reroll":       'reroll-related guides',
    "character_db": 'character profile / "X guide"',
    "weapon_db":    'weapon / artifact / disk profile',
    "news":         'patch notes / banner schedule / version updates',
    "faq":          'FAQ / mechanics-explained content',
    # `comparison` covers BOTH "X vs Y" game-internal comparisons AND the
    # affiliate "best X for Y" gaming-gear buying guides. The OutlineAgent
    # routes both to COMPARISON_PROMPT (which enforces the 4 hard rules for
    # affiliate content); game-vs-game comparisons fall back gracefully
    # since they're a vanishing minority of comparison output.
    "comparison":   (
        '"X vs Y" head-to-heads OR "best X for Y" affiliate-friendly '
        'gaming-gear buying guides (gaming chairs, mechanical keyboards, '
        'gaming mice, monitors, headphones, ergonomic desk gear). For '
        'gear: single-audience focus ("for tall players" / "under $300" / '
        '"for MMO macros") — NEVER generic "best chair 2026". Tag notes '
        'with category=<one of: gaming_chairs|keyboards_mice|monitors_displays|'
        'audio_headphones|desk_ergonomics> when generating a gear keyword.'
    ),
}

# ECOMMERCE niche (e.g. pixelmatch.art) — totally different taxonomy. Used
# when sites.config.niche == 'ecommerce_tools'. Keeping this separate is what
# stops the gardener from seeding game keywords into an ecommerce pool.
ECOM_TYPE_HINTS = {
    "tool_guide":    'how-to guides for online sellers ("how to X on Amazon/Shopify/Etsy")',
    "vs_comparison": '"Tool X vs Tool Y" comparisons for ecommerce sellers',
    "use_case":      'seller case studies / workflow stories ("how a brand did X")',
    "policy_guide":  'platform policy / spec references (image specs, listing rules, fees)',
}

ECOM_PROMPT_TEMPLATE = """You are an SEO researcher for {brand_name}, a blog for
ecommerce sellers about AI product photography, product-image optimization,
and selling on Amazon / Shopify / Etsy / TikTok Shop.

We already have these keywords — DO NOT duplicate (case-insensitive):
{existing_sample}

Use Google Search to find current SELLER search-intent keywords (last 30
days). Generate exactly {n_target} NEW keywords across these article types:

{type_section}

HARD RULE: this is an ECOMMERCE SELLER blog. Absolutely NO video-game,
gacha, or anime-game topics — no game titles (Genshin, Neverness to Everness,
etc.), no characters, no "tier list / build / reroll / banner". Every keyword
must be about selling physical/digital products online.

Reply ONLY with a single JSON array (no markdown fence, no preamble), each
element shaped:
{{
  "keyword": "<lowercase seller search query, 3-8 words>",
  "intent": "informational | comparison | how-to | list",
  "article_type": "<one of the types above>",
  "priority_score": <integer 50-90; higher = more search demand>,
  "notes": "<one short reason / source>"
}}
"""

ECOM_TYPE_BALANCE_PROMPT = """You are an SEO researcher for {brand_name}, an
ecommerce-seller blog (AI product photography, listing optimization,
marketplace selling).

ZERO articles published in the last 14 days for the '{article_type}'
category. Seed 5-8 keywords that NATURALLY fit it:

  {article_type}: {type_hint}

Existing keywords (DO NOT duplicate, case-insensitive):
{existing_sample}

Use Google Search for current seller search-intent. HARD RULE: NO video-game
/ gacha / anime-game topics whatsoever — ecommerce selling only.

Reply ONLY with a single JSON array (no fence). Each element:
{{
  "keyword": "<lowercase, 3-8 words>",
  "intent": "informational | comparison | how-to | list",
  "priority_score": <integer 60-85>,
  "notes": "<one short reason>"
}}
"""


def _is_ecom(config: dict) -> bool:
    return (config.get("niche") or "gaming") == "ecommerce_tools"


# ── Trend-jacking ("蹭话题") prompts. Capture RISING interest so a young,
# low-authority site can win the QDF (Query-Deserves-Freshness) window before
# established sites publish. Source-bound to avoid fabricating new specifics.
TREND_PROMPT = """You are a trend researcher for a multi-game gacha guide site
covering: {games}.

Use Google Search to find what is TRENDING RIGHT NOW (rising search interest
in the last 7 days): version & patch updates, post-patch tier/meta shifts,
events, collabs, and ALREADY-RELEASED characters newly in the spotlight.

HARD RULES to avoid fabrication:
- ONLY well-documented games (Genshin, Honkai Star Rail, Zenless Zone Zero,
  Wuthering Waves) where a live wiki/source already documents the subject.
- The subject MUST already be released and documented. DO NOT propose
  keywords about unreleased / leaked / just-announced characters whose kits
  aren't public yet (e.g. "<new character> best build") — those force the
  writer to fabricate. If you can't open a source page describing it now,
  skip it.
- Prefer trend angles that are inherently grounded: "<game> <version> patch
  notes", "<game> current banner tier impact", "best teams after <version>".

Existing keywords (do NOT duplicate, case-insensitive):
{existing_sample}

Return exactly {n_target} keywords capturing rising interest, each mapping to
ONE of these article types: {types}.

Only include a trend you can back with a REAL, current source. Reply ONLY with
a JSON array (no fence), each element:
{{"keyword": "<lowercase 3-8 words>", "intent": "informational|comparison|how-to|list",
  "article_type": "<one of the types>", "priority_score": <70-95>,
  "notes": "<the trigger event + source>"}}
"""

ECOM_TREND_PROMPT = """You are a trend researcher for {brand_name}, an
ecommerce-seller blog (AI product photography, listing optimization,
marketplace selling).

Use Google Search for what is TRENDING in the last 7 days relevant to online
sellers: new AI image models / tools, marketplace policy changes
(Amazon / Shopify / Etsy / TikTok Shop), major selling events (BFCM,
Prime Day, CNY sourcing), new platform features.

Existing keywords (do NOT duplicate, case-insensitive):
{existing_sample}

Return exactly {n_target} keywords for rising interest, each mapping to ONE
of: {types}. HARD RULE: NO video-game topics whatsoever. Only verifiable
trends with a real current source.

Reply ONLY with a JSON array (no fence), each element:
{{"keyword": "<lowercase 3-8 words>", "intent": "informational|comparison|how-to|list",
  "article_type": "<one of the types>", "priority_score": <70-95>,
  "notes": "<the trigger event + source>"}}
"""


def run_trending(site_id: UUID, config: dict, existing: set[str], args) -> int:
    """Seed time-sensitive 'trend' keywords (source='trend'). KeywordSelector
    gives these a freshness bonus (by created_at) that decays over ~2 weeks,
    so they get written fast then expire. Returns rows inserted."""
    ecom = _is_ecom(config)
    type_blacklist = list((config.get("content_plan") or {}).get("type_blacklist") or [])
    if ecom:
        types = [t for t in (config.get("allowed_article_types")
                             or list(ECOM_TYPE_HINTS.keys())) if t not in type_blacklist]
        brand_name = (config.get("brand") or {}).get("name") or "this ecommerce blog"
    else:
        types = [t for t in TYPE_HINTS if t not in type_blacklist]

    existing_sample = sorted(existing)
    if len(existing_sample) > 50:
        existing_sample = existing_sample[:25] + existing_sample[-25:]
    existing_lines = "\n".join(f"  - {kw}" for kw in existing_sample) or "  (empty pool)"

    if ecom:
        prompt = ECOM_TREND_PROMPT.format(
            brand_name=brand_name, existing_sample=existing_lines,
            n_target=args.target, types=", ".join(types),
        )
    else:
        games = ", ".join(
            (m.get("display_name") or g)
            for g, m in (config.get("game_metadata") or {}).items()
        ) or (config.get("game", {}).get("name") or "popular gacha games")
        prompt = TREND_PROMPT.format(
            games=games, existing_sample=existing_lines,
            n_target=args.target, types=", ".join(types),
        )

    provider = get_llm_provider("gemini")
    text_cfg = config.get("text_provider") or {}
    model = (text_cfg.get("keyword_research_model")
             or text_cfg.get("outline_model") or "gemini-3-flash-preview")
    print(f"   📈 trend scan ({'ecommerce' if ecom else 'gaming'})")
    resp = provider.generate(prompt=prompt, model=model, max_tokens=3000,
                             temperature=0.4, json_mode=True, enable_search=True)
    try:
        data = json.loads(resp.text.strip())
    except Exception:
        try:
            data = extract_json("{\"items\": " + resp.text + "}").get("items", [])
        except Exception:
            print("   ⚠️  trend parse failed"); return 0
    if isinstance(data, dict):
        for k in ("keywords", "items", "results"):
            if isinstance(data.get(k), list):
                data = data[k]; break
    if not isinstance(data, list):
        return 0

    fresh = [d for d in data if isinstance(d, dict)
             and (d.get("keyword") or "").strip().lower()
             and (d.get("keyword") or "").strip().lower() not in existing
             and d.get("article_type") in types]

    # NOTE: the entity-verify gate is skipped for trend mode. It is scoped to
    # the single (NTE) game context and false-positives on real characters
    # from the OTHER covered games (it flagged Genshin's Clorinde, WuWa's
    # Yinlin, ZZZ's Jane Doe as "fabricated"). Trends are search-sourced +
    # prompt-bound to well-documented games, and article-time QA + the
    # inline-citation binding catch any fabrication downstream.

    inserted = 0
    with get_db_connection() as conn, conn.cursor() as cur:
        for item in fresh:
            kw = (item.get("keyword") or "").strip()
            try:
                cur.execute(
                    """
                    insert into keywords
                      (site_id, keyword, intent, priority_score, source, notes, status)
                    values (%s, %s, %s, %s, 'trend', %s, 'planned')
                    on conflict (site_id, keyword) do nothing
                    """,
                    (str(site_id), kw, item.get("intent"),
                     int(item.get("priority_score") or 80),
                     ("[trend] " + (item.get("notes") or ""))[:500]),
                )
                if cur.rowcount:
                    inserted += 1
                    existing.add(kw.lower())
            except Exception as e:
                print(f"   ⚠️  trend insert skip {kw!r}: {e}")
    print(f"   📈 trend: +{inserted} keyword(s) (cost ${resp.cost_usd:.4f})")
    return inserted

PROMPT_TEMPLATE = """You are an SEO researcher for a fan-database site about {game_name}
(abbreviation {game_abbr}, released {release_date}). The site needs fresh
long-tail keywords every day so the daily content pipeline always has
something to write about.

We already have these keywords in the pool — DO NOT duplicate them
(case-insensitive match):
{existing_sample}

BLACKLISTED article_types — do NOT propose any keyword whose natural fit
is one of these (NTE is too new; public information is sparse and the
writer fabricates). If a topic would only make sense as one of these
types, skip it:
{type_blacklist}

Use Google Search to find current player-search-intent keywords (last 30
days) for {game_name}. Generate exactly {n_target} NEW keywords distributed
across these required article types (only the non-blacklisted ones):

{type_section}

Reply ONLY with a single JSON array (no markdown fence, no preamble), each
element shaped:
{{
  "keyword": "<lowercase search query, 3-7 words, includes 'nte' or 'neverness'>",
  "intent": "informational | comparison | how-to | list",
  "article_type": "<one of the types above>",
  "priority_score": <integer 50-90; higher = more search demand>,
  "notes": "<one short reason / source>"
}}
"""


# Affiliate gear "best X for Y" generator — separate prompt from the
# game-keyword path because the gaming PROMPT_TEMPLATE forces every
# keyword to include "nte/neverness", which would corrupt affiliate
# keywords ("best gaming chair for nte players" makes no sense).
#
# Generates keywords TAGGED with the right category= note so the
# downstream OutlineAgent products-dict injection picks the right
# affiliate_products.json slice (chairs vs keyboards vs etc).
ECOM_AFFILIATE_PROMPT_TEMPLATE = """You are a buying-guide SEO researcher for ecommerce sellers (Amazon FBA / Shopify / Etsy / TikTok Shop operators).

We want long-tail "best X for Y" keywords for physical seller equipment
our readers (active sellers running product-photo workflows) would buy
via Amazon. The blog hosting these is pixelmatch.art — an AI product-
photography SaaS — so equipment categories that intersect that audience.

We already have these keywords in the pool — DO NOT duplicate:
{existing_sample}

Generate exactly {n_target} NEW keywords across these 5 categories
(try for ~equal distribution):

  - camera_gear       (DSLR/mirrorless for product photos, smartphone gimbals, lens for macro)
  - lighting          (ring lights, softboxes, LED panels, light tents)
  - photo_studio      (lightboxes, photo backdrops, turntables, photo boxes)
  - workspace_ergo    (label printer for shipping, monitor arms, ergonomic chair for editing sessions)
  - storage_shipping  (poly mailers, dimensional weight scales, label dispenser, shipping software)

HARD RULES:
  R1 SINGLE-AUDIENCE: title MUST contain "for [seller demo/scenario]" OR
     "under $N" / "for [platform]" specificity. NEVER generic "best 2026".
     Bad:  "best ring light 2026"
     Good: "best ring light for amazon product photos under $200"
  R2 NO mention of game titles or gacha vocabulary in keyword (this is
     a seller blog, not a gaming blog). Audience tags should be
     seller-flavored: small_etsy_seller, fba_volume_seller, side_hustle,
     amazon_arbitrage, tiktok_creator_seller.
  R3 100-2000 monthly searches target band (3+ qualifier words).

Reply ONLY with a single JSON array (no markdown fence). Each element:
{{
  "keyword": "<lowercase, 4-9 words>",
  "intent": "buy",
  "article_type": "vs_comparison",
  "priority_score": <integer 50-80>,
  "category": "<one of: camera_gear|lighting|photo_studio|workspace_ergo|storage_shipping>",
  "audience": "<short tag>",
  "notes": "<short rationale>"
}}
"""


AFFILIATE_PROMPT_TEMPLATE = """You are a buying-guide SEO researcher for a gacha/MMO/JRPG audience.
We want long-tail "best X for Y" affiliate keywords for gaming-adjacent
peripherals our readers (long-session gamers) would actually buy via Amazon.

We already have these keywords in the pool — DO NOT duplicate (case-insensitive):
{existing_sample}

Use Google Search for current 2026 buying-guide trends. Generate exactly
{n_target} NEW keywords across these 5 product categories (try for ~equal
distribution):

  - gaming_chairs       (ergonomic chairs, racing-seat chairs, footrest combos)
  - keyboards_mice      (mechanical, low-profile, mmo-side-button mice, wireless)
  - monitors_displays   (gaming monitors, ultrawide, OLED, 1440p, 4K)
  - audio_headphones    (closed-back, gaming headsets, IEMs, mics, vtuber gear)
  - desk_ergonomics     (standing desks, monitor arms, lumbar supports, lights)

HARD RULES — outputs failing any rule will be rejected:
  R1 SINGLE-AUDIENCE: title must contain "for [demo or scenario]" OR
     "under $N" / "over $N" / "in 2026". NEVER generic "best chair 2026".
     Bad:  "best gaming chair 2026"
     Good: "best gaming chair for tall mmo players under $500"
  R2 NEVER mention specific game titles in the keyword (NTE, HSR, etc).
     Cross-game audience phrases OK ("gacha grinders", "mmo macro players",
     "long-session jrpg gamers", "raid leaders").
  R3 Avoid head terms — aim for est 100-2000 monthly searches per keyword
     (3+ qualifier words is usually right).

Reply ONLY with a single JSON array (no markdown fence). Each element:
{{
  "keyword": "<lowercase, 4-9 words>",
  "intent": "buy",
  "article_type": "comparison",
  "priority_score": <integer 50-80; higher = more demand>,
  "category": "<one of: gaming_chairs|keyboards_mice|monitors_displays|audio_headphones|desk_ergonomics>",
  "audience": "<short tag, e.g. tall_users, budget_under_300, mmo_macro, wfh_back_pain>",
  "notes": "<short rationale>"
}}
"""


def run_affiliate_seed(
    site_id: UUID, config: dict, existing: set[str], n_target: int = 6,
    budget_usd: float = 0.40,
) -> int:
    """Seed affiliate 'best X for Y' keywords each cron — bypasses the gaming
    keyword path entirely (which forces "nte/neverness" in every keyword).
    Inserts with notes encoded as:

        article_type=comparison|category=<cat>|audience=<tag>|game=multi

    so the OutlineAgent's products-dict injection picks the right category
    slice from data/affiliate_products.json. Returns rows inserted.

    Both niches supported — gaming uses AFFILIATE_PROMPT_TEMPLATE (gaming-
    audience gear like chairs/keyboards), ecommerce uses
    ECOM_AFFILIATE_PROMPT_TEMPLATE (seller equipment like cameras/lights).
    Skipped when the corresponding comparison article_type is blacklisted.
    """
    ecom = _is_ecom(config)
    type_blacklist = list((config.get("content_plan") or {}).get("type_blacklist") or [])
    target_type = "vs_comparison" if ecom else "comparison"
    if target_type in type_blacklist:
        print(f"   🛒 affiliate seed: {target_type} blacklisted — skip")
        return 0

    existing_sample = sorted(existing)
    if len(existing_sample) > 40:
        existing_sample = existing_sample[:20] + existing_sample[-20:]
    existing_lines = "\n".join(f"  - {kw}" for kw in existing_sample) or "  (empty pool)"

    template = ECOM_AFFILIATE_PROMPT_TEMPLATE if ecom else AFFILIATE_PROMPT_TEMPLATE
    prompt = template.format(
        existing_sample=existing_lines,
        n_target=n_target,
    )

    provider = get_llm_provider("gemini")
    # Use gemini-3.1-flash-lite (stable, non-preview, no thinking step leak).
    # gemini-3.1-flash-lite-preview was retired by Google 2026-05-26; the
    # NON-preview variant ("gemini-3.1-flash-lite") is the stable replacement.
    # Cheapest 3.x-tier model — affiliate seed is a tiny call (6 short
    # keywords), no reason to spend 3-flash-preview compute on it.
    # llm.py MODEL_FALLBACKS adds a chain so if Google ever retires this
    # variant too, we cascade gracefully to gemini-2.5-flash.
    model = "gemini-3.1-flash-lite"
    print(f"\n🛒 Affiliate seed: generating {n_target} gear keywords "
          f"(category-balanced, single-audience, model={model})")
    try:
        resp = provider.generate(prompt=prompt, model=model, max_tokens=2500,
                                 temperature=0.4, json_mode=True, enable_search=False)
    except Exception as e:
        print(f"   ⚠️  affiliate seed LLM call failed: {type(e).__name__}: {e}")
        return 0
    if resp.cost_usd > budget_usd:
        print(f"   ⚠️  cost ${resp.cost_usd:.4f} over budget ${budget_usd:.2f}; skipping inserts")
        return 0

    text = resp.text.strip()

    # Strip markdown fences if present. enable_search=True forces json_mode
    # off (Gemini's grounding tool is incompatible with response_mime_type),
    # so the LLM sometimes wraps the JSON in ```json ... ```  fences despite
    # the "no fence" instruction in the prompt.
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl > 0:
            text = text[first_nl + 1:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()

    # Some grounded models prefix with a sentence, "Here are the keywords:".
    # Find the first '[' or '{' and slice from there.
    for ch in "[{":
        i = text.find(ch)
        if i > 0:
            text = text[i:]
            break

    data: Any = None
    try:
        data = json.loads(text)
    except Exception:
        try:
            data = extract_json("{\"items\": " + text + "}").get("items", [])
        except Exception:
            # Last-ditch: regex-extract bracketed objects and JSON-parse each.
            import re as _re
            objs = _re.findall(r"\{[^{}]*\}", text)
            data = []
            for o in objs:
                try:
                    data.append(json.loads(o))
                except Exception:
                    pass
            if not data:
                print(f"   ⚠️  affiliate seed parse failed; raw={text[:300]!r}")
                return 0
    if isinstance(data, dict):
        for k in ("keywords", "items", "results"):
            if isinstance(data.get(k), list):
                data = data[k]; break
    if not isinstance(data, list):
        return 0

    valid_gaming = {
        "gaming_chairs", "keyboards_mice", "monitors_displays",
        "audio_headphones", "desk_ergonomics",
    }
    valid_ecom = {
        "camera_gear", "lighting", "photo_studio",
        "workspace_ergo", "storage_shipping",
    }
    valid_categories = valid_ecom if ecom else valid_gaming
    fresh = [d for d in data if isinstance(d, dict)
             and (d.get("keyword") or "").strip()
             and (d.get("keyword") or "").strip().lower() not in existing
             and (d.get("category") in valid_categories)]

    if not fresh:
        print("   nothing to insert (duplicates or invalid)")
        return 0

    inserted = 0
    with get_db_connection() as conn, conn.cursor() as cur:
        for item in fresh:
            kw = (item.get("keyword") or "").strip()
            category = item.get("category", "unknown")
            audience = item.get("audience", "general")
            base_notes = (item.get("notes") or "")[:200]
            # Tag with the right article_type so KeywordSelector routes
            # the keyword to the correct outline prompt downstream.
            notes = (
                f"article_type={target_type}|category={category}|"
                f"audience={audience}|game=multi|{base_notes}"
            )[:500]
            try:
                cur.execute(
                    """
                    insert into keywords
                      (site_id, keyword, intent, priority_score, source, notes, status)
                    values (%s, %s, 'buy', %s, 'affiliate_seed', %s, 'planned')
                    on conflict (site_id, keyword) do nothing
                    """,
                    (str(site_id), kw,
                     int(item.get("priority_score") or 65),
                     notes),
                )
                if cur.rowcount:
                    inserted += 1
                    existing.add(kw.lower())
            except Exception as e:
                print(f"   ⚠️  affiliate insert skip {kw!r}: {e}")
    print(f"   🛒 affiliate seed: +{inserted} keyword(s) (cost ${resp.cost_usd:.4f})")
    return inserted


def _log_verify_rejection(
    site_id: UUID, keyword: str, reason: str, fabricated_entities: list[str]
) -> None:
    """Record a gardener entity-verify rejection to `alerts` so the
    operator can audit which generated keywords were dropped and why.
    Failure to log is swallowed — never block the gardener on it."""
    try:
        with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into alerts (site_id, level, source, message, payload)
                values (%s, 'info', 'keyword_gardener', %s, %s::jsonb)
                """,
                (
                    str(site_id),
                    f"entity-verify dropped keyword {keyword!r}",
                    json.dumps({
                        "keyword": keyword,
                        "fabricated_entities": fabricated_entities,
                        "reason": reason,
                    }),
                ),
            )
    except Exception:
        pass


def _gate_keywords_by_entity_verify(
    provider, model: str, items: list[dict[str, Any]],
    site_id: UUID, budget_usd: float, log=print,
) -> tuple[list[dict[str, Any]], float, int]:
    """Run entity-verify on each candidate keyword. Items whose verdict
    is 'archive' are dropped (and alert-logged). Items whose verdict is
    'keep' (real / general / mixed-real) flow through.

    `entity_status='general'` (no proper nouns detected) skips the LLM
    call entirely to save cost — those keywords aren't at fabrication
    risk by definition.

    A simple lowercase-detection heuristic decides which keywords need
    LLM verify: anything with a 4+ character token that isn't a known
    category word goes to LLM. The conservative bias is intentional:
    extra verify calls cost $0.005 each; a single fabricated keyword
    that gets through can cost $0.40+ downstream when WritingAgent
    hallucinates content for it.

    Returns (kept_items, cumulative_cost, n_rejected).
    """
    kept: list[dict[str, Any]] = []
    rejected = 0
    cost = 0.0
    for item in items:
        if cost >= budget_usd:
            log(f"   ⛔ verify-gate budget cap ${budget_usd:.2f} reached; "
                f"passing remaining {len(items) - len(kept) - rejected} items through unverified")
            kept.extend(items[len(kept) + rejected:])
            break
        kw = (item.get("keyword") or "").strip()
        if not kw:
            continue

        # Cheap pre-filter: if the keyword has zero tokens outside our
        # category-word allowlist, skip verify (it's a pure category
        # query like "best dps build nte").
        if not _needs_entity_verify(kw):
            kept.append(item)
            continue

        res: VerifyResult = verify_keyword(provider, model, kw)
        cost += res.cost_usd
        if res.verdict == "archive":
            rejected += 1
            log(f"   ❌ verify-gate drop: {kw!r} "
                f"(fab: {res.fabricated_entities or '?'})")
            _log_verify_rejection(
                site_id, kw, res.reason, res.fabricated_entities,
            )
            continue
        kept.append(item)
    return kept, cost, rejected


# Tokens that count as "general categories" — not entity names.
# Anything in the keyword that ONLY contains these (plus stop-words)
# bypasses LLM verify.
CATEGORY_TOKENS = {
    # site / game / language scaffolding
    "nte", "neverness", "everness", "guide", "build", "tier", "list",
    "best", "for", "and", "or", "vs", "with", "without", "how", "to",
    "what", "when", "where", "who", "why", "the", "in", "on", "of",
    # gameplay categories
    "dps", "sub", "support", "healer", "tank", "beginner", "advanced",
    "tips", "tricks", "endgame", "early", "late", "f2p", "p2w",
    "reroll", "team", "teams", "comp", "comps", "synergy",
    "weapon", "weapons", "disk", "disks", "set", "sets", "skill", "skills",
    "energy", "stamina", "boss", "bosses", "guardian", "lord", "king",
    "chamber", "spiral", "abyss", "anomaly", "anomalies",
    "character", "characters", "4", "5", "star", "tier-list",
    # frequency / time
    "daily", "weekly", "monthly", "2026", "patch", "update", "release",
    "method", "fast", "fastest", "ios", "android", "pc",
    "rotation", "priority", "ranking", "rankings", "list",
}


def _needs_entity_verify(keyword: str) -> bool:
    """Return True if the keyword has at least one non-category token
    long enough to be a candidate entity name (4+ chars)."""
    import re
    tokens = re.findall(r"[a-z0-9]+", (keyword or "").lower())
    suspicious = [
        t for t in tokens
        if len(t) >= 4 and t not in CATEGORY_TOKENS
    ]
    return bool(suspicious)


def _existing_keywords(cur, site_id: UUID, sample_n: int = 60) -> set[str]:
    cur.execute(
        "select lower(keyword) from keywords where site_id = %s",
        (str(site_id),),
    )
    return {r[0] for r in cur.fetchall()}


TYPE_BALANCE_PROMPT = """You are an SEO researcher for {game_name} (abbreviation
{game_abbr}, released {release_date}).

We notice ZERO articles published in the last 14 days for the
'{article_type}' content category. We need to fix that by seeding the
keyword pool with 5-8 keywords that NATURALLY fit this category:

  {article_type}: {type_hint}

Existing keywords (DO NOT duplicate, case-insensitive):
{existing_sample}

Use Google Search to find current player-search-intent for {game_name}.
Keywords MUST be true to the category — do not stretch a tier-list query
into a banner article. If the category truly has no realistic queries
yet (game too new to have e.g. patch notes), return an empty array.

Reply ONLY with a single JSON array (no markdown fence). Each element:
{{
  "keyword": "<lowercase, 3-7 words, ideally containing 'nte' or 'neverness'>",
  "intent": "informational | comparison | how-to | list",
  "priority_score": <integer 60-85>,
  "notes": "<one short reason>"
}}
"""


def auto_balance_types(
    cur,
    site_id: UUID,
    config: dict,
    existing: set[str],
    budget_usd: float,
    forced_types: list[str] | None = None,
    forced_count: int | None = None,
) -> tuple[int, float]:
    """Seed keywords for any article_type with 0 published in the last 14d.

    If `forced_types` is provided, skips the starvation check and seeds
    those types directly (used by the type-floor pre-seed in main()).
    Returns (rows_inserted, cumulative_cost_usd).
    """
    from datetime import date, timedelta
    ecom = _is_ecom(config)
    hints = ECOM_TYPE_HINTS if ecom else TYPE_HINTS

    if forced_types:
        starved = list(forced_types)
    else:
        cur.execute(
            """
            select article_type, count(*)
              from articles
             where site_id = %s
               and status = 'published'
               and published_at >= %s
             group by article_type
            """,
            (str(site_id), date.today() - timedelta(days=14)),
        )
        have_recent = {r[0] for r in cur.fetchall() if r[0]}
        # Universe of types is niche-specific (ecommerce: no build/boss/…).
        universe = (
            (config.get("allowed_article_types") or list(ECOM_TYPE_HINTS.keys()))
            if ecom else list(TYPE_HINTS.keys())
        )
        starved = [t for t in universe if t not in have_recent]

    # Respect sites.config.content_plan.type_blacklist: never seed keywords
    # for a category the operator has marked unwritable. Without this, the
    # auto-balance loop would refill `news` faster than KeywordSelector's
    # blacklist could filter, producing a flood of doomed candidates.
    blacklist = set((config.get("content_plan") or {}).get("type_blacklist") or [])
    if blacklist:
        before = starved
        starved = [t for t in starved if t not in blacklist]
        skipped = [t for t in before if t in blacklist]
        if skipped:
            print(f"   ⚖️  skipped blacklisted starved types: {skipped}")

    if not starved:
        print("   ⚖️  all article_types have recent coverage — nothing to balance")
        return 0, 0.0

    print(f"   ⚖️  starved types (0 published / 14d): {starved}")
    provider = get_llm_provider("gemini")
    text_cfg = config.get("text_provider") or {}
    model = (
        text_cfg.get("keyword_research_model")
        or text_cfg.get("outline_model")
        or "gemini-3-flash-preview"
    )
    game = config.get("game", {})
    brand_name = (config.get("brand") or {}).get("name") or "this ecommerce blog"

    existing_sample = sorted(existing)
    if len(existing_sample) > 50:
        existing_sample = existing_sample[:25] + existing_sample[-25:]
    existing_lines = "\n".join(f"  - {kw}" for kw in existing_sample) or "  (empty pool)"

    total_inserted = 0
    cumulative_cost = 0.0

    for atype in starved:
        if cumulative_cost >= budget_usd:
            print(f"   ⛔ auto-balance budget cap ${budget_usd:.2f} hit; "
                  f"stopping at {atype}")
            break
        if ecom:
            prompt = ECOM_TYPE_BALANCE_PROMPT.format(
                brand_name=brand_name,
                article_type=atype,
                type_hint=hints.get(atype, "general content"),
                existing_sample=existing_lines,
            )
        else:
            prompt = TYPE_BALANCE_PROMPT.format(
                game_name=game.get("name", "the game"),
                game_abbr=game.get("abbreviation", ""),
                release_date=game.get("release_date", "recently"),
                article_type=atype,
                type_hint=TYPE_HINTS.get(atype, "general content"),
                existing_sample=existing_lines,
            )
        try:
            resp = provider.generate(
                prompt=prompt, model=model, max_tokens=2000, temperature=0.4,
                json_mode=True, enable_search=True,
            )
        except Exception as e:
            print(f"   ⚠️  LLM call for {atype!r} failed: {e}")
            continue
        cumulative_cost += float(resp.cost_usd or 0)

        try:
            data = json.loads(resp.text.strip())
        except Exception:
            try:
                wrapped = extract_json("{\"items\": " + resp.text + "}")
                data = wrapped.get("items", [])
            except Exception:
                print(f"   ⚠️  parse failed for {atype!r}; skipping")
                continue
        if not isinstance(data, list):
            if isinstance(data, dict):
                for k in ("keywords", "items", "results"):
                    if k in data and isinstance(data[k], list):
                        data = data[k]
                        break
            if not isinstance(data, list):
                continue

        # Entity-verify gate for auto-balance picks too. Same logic as
        # the top-up path — drop fabricated-entity proposals BEFORE
        # they enter the pool.
        candidate_items = [
            d for d in data
            if isinstance(d, dict)
            and (d.get("keyword") or "").strip().lower()
            and (d.get("keyword") or "").strip().lower() not in existing
        ]
        if candidate_items and not ecom:  # ecommerce brands are known — skip gaming verifier
            candidate_items, vcost, vrej = _gate_keywords_by_entity_verify(
                provider, model, candidate_items, site_id,
                budget_usd=max(budget_usd * 0.5, 0.20),
            )
            cumulative_cost += vcost
            if vrej:
                print(f"   🔬 {atype}: verify dropped {vrej}")

        inserted_for_type = 0
        for item in candidate_items:
            kw = (item.get("keyword") or "").strip().lower()
            if not kw or kw in existing:
                continue
            try:
                cur.execute(
                    """
                    insert into keywords
                      (site_id, keyword, intent, priority_score,
                       source, notes, status)
                    values (%s, %s, %s, %s, 'auto_type_balance', %s, 'planned')
                    on conflict (site_id, keyword) do nothing
                    """,
                    (
                        str(site_id),
                        item.get("keyword"),
                        item.get("intent"),
                        int(item.get("priority_score") or 70),
                        f"[auto-balance:{atype}] " + (item.get("notes") or "")[:400],
                    ),
                )
                if cur.rowcount:
                    inserted_for_type += 1
                    existing.add(kw)
            except Exception as e:
                print(f"   ⚠️  insert skip {kw!r}: {e}")
        total_inserted += inserted_for_type
        print(f"   ⚖️  {atype}: +{inserted_for_type} (LLM cost ${resp.cost_usd:.4f})")

    return total_inserted, cumulative_cost


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--min-planned", type=int, default=20,
                   help="Top up only when planned count drops below this")
    p.add_argument("--target", type=int, default=15,
                   help="How many new keywords to add when topping up")
    p.add_argument("--budget-usd", type=float, default=0.50)
    p.add_argument("--force", action="store_true",
                   help="Top up even if already above threshold")
    p.add_argument("--auto-balance-types", action="store_true",
                   help="After pool top-up, also scan for article_types with "
                        "0 published in the last 14 days and auto-seed 5-8 "
                        "keywords per starved type (source='auto_type_balance'). "
                        "Skipped if planned pool already has ≥40 keywords.")
    p.add_argument("--trending", action="store_true",
                   help="Trend-jacking mode: seed time-sensitive 'trend' "
                        "keywords for rising topics (source='trend'). Runs "
                        "instead of the normal top-up; pair with a daily cron "
                        "slot. KeywordSelector decays their freshness bonus.")
    args = p.parse_args()

    import os
    site_domain = os.getenv("SITE_DOMAIN", "ntecodex.com")
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select id, config from sites where domain = %s limit 1",
            (site_domain,),
        )
        row = cur.fetchone()
        if not row:
            print(f"❌ site {site_domain!r} not found in sites table")
            return 2
        site_id, config = row

        cur.execute(
            "select count(*) from keywords where site_id = %s and status = 'planned'",
            (str(site_id),),
        )
        planned_count = cur.fetchone()[0]
        existing = _existing_keywords(cur, site_id)

    ecom = _is_ecom(config)

    # Trend mode is a separate concern from evergreen pool top-up.
    if args.trending:
        print(f"📈 Keyword Gardener — TREND mode ({site_domain})")
        run_trending(site_id, config, existing, args)
        return 0

    print(f"🌱 Keyword Gardener")
    print(f"   site:           {site_domain}  (niche={'ecommerce_tools' if ecom else 'gaming'})")
    print(f"   planned now:    {planned_count}")
    print(f"   threshold:      {args.min_planned}")
    print(f"   total in pool:  {len(existing)}")

    # Type-floor pre-seed: for every article_type with a daily floor
    # (sites.config.content_plan.article_type_floors), make sure the
    # keyword pool has enough `planned` keywords of that type. Without
    # this, run_batch_smoke's force_article_type would have 0 candidates
    # and the selector would crash. Cheap — single LLM call per starved
    # type, only when planned-count for that type is < 2× the floor.
    floors = ((config.get("content_plan") or {}).get("article_type_floors") or {})
    if floors and not ecom:
        with get_db_connection() as conn, conn.cursor() as cur:
            for atype, floor_n in floors.items():
                # Skip comparison — it's seeded by run_affiliate_seed above
                # (which also runs unconditionally each cron).
                if atype in ("comparison", "vs_comparison"):
                    continue
                # Count planned keywords likely to be picked as this type.
                # Cheap heuristic: notes contains "article_type=X" OR
                # source='auto_type_balance' (created by previous balance
                # passes). Underestimates by ~30% but good enough as a
                # floor trigger.
                cur.execute(
                    """
                    select count(*) from keywords
                     where site_id = %s and status = 'planned'
                       and (notes ilike %s or source = 'auto_type_balance')
                    """,
                    (str(site_id), f"%article_type={atype}%"),
                )
                planned_for_type = int(cur.fetchone()[0])
                target = max(int(floor_n) * 3, 6)  # keep ≥3× the daily floor
                if planned_for_type >= target:
                    print(f"   ⚖️  floor-pool {atype}: {planned_for_type}/{target} — OK")
                    continue
                deficit = target - planned_for_type
                print(f"   ⚖️  floor-pool {atype}: {planned_for_type}/{target} — seeding {deficit}")
                try:
                    bal_inserted, bal_cost = auto_balance_types(
                        cur, site_id, config, existing, args.budget_usd,
                        forced_types=[atype], forced_count=deficit,
                    )
                    print(f"      ↳ +{bal_inserted} for {atype} (${bal_cost:.4f})")
                except Exception as e:
                    print(f"      ↳ ⚠️  floor-pool seed for {atype} failed: {e}")

    # Affiliate seed runs EVERY cron, independent of pool top-up state.
    # The pool may have plenty of gaming keywords but zero affiliate
    # ones — and the gaming PROMPT_TEMPLATE can't generate affiliate
    # ("must include nte/neverness" rule). So we always inject 6 fresh
    # "best X for Y" gear keywords per cron, totally separate path.
    # ~6 keywords/cron × 6 crons/day = ~36 affiliate keywords/day,
    # natural balance against the gaming inflow.
    if not ecom:
        try:
            run_affiliate_seed(site_id, config, existing,
                               n_target=6, budget_usd=args.budget_usd)
        except Exception as e:
            print(f"   ⚠️  affiliate seed failed: {type(e).__name__}: {e}")

    if planned_count >= args.min_planned and not args.force:
        print(f"   ✓ above threshold — no top-up action")
        return 0

    print(f"   → topping up by {args.target}")

    content_plan = config.get("content_plan") or {}
    type_blacklist: list[str] = list(content_plan.get("type_blacklist") or [])

    # Pick the niche's taxonomy + allowed types.
    hints = ECOM_TYPE_HINTS if ecom else TYPE_HINTS
    if ecom:
        required_types = (config.get("allowed_article_types")
                          or list(ECOM_TYPE_HINTS.keys()))
    else:
        diversity = content_plan.get("diversity", {})
        required_types = diversity.get("required_types") or list(TYPE_HINTS.keys())

    # Filter by blacklist so the prompt never invites a blacklisted category.
    allowed_required = [t for t in required_types if t not in type_blacklist]
    type_section = "\n".join(
        f"  - {t}: {hints.get(t, 'general guide')}"
        for t in allowed_required
    )

    # Sample some existing keywords to feed the prompt (so model knows what to avoid)
    existing_sample = sorted(existing)
    if len(existing_sample) > 60:
        # Show first 30 + last 30 (keeps prompt small)
        existing_sample = existing_sample[:30] + existing_sample[-30:]
    existing_lines = "\n".join(f"  - {kw}" for kw in existing_sample) or "  (empty pool)"

    if ecom:
        brand_name = (config.get("brand") or {}).get("name") or "this ecommerce blog"
        prompt = ECOM_PROMPT_TEMPLATE.format(
            brand_name=brand_name,
            existing_sample=existing_lines,
            n_target=args.target,
            type_section=type_section,
        )
    else:
        game = config.get("game", {})
        prompt = PROMPT_TEMPLATE.format(
            game_name=game.get("name", "the game"),
            game_abbr=game.get("abbreviation", ""),
            release_date=game.get("release_date", "recently"),
            existing_sample=existing_lines,
            n_target=args.target,
            type_section=type_section,
            type_blacklist=json.dumps(type_blacklist) if type_blacklist else "  (none)",
        )

    provider = get_llm_provider("gemini")
    text_cfg = config.get("text_provider") or {}
    model = (
        text_cfg.get("keyword_research_model")
        or text_cfg.get("outline_model")
        or "gemini-3-flash-preview"
    )

    resp = provider.generate(
        prompt=prompt, model=model, max_tokens=4000, temperature=0.5,
        json_mode=True, enable_search=True,
    )

    if resp.cost_usd > args.budget_usd:
        print(f"⚠️  this single call cost ${resp.cost_usd:.4f} — over budget cap "
              f"${args.budget_usd:.2f}. Bailing.")
        return 1

    # Try direct JSON parse (array), with fallback through extract_json which
    # handles fenced output but expects an object — wrap arrays into objects.
    text = resp.text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # extract_json returns a dict; wrap text into an object for parsing
        try:
            obj = extract_json("{\"items\": " + text + "}")
            data = obj.get("items", [])
        except Exception:
            print(f"❌ couldn't parse LLM output:\n{text[:500]}")
            return 1

    if not isinstance(data, list):
        # Maybe they returned an object with a key
        if isinstance(data, dict):
            for k in ("keywords", "items", "results"):
                if k in data and isinstance(data[k], list):
                    data = data[k]
                    break
        if not isinstance(data, list):
            print(f"❌ unexpected shape: {type(data).__name__}")
            return 1

    # De-dupe against existing pool
    fresh: list[dict[str, Any]] = []
    skipped = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        kw = (item.get("keyword") or "").strip().lower()
        if not kw or kw in existing:
            skipped += 1
            continue
        fresh.append(item)
        existing.add(kw)

    if not fresh:
        print(f"   nothing to insert (all {skipped} candidates were duplicates / empty)")
        return 0

    # Entity-verify gate (P0 二次修复 2026-05-11): drop fabricated-entity
    # keywords BEFORE they enter the pool. This guards against the gaming
    # writer hallucinating sparse new-game proper nouns. For the ECOMMERCE
    # niche the "entities" are well-known established brands (Amazon, Etsy,
    # Photoroom…) that the verifier (gaming-scoped) wrongly flags as
    # fabricated — so we skip the gate there (article-time QA still checks).
    if ecom:
        print(f"   🔬 entity-verify gate skipped (ecommerce niche — brands are known)")
    else:
        print(f"   🔬 entity-verify gate on {len(fresh)} candidate(s)")
        verify_budget = max(args.budget_usd * 0.5, 0.30)
        fresh, verify_cost, verify_rejected = _gate_keywords_by_entity_verify(
            provider, model, fresh, site_id, verify_budget, log=print,
        )
        print(f"   🔬 verify: kept {len(fresh)}, dropped {verify_rejected}, "
              f"cost ${verify_cost:.4f}")
        if not fresh:
            print(f"   ✓ nothing left after verify; nothing inserted")
            return 0

    inserted = 0
    with get_db_connection() as conn, conn.cursor() as cur:
        for item in fresh:
            try:
                cur.execute(
                    """
                    insert into keywords
                      (site_id, keyword, intent, priority_score, source, notes, status)
                    values (%s, %s, %s, %s, 'auto_seed', %s, 'planned')
                    on conflict (site_id, keyword) do nothing
                    """,
                    (
                        str(site_id),
                        item.get("keyword"),
                        item.get("intent"),
                        int(item.get("priority_score") or 60),
                        (item.get("notes") or "")[:500],
                    ),
                )
                inserted += cur.rowcount or 0
            except Exception as e:
                print(f"   ⚠️ skip {item.get('keyword')!r}: {e}")

    print(f"   ✅ inserted {inserted} new keyword(s); cost ${resp.cost_usd:.4f}")

    # Optional second pass: type-deficit balance.
    if args.auto_balance_types:
        # Skip if the topup already brought the pool to a comfortable size —
        # the diversity-weighted KeywordSelector should be able to find
        # under-represented types from existing inventory without burning
        # another LLM call per starved type.
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "select count(*) from keywords where site_id = %s and status = 'planned'",
                (str(site_id),),
            )
            planned_after = cur.fetchone()[0]
        if planned_after >= 40:
            print(f"   ⚖️  pool now {planned_after} planned — skip auto-balance "
                  f"(KeywordSelector diversity bonus will cover starved types)")
        else:
            print(f"\n⚖️  Auto-balance starved article_types (budget cap "
                  f"${args.budget_usd:.2f})")
            with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
                bal_inserted, bal_cost = auto_balance_types(
                    cur, site_id, config, existing, args.budget_usd
                )
            print(f"   ⚖️  total auto-balance: +{bal_inserted} rows, "
                  f"cumulative ${bal_cost:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
