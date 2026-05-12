"""Bulk-seed planned keywords for a specific game.

Used when a new game is added to sites.config.primary_games — populates
the keyword pool with 30-50 starter queries spanning all article_types
(characters, builds, tier lists, boss guides, reroll, FAQ) so the daily
cron has fuel from day one.

Pipeline per game:
  1. Pull game metadata from sites.config.game_metadata.<game>.
  2. Ask Gemini Pro + grounding for {count} long-tail queries split
     across the requested article-type mix.
  3. Run each candidate through the existing entity-verify gate
     (scripts/_keyword_entity_verify) — drops fabricated names.
  4. INSERT verified keywords with notes='game=<slug>|...' so
     KeywordSelector's _game_from_notes() can read them.

Usage:
  python -m scripts.seed_keywords_for_game --game wuthering_waves --count 50
  python -m scripts.seed_keywords_for_game --game zzz --count 50
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
from scripts._keyword_entity_verify import verify_keyword


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


SEED_PROMPT = """You are an SEO researcher for a multi-game gacha guide
site. Generate exactly {count} long-tail search keywords that real
players are using for {display_name} ({short_name}, released {release_date}).

Trusted sources for verification:
{wiki_sources_block}

Mix across these article-type buckets:
  - character_db: ~30%   (character profile / single-character build guides)
  - build:        ~20%   ("best X build", role-specific builds)
  - tier_list:    ~15%   ("best dps tier list", "{short_name} support tier list")
  - boss_guide:   ~10%   ("how to beat <boss>", endgame content)
  - reroll:       ~5%    ("how to reroll {short_name}", "fastest {short_name} reroll")
  - faq:          ~10%   ("how does <mechanic> work", system questions)
  - comparison:   ~10%   ("<char> vs <char>", "<weapon> vs <weapon>")

CRITICAL accuracy rules:
- All character names, weapon names, boss names, and mechanic names
  must be REAL in {display_name}. Use Google Search to verify.
- DO NOT invent: prefer general queries ("best dps tier list {short_name}",
  "{short_name} beginner guide") over specific-but-uncertain ones if you
  can't verify a proper noun.
- Lowercase the search query.
- 3-7 words per keyword.
- Include "{short_name}" or "{display_name_lower}" somewhere in the
  query so it's clear which game it belongs to.

Reply ONLY with a JSON array (no markdown fence):
[
  {{"keyword": "<lowercase query>",
    "intent": "informational | comparison | how-to | list",
    "article_type": "<one of the buckets above>",
    "priority_score": <int 60-90>,
    "notes": "<one short reason / which source mentioned it>"}}
]
"""


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--game", required=True,
                   help="Game slug — must exist in sites.config.game_metadata")
    p.add_argument("--count", type=int, default=50)
    p.add_argument("--budget-usd", type=float, default=1.00)
    p.add_argument("--dry-run", action="store_true",
                   help="Print proposals but don't INSERT")
    args = p.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select id, config from sites where domain='ntecodex.com' limit 1"
        )
        site_id, config = cur.fetchone()

    game_metadata = (config.get("game_metadata") or {}).get(args.game)
    if not game_metadata:
        print(f"❌ game {args.game!r} not in sites.config.game_metadata")
        return 2

    display_name = game_metadata.get("display_name", args.game)
    short_name = game_metadata.get("short_name", args.game)
    release_date = game_metadata.get("release_date", "recently")
    wiki_sources = game_metadata.get("wiki_sources") or []
    wiki_sources_block = "\n".join(f"  - {s}" for s in wiki_sources) or "  (general gaming sites)"

    text_cfg = config.get("text_provider") or {}
    model = (
        text_cfg.get("keyword_research_model")
        or text_cfg.get("outline_model")
        or "gemini-3-flash-preview"
    )
    verify_model = text_cfg.get("qa_model") or "gemini-3.1-pro-preview"

    print(f"🌱 Seeding {args.count} keywords for {display_name} ({short_name})")
    print(f"   wiki sources: {wiki_sources}")
    print(f"   model={model} verify_model={verify_model} budget=${args.budget_usd}")

    prompt = SEED_PROMPT.format(
        count=args.count,
        display_name=display_name,
        display_name_lower=display_name.lower(),
        short_name=short_name,
        release_date=release_date,
        wiki_sources_block=wiki_sources_block,
    )

    provider = get_llm_provider("gemini")
    resp = provider.generate(
        prompt=prompt, model=model, max_tokens=8000,
        temperature=0.4, json_mode=True, enable_search=True,
    )
    seed_cost = float(resp.cost_usd or 0)
    print(f"   seed-gen cost: ${seed_cost:.4f}")

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
    if not isinstance(data, list):
        if isinstance(data, dict):
            for k in ("keywords", "items", "results"):
                if k in data and isinstance(data[k], list):
                    data = data[k]
                    break
        if not isinstance(data, list):
            print(f"❌ unexpected shape: {type(data).__name__}")
            return 1

    print(f"   LLM proposed {len(data)} candidates")

    # Existing keywords for dedup
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select lower(keyword) from keywords where site_id = %s",
            (str(site_id),),
        )
        existing = {r[0] for r in cur.fetchall()}

    # Entity-verify
    cumulative_verify = 0.0
    kept: list[dict[str, Any]] = []
    rejected = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        kw = (item.get("keyword") or "").strip().lower()
        if not kw or kw in existing:
            continue
        if cumulative_verify >= args.budget_usd - seed_cost:
            print(f"   ⛔ verify budget cap reached; remaining "
                  f"{len(data) - len(kept) - rejected} candidates passed through")
            kept.append(item)
            existing.add(kw)
            continue
        res = verify_keyword(provider, verify_model, kw)
        cumulative_verify += res.cost_usd
        if res.verdict == "archive":
            rejected += 1
            print(f"   ❌ verify-drop: {kw!r}  (fab: {res.fabricated_entities})")
            continue
        kept.append(item)
        existing.add(kw)

    print(f"   ✓ {len(kept)} kept, {rejected} dropped")
    print(f"   total verify cost: ${cumulative_verify:.4f}")
    total_cost = seed_cost + cumulative_verify
    print(f"   TOTAL spend on {args.game}: ${total_cost:.4f}")

    if args.dry_run:
        print()
        print("--- dry run, not inserting ---")
        for item in kept[:15]:
            print(f"  pri={item.get('priority_score','?')}  "
                  f"type={item.get('article_type','?'):14s}  {item.get('keyword')!r}")
        if len(kept) > 15:
            print(f"  ... +{len(kept)-15} more")
        return 0

    # INSERT
    inserted = 0
    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        for item in kept:
            kw = (item.get("keyword") or "").strip()
            if not kw:
                continue
            # Encode game in notes; KeywordSelector parses `game=<slug>|`.
            notes = (
                f"game={args.game}|{item.get('article_type','?')}|"
                + (item.get("notes") or "")[:300]
            )
            try:
                cur.execute(
                    """
                    insert into keywords
                      (site_id, keyword, intent, priority_score,
                       source, notes, status)
                    values (%s, %s, %s, %s, 'multi_game_seed', %s, 'planned')
                    on conflict (site_id, keyword) do nothing
                    """,
                    (
                        str(site_id),
                        kw.lower(),
                        item.get("intent"),
                        int(item.get("priority_score") or 70),
                        notes,
                    ),
                )
                if cur.rowcount:
                    inserted += 1
            except Exception as e:
                print(f"   ⚠️  insert skip {kw!r}: {e}")

    print(f"   ✅ inserted {inserted} new keyword(s) for {args.game}")
    print(f"   cost: ${total_cost:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
