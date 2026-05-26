"""OutlineAgent — generate a structured outline for one article.

Branches per article_type (see CODE-SPEC §3.2.4 + SITE-STRUCTURE §6.3).
character_db follows the JSON schema in SITE-STRUCTURE §3.3.

Uses Google Search grounding to base the outline on real, current game data
rather than the LLM's stale training set.
"""

from __future__ import annotations

import json
from typing import Any

from src.agents._json_extract import extract_json
from src.agents._prompts_ecommerce import (
    ECOMMERCE_TYPE_SECTIONS,
    FACTUAL_RULES as ECOM_FACTUAL_RULES,
    OUTLINE_GENERIC_PROMPT as ECOM_OUTLINE_GENERIC,
    OUTLINE_USE_CASE_PROMPT as ECOM_OUTLINE_USE_CASE,
)
from src.agents.base import BaseAgent


# Section templates per article_type (from CODE-SPEC §3.2.4 table)
TYPE_SECTIONS: dict[str, list[str]] = {
    "build":        ["Overview", "Best Weapons", "Best Disks (Artifacts)", "Team Comp", "Rotation", "FAQ"],
    "tier_list":    ["Methodology", "S Tier", "A Tier", "B Tier", "C Tier", "Recent Changes"],
    "boss_guide":   ["Boss Stats", "Attack Patterns", "Step-by-Step Strategy", "Recommended Team", "Loot"],
    "reroll":       ["Why Reroll", "How to Reroll", "Best Starters", "Time Estimate", "FAQ"],
    "weapon_db":    ["Stats", "Effect", "Best On (Characters)", "How to Get", "Comparison"],
    "news":         ["What Happened", "Key Changes", "Player Reactions", "What's Next"],
    "faq":          ["Question Restated", "Direct Answer", "Detailed Explanation", "Related"],
    "comparison":   ["TL;DR Verdict", "Side-by-Side Table", "Detailed Comparison", "Recommendation"],
}


FACTUAL_RULES = """
CRITICAL FACTUAL ACCURACY RULES:
1. This article is about {game_name} ({game_abbr}), released on {release_date}.
2. You MUST use Google Search to find current, accurate information about
   characters, weapons, banners, and game mechanics in {game_name}.
3. DO NOT invent character names, weapon names, or game mechanics. If your
   search returns no results for a specific fact, write "[information unavailable]"
   rather than making something up.
4. Prefer official sources: official game website, official Discord,
   mainstream gaming news (IGN, GameSpot, Polygon, Game8, Game Rant), Reddit
   r/{game_subreddit}, prydwen.gg, gamerant.com.
"""


GENERIC_PROMPT = """You are an SEO content strategist for a guide site about {game_name}.

{factual_rules}

Your task: generate an outline for a single article.

Keyword (target search query): {keyword}
Article type: {article_type}
Required sections (use these as H2 headings, in order): {sections}
Target word count: {target_words}

Reply with a single JSON object (no surrounding prose, no fences). Schema:
{{
  "article_type": "{article_type}",
  "title": "<H1 / SEO title, 50-65 chars>",
  "slug": "<kebab-case slug, ASCII only, max 60 chars>",
  "meta_description": "<140-160 chars>",
  "h1": "<the article H1>",
  "quick_answer": "<1-2 sentences that directly answer the search query — appears as a callout card above the article body so readers get the answer without scrolling. Concrete and specific, not a hedged preamble. Max 240 chars.>",
  "sections": [
    {{
      "h2": "<exact section name from required list>",
      "key_points": ["<bullet 1>", "<bullet 2>", "..."],
      "data_required": ["<table / chart needed>", "..."],
      "h3_subsections": ["<optional H3>", "..."]
    }}
  ],
  "internal_links": [
    {{"anchor_text": "<text>", "target_keyword": "<related keyword>"}}
  ],
  "image_specs": [
    {{"position": "after H2-1", "description": "<image desc>", "aspect_ratio": "16:9"}}
  ],
  "estimated_word_count": {target_words}
}}
"""


CHARACTER_DB_PROMPT = """You are an SEO content strategist for a guide site about {game_name}.

{factual_rules}

Your task: generate a structured character_db page outline for the keyword: {keyword}
Target word count: {target_words}

The character_db type uses a SPECIFIC JSON schema (do not deviate).

Reply with a single JSON object (no surrounding prose, no fences). Schema:
{{
  "article_type": "character_db",
  "title": "<H1 / SEO title with character name + game abbreviation>",
  "slug": "<character-name-slug>",
  "meta_description": "<140-160 chars>",
  "h1": "<the article H1>",
  "quick_answer": "<1-2 sentences that directly answer 'how do I build/play this character?' — appears as a callout card above the body. Be concrete: name the BiS weapon and primary team archetype. Max 240 chars.>",
  "character_id": "<lowercase character name>",
  "rarity": <int 4 or 5; if unknown use null>,
  "element": "<element name; if unknown 'Unknown'>",
  "weapon_type": "<sword|bow|polearm|catalyst|claymore|fist; if unknown 'Unknown'>",
  "tier": "<S+|S|A|B|C; if community has not rated yet 'Unrated'>",
  "role": ["DPS"|"Sub-DPS"|"Support"|"Healer"|"Tank"],
  "release_banner": "<banner name or 'Unknown'>",
  "skills": {{
    "basic_attack": {{"name": "<name or [information unavailable]>", "description": "..."}},
    "skill":        {{"name": "...", "description": "...", "cooldown_sec": <int or null>}},
    "ultimate":     {{"name": "...", "description": "...", "energy_cost": <int or null>}},
    "passives":     [{{"name": "...", "description": "..."}}]
  }},
  "ascension_materials": [
    {{"level": 20, "items": ["<item or [information unavailable]>"]}}
  ],
  "best_build": {{
    "weapons": ["<weapon name>"],
    "disks": [
      {{"set": "<4pc set name>",
        "main_stats": {{"head": "...", "chest": "..."}},
        "sub_priority": ["CritRate", "CritDmg", "ATK%"]}}
    ]
  }},
  "teams": [
    {{"name": "<team comp name>", "members": ["<char1>", "<char2>"]}}
  ],
  "sections": [
    {{"h2": "Overview",   "key_points": ["..."], "data_required": []}},
    {{"h2": "Skills",     "key_points": ["..."], "data_required": ["skills table"]}},
    {{"h2": "Materials",  "key_points": ["..."], "data_required": ["materials table"]}},
    {{"h2": "Best Build", "key_points": ["..."], "data_required": ["build table"]}},
    {{"h2": "Teams",      "key_points": ["..."], "data_required": []}}
  ],
  "internal_links": [{{"anchor_text": "<text>", "target_keyword": "<keyword>"}}],
  "image_specs": [{{"position": "hero", "description": "...", "aspect_ratio": "16:9"}}],
  "estimated_word_count": {target_words}
}}

Always populate fields with real values from search results. For any field
where search yields no reliable answer, use "[information unavailable]" or null.
"""


# Comparison / "best X for Y" affiliate round-up prompt.
#
# This is the OPPOSITE of generic AI review spam. Four hard rules enforced
# below produce content that survives both AdSense scrutiny and reader
# bullshit-detection:
#
#   1) SINGLE-AUDIENCE: title must contain "for [demo/use-case]". No
#      "best in general" round-ups — they're indistinguishable from
#      affiliate spam.
#   2) WEAKNESS PARAGRAPH: every product gets `cons`. No cons = sales pitch.
#   3) GROUNDED FACTS: every spec (price, rating, dimension) traces to a
#      source URL. Reddit thread > random blog. Unverifiable spec = drop it.
#   4) "WHY SKIPPED X" SECTION: name the brands NOT included and why.
#      This is the strongest trust signal — proves curation, not scraping.
#
# Output `products` array becomes frontmatter `products:` on the published
# article, where ntecodex's ProductRoundup component renders it as cards
# above the prose body.
COMPARISON_PROMPT = """You are a product reviewer covering gear used by long-session gacha/MMO/JRPG players.

Your job: produce a comparison round-up for the keyword: "{keyword}"

This article will publish on a site that takes Amazon Associates commissions.
That MUST NOT change your recommendations. Editorial independence is the only
reason readers come back. If a product is bad, say so. If a popular product is
overrated, say so. Cite sources for every factual claim.

CRITICAL: factual accuracy
- Use Google Search to find current product specs, pricing bands, ratings,
  and review aggregations from RTINGS, Wirecutter, Reddit (r/ergonomics,
  r/MechanicalKeyboards, r/Monitors), and Amazon review aggregates.
- Each `verdict` and `cons` field must trace to something a reader could
  verify. Don't invent specs. If a spec isn't verifiable, omit it rather
  than guess.
- For Amazon ASIN values: if uncertain, set "asin": null and the editor
  will fill it in. Never guess ASINs.

FOUR HARD RULES — outputs are rejected if any rule is violated:

  R1 SINGLE-AUDIENCE TITLE. The title MUST contain "for [demo or scenario]".
     - Bad: "Best gaming chairs 2026"
     - Good: "Best gaming chair for players over 6'2"" / "Best gaming
             chair under $300 for long MMO sessions"

  R2 WEAKNESS PARAGRAPH. Every product MUST have a non-empty `cons` array
     with at least 2 specific weaknesses. "None really" is NOT a valid con.
     Generic cons ("could be cheaper") are NOT valid — point to specific
     features or use-case mismatches.

  R3 GROUNDED FACTS. The `verdict` text must reference specific specs or
     comparisons (price tier, build material, warranty length, etc.) — no
     hand-wave like "great product" or "highly recommended". The reader
     should be able to fact-check the verdict against the cited spec.

  R4 "WHY WE SKIPPED" SECTION. Include a section h2 = "What we didn't
     include and why" naming 2-3 popular brands/products you DELIBERATELY
     left out, with a specific reason for each (e.g., "DXRacer racing-seat
     geometry pushes shoulders forward — wrong for long sessions").

Reply with a single JSON object (no surrounding prose, no fences). Schema:
{{
  "article_type": "comparison",
  "title": "<must contain 'for [specific audience or scenario]'>",
  "slug": "<kebab-case>",
  "meta_description": "<140-160 chars>",
  "h1": "<same as title>",
  "quick_answer": "<1-2 sentences naming the top pick + 1-2 specific runner-ups for different sub-segments. Max 240 chars.>",
  "target_audience": "<one sentence: who this article is FOR. e.g. 'Players 6'2\\" and taller doing 4+ hour sessions on a budget under $500.'>",
  "products": [
    {{
      "name": "<exact Amazon-style product name>",
      "asin": "<Amazon ASIN or null if uncertain>",
      "image_url": "<full m.media-amazon.com URL or null>",
      "price_usd": <number — approximate retail>,
      "rating": <number 0-5>,
      "review_count": <approximate integer>,
      "best_for": "<short tag — what this product is the top pick for>",
      "pros": ["<concrete pro 1>", "<concrete pro 2>", "<concrete pro 3>"],
      "cons": ["<specific weakness 1>", "<specific weakness 2>"],
      "verdict": "<2-3 sentence editorial verdict citing specific specs or comparisons>"
    }}
  ],
  "sections": [
    {{"h2": "How we picked these {{category}}", "key_points": ["...sourcing methodology...", "...selection criteria..."], "data_required": []}},
    {{"h2": "What '{{audience tag}}' actually needs", "key_points": ["...3 specific requirements with rationale..."], "data_required": []}},
    {{"h2": "Comparison summary", "key_points": ["...explain the rankings without listing products again..."], "data_required": []}},
    {{"h2": "When to skip the upgrade", "key_points": ["...honest 'don't buy' guidance — strong trust signal..."], "data_required": []}},
    {{"h2": "What we didn't include and why", "key_points": ["<brand A and why>", "<brand B and why>"], "data_required": []}}
  ],
  "internal_links": [
    {{"anchor_text": "<text>", "target_keyword": "<adjacent buying-guide keyword>"}}
  ],
  "image_specs": [
    {{"position": "hero", "description": "<lifestyle shot showing product in long-session use, NOT a stock product photo>", "aspect_ratio": "16:9"}}
  ],
  "estimated_word_count": {target_words}
}}

Aim for 5 products in the `products` array (3-7 acceptable). All 4 rules apply.
"""


class OutlineAgent(BaseAgent):
    name = "outline"
    task_type = "outline"
    max_retries = 2

    def _execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        keyword = input_data["keyword"]
        article_type = input_data["article_type"]
        target_words = int(input_data.get("target_word_count", 1500))

        # Niche branch (Phase 1A pixelmatch): when site_config.niche
        # is "ecommerce_tools" we route to the ecommerce prompt
        # family (no game_name framing, B2B SaaS voice, seller-facing
        # factuality rules). Default niche="gaming" keeps the existing
        # behavior for ntecodex untouched.
        niche = self.site_config.get("niche") or "gaming"
        if niche == "ecommerce_tools":
            return self._execute_ecommerce(input_data)

        # Multi-game (Phase 2.3+): prefer per-article game metadata
        # from input_data['game'], falling back to legacy
        # site_config.game for single-game sites. Without this, every
        # multi-game article gets NTE-themed outlines because the
        # legacy config still says game.name='Neverness to Everness'.
        game_slug = input_data.get("game") or "unknown"
        game_meta_by_slug = self.site_config.get("game_metadata") or {}
        per_game = game_meta_by_slug.get(game_slug) or {}
        legacy_game = self.site_config.get("game") or {}
        game_name = (
            per_game.get("display_name")
            or legacy_game.get("name")
            or "the game"
        )
        game_abbr = (
            per_game.get("short_name")
            or legacy_game.get("abbreviation")
            or game_slug
        )
        release_date = (
            per_game.get("release_date")
            or legacy_game.get("release_date")
            or "recently"
        )
        rules = FACTUAL_RULES.format(
            game_name=game_name,
            game_abbr=game_abbr,
            release_date=release_date,
            game_subreddit=game_name.replace(" ", "").replace(":", ""),
        )

        if article_type == "character_db":
            prompt = CHARACTER_DB_PROMPT.format(
                game_name=game_name,
                factual_rules=rules,
                keyword=keyword,
                target_words=target_words,
            )
        elif article_type == "comparison":
            # Affiliate / "best X for Y" round-up. Skips the game-specific
            # factuality framing (we're not making game claims here) and
            # uses the dedicated prompt with the 4 hard rules.
            prompt = COMPARISON_PROMPT.format(
                keyword=keyword,
                target_words=target_words,
            )
        else:
            sections = TYPE_SECTIONS.get(article_type, ["Overview", "Details", "FAQ"])
            prompt = GENERIC_PROMPT.format(
                game_name=game_name,
                factual_rules=rules,
                keyword=keyword,
                article_type=article_type,
                sections=", ".join(sections),
                target_words=target_words,
            )

        resp = self._call_llm(
            prompt=prompt,
            # Bumped: with enable_search the model spends ~thousands of tokens
            # on thinking before emitting JSON. 4k was hitting truncation on
            # boss_guide/character_db schemas.
            max_tokens=8000,
            temperature=0.4,
            json_mode=True,        # auto-dropped because enable_search=True
            enable_search=True,
        )
        outline = extract_json(resp.text)

        for required in ("title", "slug", "h1", "sections"):
            if required not in outline:
                raise ValueError(f"Outline missing field: {required}")
        if not isinstance(outline["sections"], list) or not outline["sections"]:
            raise ValueError("Outline.sections must be a non-empty list")

        return outline

    # ────────────────────────────────────────────────────────────────
    # Ecommerce niche branch — used when site_config.niche == "ecommerce_tools".
    # ────────────────────────────────────────────────────────────────
    def _execute_ecommerce(self, input_data: dict[str, Any]) -> dict[str, Any]:
        from datetime import date as _date

        keyword = input_data["keyword"]
        article_type = input_data["article_type"]
        target_words = int(input_data.get("target_word_count", 1800))

        # Platform metadata: orchestrator threads input_data['platform']
        # (amazon_fba | shopify | etsy | tiktok_shop). Falls back to
        # "multi" for cross-platform articles.
        platform_slug = input_data.get("platform") or "multi"
        platform_meta_by_slug = self.site_config.get("platform_metadata") or {}
        per_platform = platform_meta_by_slug.get(platform_slug) or {}
        audience_label = (
            per_platform.get("display_name")
            or "multi-platform ecommerce sellers"
        )
        official_docs_list = ", ".join(per_platform.get("official_docs") or [
            "sellercentral.amazon.com", "help.shopify.com",
            "help.etsy.com", "seller.tiktok.com/help",
        ])
        platform_subreddit = per_platform.get("subreddit") or "FulfillmentByAmazon"

        brand = self.site_config.get("brand") or {}
        brand_name = brand.get("name") or "the publisher's tool"

        rules = ECOM_FACTUAL_RULES.format(
            audience_label=audience_label,
            today_iso=_date.today().isoformat(),
            official_docs_list=official_docs_list,
            platform_subreddit=platform_subreddit,
            brand_name=brand_name,
        )

        if article_type == "use_case":
            prompt = ECOM_OUTLINE_USE_CASE.format(
                brand_name=brand_name,
                audience_label=audience_label,
                factual_rules=rules,
                keyword=keyword,
                target_words=target_words,
            )
        else:
            sections = ECOMMERCE_TYPE_SECTIONS.get(
                article_type, ["Overview", "Step-by-Step", "FAQ"]
            )
            prompt = ECOM_OUTLINE_GENERIC.format(
                brand_name=brand_name,
                audience_label=audience_label,
                factual_rules=rules,
                keyword=keyword,
                article_type=article_type,
                sections=", ".join(sections),
                target_words=target_words,
            )

        resp = self._call_llm(
            prompt=prompt,
            max_tokens=8000,
            temperature=0.4,
            json_mode=True,
            enable_search=True,
        )
        outline = extract_json(resp.text)
        for required in ("title", "slug", "h1", "sections"):
            if required not in outline:
                raise ValueError(f"Outline missing field: {required}")
        if not isinstance(outline["sections"], list) or not outline["sections"]:
            raise ValueError("Outline.sections must be a non-empty list")
        # Tag the niche + platform back into the outline so downstream
        # agents (and PublishAgent) can route on it without re-querying
        # site_config.
        outline.setdefault("niche", "ecommerce_tools")
        outline.setdefault("platform", platform_slug)
        return outline
