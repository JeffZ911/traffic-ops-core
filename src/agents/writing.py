"""WritingAgent — turn an outline into Markdown article body, grounded in real game data."""

from __future__ import annotations

import json
import re
from typing import Any

from src.agents._prompts_ecommerce import (
    FACTUAL_RULES as ECOM_FACTUAL_RULES,
    WRITING_PROMPT as ECOM_WRITING_PROMPT,
)
from src.agents.base import BaseAgent


FACTUAL_RULES = """
CRITICAL FACTUAL ACCURACY RULES:
1. This article is about {game_name} ({game_abbr}), released on {release_date}.
   Preferred wiki / community sources for this specific game:
{wiki_sources_block}
2. You MUST use Google Search to find current, accurate information about
   characters, weapons, banners, and game mechanics in {game_name}.
3. DO NOT invent character names, weapon names, or game mechanics. If your
   search returns no results for a specific fact, write "[information unavailable]"
   rather than making something up.
4. SOURCE-BINDING (hard rule): you may state a specific proper noun (character /
   weapon / skill / banner / mechanic name) or a specific number ONLY IF it
   appears in a source you actually retrieved. When you state such a specific,
   CITE IT INLINE as a Markdown link on the supporting word or short phrase,
   pointing to the REAL authoritative source URL you actually opened, e.g.
   `the [Botany Experiment](<real-url-from-your-search>) Resonance Skill`.
   NEVER output a placeholder, "example.com", or made-up URL — only paste
   URLs that appeared in your actual search results. If you cannot find a
   real source for a specific name/number, DO NOT cite a fake link and DO
   NOT invent the fact — describe it generically (e.g. "her Resonance
   Skill") or write "[information unavailable]". A specific claim with NO
   inline citation is treated as a fabrication risk — prefer generic.
5. After writing, ALSO list the URLs you cited at the end under a "## Sources"
   heading, one Markdown bullet each: - <Title or hostname> — <full URL>
6. Prefer official sources: official game website, official Discord, mainstream
   gaming news (IGN, GameSpot, Polygon, Game8, Game Rant), Reddit, prydwen.gg.
   Inline citations to these authoritative sources improve E-E-A-T — use them
   generously for every concrete, checkable claim.

FACTUAL HONESTY RULE (added 2026-05-11 after 5 consecutive QA rejections
for fabricated proper nouns):

  If you cannot find verified information about a specific proper noun
  (character name, weapon / Arc name, banner name, mechanic / system
  name), DO NOT INVENT IT. Instead write the exact phrase:

      [Information not yet publicly available as of {today_iso}]

  Better an honest gap than a fabricated fact. QA will FAIL the entire
  article if any unverified proper noun appears — even one fabrication
  triggers `factual_accuracy = 0` and the article is rejected.

  Concrete examples of what to AVOID (these were the actual fabrications
  in the 2026-05-11 incident):
  - "Echo of Hethereau" as an Arc name (not real)
  - "Ready-Ready" as a mechanic (not real)
  - "Urban Vanguard" as a banner name (Nanally's banner is actually "The Ichi-daime")
  - "Umbral Edge" / "Standard Resonance" as weapons (not real)
  - Pseudo-precise statistics like "19.59% damage increase" (numbers
    invented to look authoritative)

  When information is sparse — say so. When a number is uncertain — write
  "[exact value pending official confirmation]". The reader trusts you
  MORE when you're honest about gaps.
"""


PROMPT = """You are an SEO content writer for a guide site about {game_name}.

{factual_rules}

Write a complete article in Markdown.

Target keyword: {keyword}
Article type: {article_type}
Target word count: between {min_words} and {max_words} words
Outline (you MUST follow this structure):
{outline_json}

{feedback_block}

Strict requirements:
- Open with a 1-2 sentence hook then the main H1.
- Use H2 for each section in the outline (in the same order).
- Include at least one Markdown table OR data list.
- Hit the target word count band (excluding the Sources section).
- Avoid stock AI phrases: "in the realm of", "in today's fast-paced",
  "delve into", "embark on", "navigating the", "in conclusion",
  "remember that", "it's important to note".
- Be specific with numbers/effects from search results, and CITE each specific
  inline as a Markdown link to the authoritative source (see SOURCE-BINDING).
  If a number is not available from search, write "[information unavailable]"
  rather than guessing.
- INLINE CITATIONS (external, REQUIRED): link concrete claims to the external
  source they came from, e.g. `the [Rejuvenating Flow](https://wiki/...) skill`.
  These outbound citations are encouraged — they prove the facts and lift
  E-E-A-T. Only link to real external sources you retrieved.
- DO NOT insert INTERNAL links (no markdown `[text](url)` or `<a>` pointing to
  other articles on THIS site, i.e. {site_host} or relative `/...` paths). The
  CMS adds related-article links automatically after publication. Inline links
  must only ever point to EXTERNAL authoritative sources.
- Do not embed any `<img>` tags or `![alt](url)` markdown images in the
  article body. Hero and section images are added by the CMS post-publish.
- End with a `## Sources` H2 listing the external URLs you cited (1 per line).

Reply with the Markdown body ONLY. No preamble, no JSON wrapping, no fences.
Start directly with the opening hook line.
"""


class WritingAgent(BaseAgent):
    name = "writing"
    task_type = "writing"
    max_retries = 2

    def _execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        # Niche branch (Phase 1A pixelmatch): ecommerce_tools sites
        # use a B2B SaaS voice + seller-facing factuality rules.
        if (self.site_config.get("niche") or "gaming") == "ecommerce_tools":
            return self._execute_ecommerce(input_data)

        from datetime import date as _date
        keyword = input_data["keyword"]
        article_type = input_data["article_type"]
        outline = input_data["outline"]
        min_words = int(input_data.get("min_word_count", 1200))
        max_words = int(input_data.get("max_word_count", 2500))
        feedback = input_data.get("qa_feedback")

        # Resolve game metadata: prefer the per-article game (set by the
        # orchestrator via input_data['game']), falling back to the
        # legacy single-game `site_config.game` for non-multi-game sites.
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
        wiki_sources = per_game.get("wiki_sources") or []
        wiki_sources_block = (
            "\n".join(f"   - {s}" for s in wiki_sources)
            if wiki_sources
            else "   (general gaming sites: IGN, GameSpot, Game8, Reddit)"
        )
        rules = FACTUAL_RULES.format(
            game_name=game_name,
            game_abbr=game_abbr,
            release_date=release_date,
            wiki_sources_block=wiki_sources_block,
            today_iso=_date.today().isoformat(),
        )

        feedback_block = ""
        if feedback:
            banned = feedback.get("fabricated_terms") or []
            ban_section = ""
            if banned:
                bullet = "\n".join(f"   - {t}" for t in banned)
                ban_section = (
                    "\nBANNED TERMS — these names did NOT verify in search last "
                    "time. You MUST NOT use any of them in this rewrite. If you "
                    "need to refer to such a concept, write '[information "
                    "unavailable]' or omit it entirely:\n" + bullet + "\n"
                )
            feedback_block = (
                "PREVIOUS ATTEMPT FAILED QA. Address these issues in this rewrite:\n"
                f"{json.dumps(feedback, indent=2, ensure_ascii=False)}\n"
                + ban_section
            )

        site_host = (
            self.site_config.get("domain")
            or self.site_config.get("site_url")
            or "this site"
        )
        prompt = PROMPT.format(
            game_name=game_name,
            factual_rules=rules,
            keyword=keyword,
            article_type=article_type,
            min_words=min_words,
            max_words=max_words,
            outline_json=json.dumps(outline, indent=2, ensure_ascii=False),
            feedback_block=feedback_block,
            site_host=site_host,
        )

        resp = self._call_llm(
            prompt=prompt,
            max_tokens=12000,
            temperature=0.7,
            json_mode=False,
            enable_search=True,
        )
        content = resp.text.strip()
        if not content:
            raise RuntimeError("Writing returned empty content (likely thinking-budget issue)")

        # Word count: split on whitespace, drop punctuation-only tokens.
        # Excludes the Sources section so the band check reflects body length.
        body_for_count = re.split(r"\n##\s*Sources\s*\n", content, maxsplit=1)[0]
        words = re.findall(r"\b\w+\b", body_for_count)
        word_count = len(words)

        if "## " not in content and "# " not in content:
            raise RuntimeError("Writing output has no H1/H2 markdown headings")

        return {
            "content_md": content,
            "word_count": word_count,
        }

    # ────────────────────────────────────────────────────────────────
    # Ecommerce niche branch
    # ────────────────────────────────────────────────────────────────
    def _execute_ecommerce(self, input_data: dict[str, Any]) -> dict[str, Any]:
        from datetime import date as _date

        keyword = input_data["keyword"]
        article_type = input_data["article_type"]
        outline = input_data["outline"]
        min_words = int(input_data.get("min_word_count", 1400))
        max_words = int(input_data.get("max_word_count", 2600))
        feedback = input_data.get("qa_feedback")

        platform_slug = (
            input_data.get("platform")
            or outline.get("platform")
            or "multi"
        )
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

        feedback_block = ""
        if feedback:
            banned = feedback.get("fabricated_terms") or []
            ban_section = ""
            if banned:
                bullet = "\n".join(f"   - {t}" for t in banned)
                ban_section = (
                    "\nBANNED CLAIMS — these did NOT verify in search last "
                    "time. Do NOT repeat them. If you need to refer to such "
                    "a fact, write '[Information not yet publicly available "
                    "as of " + _date.today().isoformat() + "]' or omit:\n"
                    + bullet + "\n"
                )
            feedback_block = (
                "PREVIOUS ATTEMPT FAILED QA. Address these issues in this "
                f"rewrite:\n{json.dumps(feedback, indent=2, ensure_ascii=False)}\n"
                + ban_section
            )

        prompt = ECOM_WRITING_PROMPT.format(
            brand_name=brand_name,
            audience_label=audience_label,
            factual_rules=rules,
            keyword=keyword,
            article_type=article_type,
            min_words=min_words,
            max_words=max_words,
            outline_json=json.dumps(outline, indent=2, ensure_ascii=False),
            feedback_block=feedback_block,
        )

        resp = self._call_llm(
            prompt=prompt,
            max_tokens=12000,
            temperature=0.7,
            json_mode=False,
            enable_search=True,
        )
        content = resp.text.strip()
        if not content:
            raise RuntimeError("Writing returned empty content (likely thinking-budget issue)")

        body_for_count = re.split(r"\n##\s*Sources\s*\n", content, maxsplit=1)[0]
        words = re.findall(r"\b\w+\b", body_for_count)
        word_count = len(words)

        if "## " not in content and "# " not in content:
            raise RuntimeError("Writing output has no H1/H2 markdown headings")

        return {
            "content_md": content,
            "word_count": word_count,
        }
