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
- Internal-link anchors: use the relative-path placeholder
  `[anchor text](#TODO:keyword:<target-keyword>)` instead of any external URL.
  Example: `[NTE Reroll Guide](#TODO:keyword:nte-reroll-guide)`
- End with a `## Sources` H2 listing the URLs you used (1 line per source).

Reply with the Markdown body ONLY. No preamble, no JSON wrapping, no fences.
Start directly with the opening hook line.
"""


class WritingAgent(BaseAgent):
    name = "writing"
    task_type = "writing"
    max_retries = 2

    def _execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
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
        )

        feedback_block = ""
        if feedback:
            feedback_block = (
                "PREVIOUS ATTEMPT FAILED QA. Address these issues in this rewrite:\n"
                f"{json.dumps(feedback, indent=2, ensure_ascii=False)}\n"
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
