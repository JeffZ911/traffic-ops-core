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


class OutlineAgent(BaseAgent):
    name = "outline"
    task_type = "outline"
    max_retries = 2

    def _execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        keyword = input_data["keyword"]
        article_type = input_data["article_type"]
        target_words = int(input_data.get("target_word_count", 1500))

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
