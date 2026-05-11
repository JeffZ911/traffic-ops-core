"""KeywordSelectorAgent — pick one keyword for today's writing slot.

Picks one keyword + article_type, balancing two pressures:

1. **Content-type diversity.** We want the published catalog to span every
   article_type that the site can support (build, tier_list, boss_guide,
   reroll, character_db, weapon_db, news, faq, comparison). The cron picks
   ONE article per day, so without intervention the LLM tends to pile up on
   whatever's most popular in the candidate pool. We compute a 14-day +
   7-day type distribution and inject it into the prompt so the LLM can see
   which types are starved.

2. **GSC long-tail priority.** Keywords sourced from GSC long-tail discovery
   (status='planned', source='gsc_longtail') get a +20 priority bonus
   surfaced in the candidate list. Rationale: Google already shows the site
   for those queries — converting impressions to clicks is the cheapest
   ranking win available.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from src.agents._json_extract import extract_json
from src.agents.base import BaseAgent
from src.db.client import get_db_connection


# Article types we actively schedule for (matches articles.article_type CHECK).
ALL_TYPES: tuple[str, ...] = (
    "build", "tier_list", "boss_guide", "reroll", "character_db",
    "weapon_db", "news", "faq", "comparison",
)

# Bonus applied at sort time for GSC-discovered long-tail keywords. Surfaced
# in the candidate list so the LLM sees them ranked higher; also called out
# explicitly in the prompt rules.
GSC_LONGTAIL_BONUS = 20.0


PROMPT = """You are a content scheduler for a gacha-game guide site.

Pool of candidate keywords (already filtered to status='planned', sorted by
priority — note the `priority_with_bonus` column already reflects a +20 bump
for keywords sourced from `gsc_longtail`):
{candidates}

Article-type distribution snapshot:
- Past 7 days  : {recent_7}
- Past 14 days : {recent_14}
- Type deficit (negative = under-published vs. target cadence):
{deficit}

Your task: pick exactly ONE keyword to write next, AND assign an article_type.
Allowed article_type values: {allowed_types}.

Rules:
- Prefer high `priority_with_bonus` keywords.
- Prefer article_types with the LARGEST deficit (most under-published).
  These should be your strong default unless a top-priority keyword clearly
  fits a different type.
- Keywords with source='gsc_longtail' should win ties: Google already
  surfaces the site for those queries, so converting them is the cheapest
  ranking win.
- Map intent to article_type: list-intent → tier_list; how-to → build /
  boss_guide / reroll; informational → character_db / news / faq;
  side-by-side → comparison; banner schedule / patch news → news.
- Do NOT pick the same article_type two days running unless that type still
  has the largest deficit AFTER today's planned write.

Reply ONLY with JSON in this exact shape (no markdown fence):
{{"keyword_id": "<uuid>", "keyword_text": "<the chosen keyword>",
"article_type": "<one of allowed>", "reason": "<one short sentence>"}}
"""


def _compute_deficit(dist_14d: dict[str, int]) -> dict[str, int]:
    """Per-type deficit relative to an even 14-day allocation.

    With 9 types and a 1-article-per-day cron, a perfectly even
    distribution over 14 days would be ~1.55 articles per type. We treat
    that as the target and report `published - target` (negative means
    under-published). Numbers small intentionally so the LLM treats them
    as one signal among many, not a hard constraint.
    """
    target_per_type = 14 / len(ALL_TYPES)  # ~1.55
    return {
        t: round(dist_14d.get(t, 0) - target_per_type, 2)
        for t in ALL_TYPES
    }


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

        with get_db_connection() as conn, conn.cursor() as cur:
            # Pull a larger superset than `cap` so we can re-sort with the
            # gsc_longtail bonus before truncating.
            cur.execute(
                """
                select id, keyword, intent, priority_score, source
                  from keywords
                 where site_id = %s
                   and status = 'planned'
                   and (last_used_at is null
                        or last_used_at < now() - interval '6 hours')
                 order by priority_score desc nulls last,
                          last_used_at nulls first
                 limit %s
                """,
                (str(site_id), max(cap * 2, 50)),
            )
            cand_rows = cur.fetchall()
            if not cand_rows:
                raise RuntimeError("No keywords with status='planned' available")

            candidates: list[dict[str, Any]] = []
            for kid, kw, intent, pri, src in cand_rows:
                base = float(pri) if pri is not None else 0.0
                bonus = GSC_LONGTAIL_BONUS if src == "gsc_longtail" else 0.0
                candidates.append({
                    "keyword_id": str(kid),
                    "keyword": kw,
                    "intent": intent,
                    "source": src or "manual",
                    "priority": base,
                    "priority_with_bonus": round(base + bonus, 2),
                })
            candidates.sort(key=lambda c: c["priority_with_bonus"], reverse=True)
            candidates = candidates[:cap]

            # Diversity snapshots
            today = date.today()
            seven_days_ago = today - timedelta(days=7)
            fourteen_days_ago = today - timedelta(days=14)

            cur.execute(
                """
                select coalesce(article_type, '(none)'), count(*)
                  from articles
                 where site_id = %s
                   and status = 'published'
                   and published_at >= %s
                 group by article_type
                """,
                (str(site_id), seven_days_ago),
            )
            recent_7 = {row[0]: int(row[1]) for row in cur.fetchall()}

            cur.execute(
                """
                select coalesce(article_type, '(none)'), count(*)
                  from articles
                 where site_id = %s
                   and status = 'published'
                   and published_at >= %s
                 group by article_type
                """,
                (str(site_id), fourteen_days_ago),
            )
            recent_14 = {row[0]: int(row[1]) for row in cur.fetchall()}

        deficit = _compute_deficit(recent_14)
        # Order deficit dict so largest deficit (most under-published) is first
        # — that's the visual cue we want the LLM to anchor on.
        deficit_ordered = dict(sorted(deficit.items(), key=lambda kv: kv[1]))

        prompt = PROMPT.format(
            candidates=json.dumps(candidates, indent=2, ensure_ascii=False),
            recent_7=json.dumps(recent_7 or {"(no articles)": 0}, indent=2),
            recent_14=json.dumps(recent_14 or {"(no articles)": 0}, indent=2),
            deficit=json.dumps(deficit_ordered, indent=2),
            allowed_types=", ".join(ALL_TYPES),
        )

        resp = self._call_llm(
            prompt=prompt, max_tokens=4000, temperature=0.3, json_mode=True,
        )
        choice = extract_json(resp.text)
        cand_ids = {c["keyword_id"] for c in candidates}
        if choice.get("keyword_id") not in cand_ids:
            raise ValueError(
                f"LLM returned keyword_id not in candidate pool: {choice}"
            )
        # Annotate the return so downstream agents / dashboards can audit
        # the diversity decision after the fact.
        choice["_diversity_snapshot"] = {
            "recent_7": recent_7,
            "recent_14": recent_14,
            "deficit": deficit,
        }
        return choice
