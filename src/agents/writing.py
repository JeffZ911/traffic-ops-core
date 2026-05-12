"""WritingAgent — turn an outline into Markdown article body, grounded in real game data."""

from __future__ import annotations

import json
import re
from typing import Any

from src.agents.base import BaseAgent


FACTUAL_RULES = """
CRITICAL FACTUAL ACCURACY RULES:
1. This article is about {game_name} ({game_abbr}), released on {release_date}.
2. You MUST use Google Search to find current, accurate information about
   characters, weapons, banners, and game mechanics in {game_name}.
3. DO NOT invent character names, weapon names, or game mechanics. If your
   search returns no results for a specific fact, write "[information unavailable]"
   rather than making something up.
4. After writing, list the URLs you actually used as sources at the end of the
   response under a "## Sources" heading. Format each as a Markdown bullet:
   - <Title or hostname> — <full URL>
5. Prefer official sources: official game website, official Discord, mainstream
   gaming news (IGN, GameSpot, Polygon, Game8, Game Rant), Reddit, prydwen.gg.

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
- Be specific with numbers/effects from search results. If a number is not
  available from search, write "[information unavailable]" rather than guessing.
- DO NOT insert any internal links (no `<a>` tags, no markdown `[text](url)`
  pointing to other articles on this site). The CMS adds related-article
  links automatically after publication. External citations belong in the
  Sources section only — not inline.
- Do not embed any `<img>` tags or `![alt](url)` markdown images in the
  article body. Hero and section images are added by the CMS post-publish.
- End with a `## Sources` H2 listing the URLs you used (1 line per source).

Reply with the Markdown body ONLY. No preamble, no JSON wrapping, no fences.
Start directly with the opening hook line.
"""


class WritingAgent(BaseAgent):
    name = "writing"
    task_type = "writing"
    max_retries = 2

    def _execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        from datetime import date as _date
        keyword = input_data["keyword"]
        article_type = input_data["article_type"]
        outline = input_data["outline"]
        min_words = int(input_data.get("min_word_count", 1200))
        max_words = int(input_data.get("max_word_count", 2500))
        feedback = input_data.get("qa_feedback")

        game = self.site_config.get("game", {})
        rules = FACTUAL_RULES.format(
            game_name=game.get("name", "the game"),
            game_abbr=game.get("abbreviation", ""),
            release_date=game.get("release_date", "recently"),
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

        prompt = PROMPT.format(
            game_name=game.get("name", "the game"),
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
