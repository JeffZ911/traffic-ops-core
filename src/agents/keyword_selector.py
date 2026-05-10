"""KeywordSelectorAgent — pick one keyword for today's writing slot."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from src.agents._json_extract import extract_json
from src.agents.base import BaseAgent
from src.db.client import get_db_connection


PROMPT = """You are a content scheduler for a gacha-game guide site.

Pool of candidate keywords (already filtered to status='planned', sorted by priority):
{candidates}

Article-type distribution in the past 7 days:
{recent_dist}

Your task: pick exactly ONE keyword to write next, AND assign an article_type.
Allowed article_type values: build, tier_list, boss_guide, reroll, character_db,
weapon_db, news, faq, comparison.

Rules:
- Prefer high-priority unused keywords.
- Prefer article_types that are UNDER-represented in the past 7 days
  (we want diversity per site config).
- Map intent to article_type: list-intent → tier_list/list-style, how-to → guide
  types (build/boss_guide/reroll/beginner), informational → character_db/news,
  comparison → comparison.

Reply ONLY with JSON in this exact shape (no markdown fence):
{{"keyword_id": "<uuid>", "keyword_text": "<the chosen keyword>",
"article_type": "<one of allowed>", "reason": "<one short sentence>"}}
"""


class KeywordSelectorAgent(BaseAgent):
    name = "keyword_selector"
    task_type = "keyword_selection"      # not present in site_config
    max_retries = 3                      # selection is cheap; retry up to 3x on parse fail

    def get_model(self) -> str:
        # Override: use outline_model since site_config has no keyword_selection_model
        return self.site_config["text_provider"]["outline_model"]

    def _execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        site_id = input_data["site_id"]
        cap = int(input_data.get("candidate_cap", 25))

        # Pull candidates
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select id, keyword, intent, priority_score
                  from keywords
                 where site_id = %s
                   and status = 'planned'
                   and (last_used_at is null
                        or last_used_at < now() - interval '6 hours')
                 order by last_used_at nulls first,
                          priority_score desc nulls last
                 limit %s
                """,
                (str(site_id), cap),
            )
            cand_rows = cur.fetchall()
            candidates = [
                {"keyword_id": str(r[0]), "keyword": r[1], "intent": r[2],
                 "priority": float(r[3]) if r[3] is not None else None}
                for r in cand_rows
            ]
            if not candidates:
                raise RuntimeError("No keywords with status='planned' available")

            # Recent diversity
            seven_days_ago = date.today() - timedelta(days=7)
            cur.execute(
                """
                select coalesce(article_type, '(none)'), count(*)
                  from articles
                 where site_id = %s and created_at >= %s
                 group by article_type
                """,
                (str(site_id), seven_days_ago),
            )
            recent_dist = {row[0]: row[1] for row in cur.fetchall()}

        prompt = PROMPT.format(
            candidates=json.dumps(candidates, indent=2),
            recent_dist=json.dumps(recent_dist or {"(no articles yet)": 0}, indent=2),
        )

        resp = self._call_llm(
            prompt=prompt, max_tokens=4000, temperature=0.3, json_mode=True,
        )
        choice = extract_json(resp.text)
        # Validate keyword_id is in candidate set
        cand_ids = {c["keyword_id"] for c in candidates}
        if choice.get("keyword_id") not in cand_ids:
            raise ValueError(
                f"LLM returned keyword_id not in candidate pool: {choice}"
            )
        return choice
