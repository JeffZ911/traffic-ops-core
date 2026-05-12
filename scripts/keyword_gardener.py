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


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# Map article types to natural-language hints we feed the LLM
TYPE_HINTS = {
    "build":        'character build / "best build for X"',
    "tier_list":    'tier lists / "best DPS / Support / Healer"',
    "boss_guide":   'boss-fight strategy / "how to beat X"',
    "reroll":       'reroll-related guides',
    "character_db": 'character profile / "X guide"',
    "weapon_db":    'weapon / artifact / disk profile',
    "news":         'patch notes / banner schedule / version updates',
    "faq":          'FAQ / mechanics-explained content',
    "comparison":   '"X vs Y" head-to-heads',
}

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
) -> tuple[int, float]:
    """Seed keywords for any article_type with 0 published in the last 14d.

    Returns (rows_inserted, cumulative_cost_usd).
    """
    from datetime import date, timedelta
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
    starved = [t for t in TYPE_HINTS if t not in have_recent]

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

        inserted_for_type = 0
        for item in data:
            if not isinstance(item, dict):
                continue
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
    args = p.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select id, config from sites where domain = 'ntecodex.com' limit 1"
        )
        row = cur.fetchone()
        if not row:
            print("❌ ntecodex.com site missing")
            return 2
        site_id, config = row

        cur.execute(
            "select count(*) from keywords where site_id = %s and status = 'planned'",
            (str(site_id),),
        )
        planned_count = cur.fetchone()[0]
        existing = _existing_keywords(cur, site_id)

    print(f"🌱 Keyword Gardener")
    print(f"   site:           ntecodex.com")
    print(f"   planned now:    {planned_count}")
    print(f"   threshold:      {args.min_planned}")
    print(f"   total in pool:  {len(existing)}")
    if planned_count >= args.min_planned and not args.force:
        print(f"   ✓ above threshold — no action")
        return 0

    print(f"   → topping up by {args.target}")

    game = config.get("game", {})
    content_plan = config.get("content_plan") or {}
    diversity = content_plan.get("diversity", {})
    required_types = diversity.get("required_types") or list(TYPE_HINTS.keys())
    type_blacklist: list[str] = list(content_plan.get("type_blacklist") or [])

    # Filter the required_types list by the blacklist so the prompt never
    # invites a blacklisted category in the first place.
    allowed_required = [t for t in required_types if t not in type_blacklist]
    type_section = "\n".join(
        f"  - {t}: {TYPE_HINTS.get(t, 'general guide')}"
        for t in allowed_required
    )

    # Sample some existing keywords to feed the prompt (so model knows what to avoid)
    existing_sample = sorted(existing)
    if len(existing_sample) > 60:
        # Show first 30 + last 30 (keeps prompt small)
        existing_sample = existing_sample[:30] + existing_sample[-30:]
    existing_lines = "\n".join(f"  - {kw}" for kw in existing_sample) or "  (empty pool)"

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
