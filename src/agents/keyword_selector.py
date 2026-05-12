"""KeywordSelectorAgent — pick one keyword for today's writing slot.

Picks one keyword + article_type. Three signals combine into a single
ranking the LLM sees:

1. **Per-type QA-pass-rate weighting** (Phase-2.2 P0 fix, 2026-05-11).
   We compute the last-30-day published pass/fail counts per article_type
   and translate to a priority adjustment:

       qa_pass_rate    | adjustment
       ≥ 60%           | +30   (reward types we're consistently good at)
       30-60%          |   0   (neutral)
       <30%            | -50   (penalty for high failure rate)
       3 consecutive failures | -100   (force avoidance even if rate not tripped yet)
       sample < 3      |   0   (insufficient evidence; treat as neutral)

   Replaces the earlier diversity-deficit weighting, which was found to
   actively HARM quality: it pushed the cron toward starved categories
   (banner / weapon_db / news) for which NTE's public information was too
   sparse, leading to 5 consecutive qa_failed runs with fabricated
   proper nouns.

2. **Type blacklist** (`sites.config.content_plan.type_blacklist`).
   Hard-exclude categories that are temporarily unwritable. The
   selector also instructs the LLM never to assign those types.

3. **GSC long-tail priority bonus**: keywords sourced from `gsc_longtail`
   get +20 surfaced in the candidate list as `priority_with_bonus`.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from src.agents._json_extract import extract_json
from src.agents.base import BaseAgent
from src.db.client import get_db_connection


ALL_TYPES: tuple[str, ...] = (
    "build", "tier_list", "boss_guide", "reroll", "character_db",
    "weapon_db", "news", "faq", "comparison",
)

GSC_LONGTAIL_BONUS = 20.0

# QA-pass-rate weighting thresholds. Conservative: we only reward types
# with a real track record and only penalize when failure mode is clear.
QA_RATE_REWARD_THRESHOLD = 0.60
QA_RATE_PENALTY_THRESHOLD = 0.30
QA_RATE_MIN_SAMPLES = 3
QA_RATE_REWARD = 30.0
QA_RATE_PENALTY = -50.0
QA_RATE_CONSECUTIVE_FAIL_PENALTY = -100.0
QA_RATE_LOOKBACK_DAYS = 30


PROMPT = """You are a content scheduler for a {game_name} ({game_abbr}, released {release_date}) guide site.

Pool of candidate keywords (status='planned'; already sorted by
priority_with_bonus, which includes a +20 bump for `gsc_longtail` and a
historical-QA-pass-rate adjustment per article_type):
{candidates}

Historical track record by article_type (last {lookback_days} days):
{track_record}

Blacklisted article types — DO NOT assign these (NTE is too new and
public information is too sparse, so the writer fabricates):
{type_blacklist}

Allowed article_types: {allowed_types}.

Rules:
- Prefer high `priority_with_bonus` keywords. The adjustments already
  encode our track record — high score = type we ship well + GSC signal.
- Map intent to article_type: list-intent → tier_list; how-to → build /
  boss_guide / reroll; informational → character_db / faq; side-by-side
  → comparison. Avoid news / weapon-tier-list-style keywords entirely
  until the blacklist is lifted.
- If multiple candidates score similarly, choose the one with the
  best (highest pass-rate) article_type in the track record. If the
  best-fit article_type is blacklisted, pick a different keyword
  rather than forcing the keyword into a non-blacklisted type that
  doesn't suit it (that was the root cause of the 2026-05-11 incident).
- If a candidate keyword's natural article_type IS in the blacklist,
  prefer skipping that keyword rather than coercing it.

Reply ONLY with JSON in this exact shape (no markdown fence):
{{
  "keyword_id": "<uuid>",
  "keyword_text": "<the chosen keyword>",
  "suggested_article_type": "<one of allowed; MUST NOT be in blacklist>",
  "article_type": "<same as suggested_article_type for backward compat>",
  "reason": "<one short sentence explaining the pick>"
}}
"""


def _qa_pass_rate_table(
    cur, site_id: str, lookback_days: int = QA_RATE_LOOKBACK_DAYS
) -> dict[str, dict[str, Any]]:
    """Per-type aggregates over the last N days.

    Returns: {article_type: {n_pass, n_fail, pass_rate, consecutive_fail}}.
    consecutive_fail = how many of the MOST RECENT articles of that type
    in a row are qa_failed. Reaching the configured threshold triggers
    the harshest priority penalty.
    """
    since = date.today() - timedelta(days=lookback_days)
    cur.execute(
        """
        select coalesce(article_type, '(none)') as t,
               sum(case when status='published' or status='qa_passed' then 1 else 0 end) as n_pass,
               sum(case when status='qa_failed' then 1 else 0 end) as n_fail
          from articles
         where site_id = %s
           and created_at >= %s
         group by t
        """,
        (str(site_id), since),
    )
    base: dict[str, dict[str, Any]] = {}
    for t, n_pass, n_fail in cur.fetchall():
        n_pass = int(n_pass or 0)
        n_fail = int(n_fail or 0)
        denom = n_pass + n_fail
        rate = (n_pass / denom) if denom > 0 else None
        base[t] = {"n_pass": n_pass, "n_fail": n_fail, "pass_rate": rate}

    # Consecutive-fail tail count per type — needed for the strongest
    # penalty. Cheap because we only look at the recent slice.
    for t in list(base.keys()):
        cur.execute(
            """
            select status
              from articles
             where site_id = %s
               and coalesce(article_type, '(none)') = %s
               and created_at >= %s
             order by created_at desc
             limit 10
            """,
            (str(site_id), t, since),
        )
        statuses = [r[0] for r in cur.fetchall()]
        consec = 0
        for st in statuses:
            if st == "qa_failed":
                consec += 1
            else:
                break
        base[t]["consecutive_fail"] = consec
    return base


def _type_adjustment(stats: dict[str, Any] | None) -> tuple[float, str]:
    """Return (priority_delta, short_label) for one article_type.

    Defaults to (0, "neutral") when stats is None / insufficient.
    """
    if not stats:
        return 0.0, "no_data"
    n_pass = int(stats.get("n_pass") or 0)
    n_fail = int(stats.get("n_fail") or 0)
    consec = int(stats.get("consecutive_fail") or 0)
    samples = n_pass + n_fail
    rate = stats.get("pass_rate")

    # Consecutive-failure escape valve fires first
    if consec >= 3:
        return QA_RATE_CONSECUTIVE_FAIL_PENALTY, f"consec_fail={consec}"

    if samples < QA_RATE_MIN_SAMPLES:
        return 0.0, f"few_samples={samples}"

    if rate is None:
        return 0.0, "no_rate"
    if rate >= QA_RATE_REWARD_THRESHOLD:
        return QA_RATE_REWARD, f"pass_rate={rate:.0%}"
    if rate < QA_RATE_PENALTY_THRESHOLD:
        return QA_RATE_PENALTY, f"pass_rate={rate:.0%}"
    return 0.0, f"pass_rate={rate:.0%}"


class KeywordSelectorAgent(BaseAgent):
    name = "keyword_selector"
    task_type = "keyword_selection"
    max_retries = 3

    def get_model(self) -> str:
        return self.site_config["text_provider"]["outline_model"]

    def _execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        site_id = input_data["site_id"]
        cap = int(input_data.get("candidate_cap", 25))

        # Read type blacklist from site config — defaults to empty list
        # if missing. The blacklist is intentionally NOT hard-coded so
        # the operator can flip it from the dashboard without a deploy.
        content_plan = self.site_config.get("content_plan") or {}
        type_blacklist: list[str] = list(content_plan.get("type_blacklist") or [])

        with get_db_connection() as conn, conn.cursor() as cur:
            # Compute the per-type pass-rate signal once
            track_record = _qa_pass_rate_table(cur, str(site_id))

            # Pre-compute per-type priority adjustment (cached for the
            # whole candidate set — much faster than per-candidate)
            type_adj: dict[str, tuple[float, str]] = {
                t: _type_adjustment(track_record.get(t)) for t in ALL_TYPES
            }

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

            # Heuristic: infer the keyword's likely article_type from its
            # text/intent so we can apply the type-rate adjustment to its
            # priority BEFORE the LLM sees the list. Coarse but enough
            # to push the ordering in the right direction.
            def _guess_type(kw: str, intent: str | None) -> str | None:
                kwl = (kw or "").lower()
                if any(s in kwl for s in ("banner", "patch", "release date", "schedule")):
                    return "news"
                if "vs " in kwl or " vs" in kwl or " or " in kwl:
                    return "comparison"
                if "tier list" in kwl or "best characters" in kwl or "best dps" in kwl:
                    return "tier_list"
                if "weapon" in kwl or "arc" in kwl:
                    return "weapon_db"
                if "build" in kwl:
                    return "build"
                if any(s in kwl for s in ("boss", "abyss", "how to beat", "fight")):
                    return "boss_guide"
                if "reroll" in kwl:
                    return "reroll"
                if any(s in kwl for s in ("how", "what", "why", "pity", "system")):
                    return "faq"
                if intent == "list":
                    return "tier_list"
                if intent == "how-to":
                    return "build"
                if intent == "comparison":
                    return "comparison"
                if intent == "informational":
                    return "faq"
                return None

            candidates: list[dict[str, Any]] = []
            for kid, kw, intent, pri, src in cand_rows:
                guessed_type = _guess_type(kw, intent)
                # If the guessed type is blacklisted, skip this candidate
                # outright so the LLM doesn't even see it.
                if guessed_type and guessed_type in type_blacklist:
                    continue
                base = float(pri) if pri is not None else 0.0
                gsc_bonus = GSC_LONGTAIL_BONUS if src == "gsc_longtail" else 0.0
                type_bonus, type_label = type_adj.get(
                    guessed_type or "_unknown_", (0.0, "no_type_guess")
                )
                candidates.append({
                    "keyword_id": str(kid),
                    "keyword": kw,
                    "intent": intent,
                    "source": src or "manual",
                    "guessed_type": guessed_type,
                    "priority": base,
                    "type_adjustment": type_bonus,
                    "type_adjustment_reason": type_label,
                    "priority_with_bonus": round(
                        base + gsc_bonus + type_bonus, 2
                    ),
                })
            if not candidates:
                raise RuntimeError(
                    f"All candidates filtered out by type_blacklist={type_blacklist}"
                )
            candidates.sort(key=lambda c: c["priority_with_bonus"], reverse=True)
            candidates = candidates[:cap]

        # Format the track record for the prompt
        tr_lines: list[str] = []
        for t in ALL_TYPES:
            s = track_record.get(t)
            adj, label = type_adj[t]
            if s:
                tr_lines.append(
                    f"  - {t:14s} pass={s['n_pass']} fail={s['n_fail']} "
                    f"rate={s['pass_rate']*100:.0f}% "
                    f"consec_fail={s.get('consecutive_fail', 0)}  "
                    f"→ priority_adj={adj:+.0f} ({label})"
                    if s["pass_rate"] is not None
                    else f"  - {t:14s} pass=0 fail=0  → priority_adj=0 (no data)"
                )
            else:
                tr_lines.append(f"  - {t:14s} pass=0 fail=0  → priority_adj=0 (no data)")

        prompt = PROMPT.format(
            game_name=self.site_config.get("game", {}).get("name", "the game"),
            game_abbr=self.site_config.get("game", {}).get("abbreviation", ""),
            release_date=self.site_config.get("game", {}).get("release_date", "recently"),
            candidates=json.dumps(candidates, indent=2, ensure_ascii=False),
            track_record="\n".join(tr_lines),
            type_blacklist=json.dumps(type_blacklist),
            lookback_days=QA_RATE_LOOKBACK_DAYS,
            allowed_types=", ".join(t for t in ALL_TYPES if t not in type_blacklist),
        )

        resp = self._call_llm(
            prompt=prompt, max_tokens=4000, temperature=0.3, json_mode=True,
        )
        choice = extract_json(resp.text)

        # ---- Validation ----
        cand_ids = {c["keyword_id"] for c in candidates}
        if choice.get("keyword_id") not in cand_ids:
            raise ValueError(
                f"LLM returned keyword_id not in candidate pool: {choice}"
            )

        # Resolve article_type with backward-compat for both field names.
        atype = (
            choice.get("article_type")
            or choice.get("suggested_article_type")
        )
        if not atype:
            raise ValueError(f"LLM did not return article_type: {choice}")
        if atype in type_blacklist:
            raise ValueError(
                f"LLM picked blacklisted article_type={atype!r}; blacklist={type_blacklist}"
            )
        if atype not in ALL_TYPES:
            raise ValueError(
                f"LLM picked unknown article_type={atype!r}; allowed={ALL_TYPES}"
            )

        # Normalize output shape: both keys present so callers can use
        # either; older orchestrator reads `article_type`.
        choice["article_type"] = atype
        choice["suggested_article_type"] = atype

        # Annotate selector output with full track-record snapshot so
        # post-hoc audits can see what the LLM was looking at.
        choice["_diversity_snapshot"] = {
            "track_record": track_record,
            "type_blacklist": type_blacklist,
            "type_adjustments": {k: {"delta": v[0], "label": v[1]} for k, v in type_adj.items()},
        }
        return choice
