"""WritingAgent — turn an outline into Markdown article body."""

from __future__ import annotations

import json
import re
from typing import Any

from src.agents.base import BaseAgent


PROMPT = """You are an SEO content writer for a gacha-game guide site (Neverness to Everness / NTE).

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
- Hit the target word count band.
- Avoid stock AI phrases: "in the realm of", "in today's fast-paced", "delve into", "embark on", "navigating the".
- Be specific with numbers/effects, even if speculative for a new game — readers prefer
  decisive estimates over vague hedging. Do NOT add "as an AI" disclaimers.

Reply with the Markdown body ONLY. No preamble, no JSON wrapping, no fences.
Start directly with the H1 line.
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

        feedback_block = ""
        if feedback:
            feedback_block = (
                "PREVIOUS ATTEMPT FAILED QA. Address these issues in this rewrite:\n"
                f"{json.dumps(feedback, indent=2, ensure_ascii=False)}\n"
            )

        prompt = PROMPT.format(
            keyword=keyword,
            article_type=article_type,
            min_words=min_words,
            max_words=max_words,
            outline_json=json.dumps(outline, indent=2, ensure_ascii=False),
            feedback_block=feedback_block,
        )

        resp = self._call_llm(
            prompt=prompt, max_tokens=12000, temperature=0.7, json_mode=False,
        )
        content = resp.text.strip()
        if not content:
            raise RuntimeError("Writing returned empty content (likely thinking-budget issue)")

        # Word count: split on whitespace, drop punctuation-only tokens
        words = [w for w in re.findall(r"\b\w+\b", content)]
        word_count = len(words)

        # Basic sanity: at least one H2 + one paragraph
        if "## " not in content and "# " not in content:
            raise RuntimeError("Writing output has no H1/H2 markdown headings")

        return {
            "content_md": content,
            "word_count": word_count,
        }
