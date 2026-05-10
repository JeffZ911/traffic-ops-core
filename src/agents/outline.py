"""OutlineAgent — generate a structured outline for one article.

Branches per article_type (see CODE-SPEC §3.2.4 + SITE-STRUCTURE §6.3).
character_db follows the JSON schema in SITE-STRUCTURE §3.3.
"""

from __future__ import annotations

import json
from typing import Any

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


GENERIC_PROMPT = """You are an SEO content strategist for a gacha-game guide site
(Neverness to Everness, abbreviated NTE).

Generate an outline for a single article.

Keyword (target search query): {keyword}
Article type: {article_type}
Required sections (use these as H2 headings, in order): {sections}
Target word count: {target_words}

Reply with JSON only (no markdown fence). Schema:
{{
  "article_type": "{article_type}",
  "title": "<H1 / SEO title, 50–65 chars>",
  "slug": "<kebab-case slug, ASCII only, max 60 chars>",
  "meta_description": "<140–160 chars>",
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


CHARACTER_DB_PROMPT = """You are an SEO content strategist for a gacha-game guide site
(Neverness to Everness, abbreviated NTE).

Generate a structured character_db page outline for the keyword: {keyword}
Target word count: {target_words}

The character_db type uses a SPECIFIC JSON schema (do not deviate).

Reply with JSON only (no markdown fence). Schema:
{{
  "article_type": "character_db",
  "title": "<H1 / SEO title with character name + 'NTE Build & Guide'>",
  "slug": "<character-name-slug>",
  "meta_description": "<140–160 chars>",
  "h1": "<the article H1>",
  "character_id": "<lowercase character name>",
  "rarity": <int 4-5>,
  "element": "<element name; if unknown, 'Unknown'>",
  "weapon_type": "<sword|bow|polearm|catalyst|claymore|fist; if unknown 'Unknown'>",
  "tier": "<S+|S|A|B|C>",
  "role": ["DPS"|"Sub-DPS"|"Support"|"Healer"|"Tank"],
  "release_banner": "<banner name or 'Unknown'>",
  "skills": {{
    "basic_attack": {{"name": "<name>", "description": "<desc>"}},
    "skill":        {{"name": "<name>", "description": "<desc>", "cooldown_sec": <int>}},
    "ultimate":     {{"name": "<name>", "description": "<desc>", "energy_cost": <int>}},
    "passives":     [{{"name": "<name>", "description": "<desc>"}}]
  }},
  "ascension_materials": [
    {{"level": 20, "items": ["<item>", "..."]}}
  ],
  "best_build": {{
    "weapons": ["<weapon name>", "<weapon name>"],
    "disks": [
      {{"set": "<4pc set name>", "main_stats": {{"head": "...", "chest": "..."}},
        "sub_priority": ["CritRate", "CritDmg", "ATK%"]}}
    ]
  }},
  "teams": [
    {{"name": "<team comp name>", "members": ["<char1>", "<char2>", "..."]}}
  ],
  "sections": [
    {{"h2": "Overview",   "key_points": ["..."], "data_required": []}},
    {{"h2": "Skills",     "key_points": ["..."], "data_required": ["skills table"]}},
    {{"h2": "Materials",  "key_points": ["..."], "data_required": ["materials table"]}},
    {{"h2": "Best Build", "key_points": ["..."], "data_required": ["build table"]}},
    {{"h2": "Teams",      "key_points": ["..."], "data_required": []}}
  ],
  "internal_links": [{{"anchor_text": "<text>", "target_keyword": "<keyword>"}}],
  "image_specs": [{{"position": "hero", "description": "<desc>", "aspect_ratio": "16:9"}}],
  "estimated_word_count": {target_words}
}}

For unknown game-specific data (NTE is recently launched), make plausible
educated guesses but keep them clearly fillable later — flag in notes.
"""


class OutlineAgent(BaseAgent):
    name = "outline"
    task_type = "outline"
    max_retries = 2

    def _execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        keyword = input_data["keyword"]
        article_type = input_data["article_type"]
        target_words = int(input_data.get("target_word_count", 1500))

        if article_type == "character_db":
            prompt = CHARACTER_DB_PROMPT.format(keyword=keyword, target_words=target_words)
        else:
            sections = TYPE_SECTIONS.get(
                article_type, ["Overview", "Details", "FAQ"]
            )
            prompt = GENERIC_PROMPT.format(
                keyword=keyword,
                article_type=article_type,
                sections=", ".join(sections),
                target_words=target_words,
            )

        resp = self._call_llm(
            prompt=prompt, max_tokens=4000, temperature=0.4, json_mode=True,
        )
        outline = json.loads(resp.text)

        # Minimal validation
        for required in ("title", "slug", "h1", "sections"):
            if required not in outline:
                raise ValueError(f"Outline missing field: {required}")
        if not isinstance(outline["sections"], list) or not outline["sections"]:
            raise ValueError("Outline.sections must be a non-empty list")

        return outline
