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

# Competition-aware bias (LLM-estimated keywords.competition ∈ [0,1]).
# Magnitudes sit between the type-rate rewards (±50/80) and the trend
# bonus so competition nudges ordering without overriding a proven
# high-pass-rate type. A zero-authority site can only rank low-comp
# long-tail, so the reward/penalty gap is deliberately wide.
COMP_LOW_REWARD = 40.0      # competition ≤ 0.30 → rankable long-tail
COMP_MID_REWARD = 5.0       # 0.30 < c < 0.70
COMP_HIGH_PENALTY = -60.0   # competition ≥ 0.70 → unwinnable head term
COMP_VOLUME_BONUS = 10.0    # volume band ≥ 2 (real demand) on top of above


def _trend_freshness_bonus(source: str | None, age_days: float | None) -> float:
    """Trend-jacking: source='trend' keywords get a big bonus that DECAYS so
    they're written while the topic is hot (QDF window), then expire. Only
    applies to trend-sourced keywords — evergreen keywords are unaffected.

    QDF "6h 极速" guarantee: a JUST-SEEDED trend (age≈0, same 03:00 run that
    seeded it) must DOMINATE selection so it publishes same-run and hits the
    3-12h freshness window. The top reward must out-score the strongest
    evergreen (base ~95 + low-comp 40 + volume 10 ≈ 145), so age<1 → +150.
    Decays fast: a trend is only worth racing while it's hot.

    NOTE: source='expansion' (security_cameras footprint layer) deliberately
    gets NO bonus here. Its throughput is guaranteed instead by a reserved
    daily slice in run_batch_smoke (--reserve-source), which keeps the
    experiment's treatment size controlled rather than letting expansion win an
    uncontrolled number of general slots. Expansion keywords are evergreen, so
    there is no QDF window to race anyway."""
    if source != "trend" or age_days is None:
        return 0.0
    a = float(age_days)
    if a < 1:
        return 150.0   # seeded today → dominate, publish this run (QDF window)
    if a < 3:
        return 80.0
    if a < 7:
        return 30.0
    if a < 14:
        return 10.0
    return 0.0  # stale trend — no longer boosted (falls back to base priority)

# QA-pass-rate weighting thresholds (Phase 2.4 — 2026-05-12).
# Aggressive 4-band scheme to push the cron toward consistently-passing
# article types when daily volume goes from 1→24 attempts. At 24/day a
# 10% pass-rate type wastes ~$3.50/day on guaranteed-fail attempts;
# the −80 penalty makes it essentially invisible to the LLM. Consec-fail
# escape valve fires hardest (−150) so a sudden regression in a
# previously-good type bails out fast.
#
#     pass_rate band   | adjustment
#     ≥ 70%            | +50    (strong reward — push toward winners)
#     50 – 70%         | +20    (small reward)
#     30 – 50%         |   0    (neutral)
#     < 30%            | −80    (strong penalty)
#     3 consec fails   | −150   (override; force-avoid regression)
#     samples < 3      |   0    (insufficient evidence)
QA_RATE_HIGH_REWARD_THRESHOLD = 0.70   # ≥ 70%
QA_RATE_MID_REWARD_THRESHOLD = 0.50    # 50-70%
QA_RATE_PENALTY_THRESHOLD = 0.30       # < 30%
QA_RATE_MIN_SAMPLES = 3
QA_RATE_HIGH_REWARD = 50.0
QA_RATE_MID_REWARD = 20.0
QA_RATE_PENALTY = -80.0
QA_RATE_CONSECUTIVE_FAIL_PENALTY = -150.0
QA_RATE_LOOKBACK_DAYS = 30

# Kept as aliases so existing tests still import them.
QA_RATE_REWARD_THRESHOLD = QA_RATE_HIGH_REWARD_THRESHOLD
QA_RATE_REWARD = QA_RATE_HIGH_REWARD


PROMPT = """You are a content scheduler for a MULTI-GAME gacha guide site.

Pool of candidate keywords (status='planned'; already sorted by
priority_with_bonus, which includes a +20 bump for `gsc_longtail`, a
historical-QA-pass-rate adjustment per article_type, AND a game-priority
adjustment per `game_priorities` config):
{candidates}

Historical track record by article_type (last {lookback_days} days):
{track_record}

Cross-game priority targets (the site's intended content mix):
{game_priorities_section}

Blacklisted article types — DO NOT assign these for the indicated game:
{type_blacklist}

Allowed article_types: {allowed_types}.

Rules:
- Prefer high `priority_with_bonus` keywords. The adjustments already
  encode our track record — high score = type we ship well + GSC signal.
- COMPETITION: this is a young, low-authority site — it can only rank
  LOW-competition long-tail. When `competition` is present, strongly
  prefer candidates with competition ≤ 0.3 (rankable) and AVOID
  competition ≥ 0.7 (head terms incumbents own — we cannot win them yet),
  unless the long-tail pool is exhausted. `volume_band` ≥ 2 means real
  demand — prefer rankable keywords that also have demand. (This is
  already folded into priority_with_bonus, but weigh it consciously.)
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
  "suggested_article_type": "<one of allowed; MUST NOT be in this game's blacklist>",
  "article_type": "<same as suggested_article_type for backward compat>",
  "game": "<short_name like wuwa | zzz | hsr | genshin | nte — copy from the candidate's `game` field>",
  "reason": "<one short sentence explaining the pick>"
}}
"""


# Encodes how we tag a keyword with its game in `keywords.notes`.
# The notes column is text; we prefix with `game=<slug>|`. Stable, easy
# to parse, easy to migrate if a real `keywords.game` column ever lands.
_GAME_NOTE_RE = __import__("re").compile(r"\bgame=([a-z_]+)\b")


def _game_from_notes(notes: str | None) -> str | None:
    """Extract game slug from a keywords.notes string."""
    if not notes:
        return None
    m = _GAME_NOTE_RE.search(notes)
    return m.group(1) if m else None


# Tiny adjustment per-game so the LLM sees a higher priority on
# "primary" games and a lower one on the demoted NTE. Linear scaling:
# priority_with_bonus += GAME_PRIORITY_WEIGHT * priority_fraction.
GAME_PRIORITY_WEIGHT = 40.0


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
    if rate >= QA_RATE_HIGH_REWARD_THRESHOLD:
        return QA_RATE_HIGH_REWARD, f"pass_rate={rate:.0%}"
    if rate >= QA_RATE_MID_REWARD_THRESHOLD:
        return QA_RATE_MID_REWARD, f"pass_rate={rate:.0%}"
    if rate < QA_RATE_PENALTY_THRESHOLD:
        return QA_RATE_PENALTY, f"pass_rate={rate:.0%}"
    return 0.0, f"pass_rate={rate:.0%}"


# ── Duplicate-topic guard ────────────────────────────────────────────────
# Root-caused the 15-20 near-identical "Nanally build" articles: nothing
# stopped the selector from picking a keyword whose topic was already covered.
# We reduce a keyword to a "signature" of significant tokens (drop generic
# scaffolding but KEEP discriminating gameplay words like build/vs/tier) and
# skip a candidate if it overlaps an already-published title too much.
import re as _re

_GENERIC_TOKENS = {
    "the", "a", "an", "for", "to", "of", "in", "on", "and", "or", "with",
    "your", "you", "is", "are", "how", "what", "why", "when", "where", "who",
    "guide", "best", "top", "complete", "ultimate", "2026", "2025", "nte",
    "neverness", "everness", "game", "gaming", "explained", "tips", "tricks",
    "new", "latest", "update", "all",
}


def _topic_signature(text: str) -> frozenset[str]:
    """Significant-token set of a keyword/title (lowercased, generic words +
    pure numbers/dates dropped). Keeps gameplay-discriminating words so
    'X build' and 'X vs Y' stay distinct."""
    toks = _re.findall(r"[a-z0-9]+", (text or "").lower())
    return frozenset(
        t for t in toks
        if t not in _GENERIC_TOKENS and not t.isdigit() and len(t) >= 3
    )


def _is_duplicate_topic(
    sig: frozenset[str], published_sigs: list[frozenset[str]], threshold: float = 0.6
) -> bool:
    """True if `sig` overlaps any published signature at/above `threshold`
    (Jaccard), or is a subset of one (already fully covered). Empty sigs
    (all-generic keywords) are never treated as duplicates."""
    if not sig:
        return False
    for ps in published_sigs:
        if not ps:
            continue
        if sig <= ps:  # candidate fully covered by an existing topic
            return True
        inter = len(sig & ps)
        if inter and inter / len(sig | ps) >= threshold:
            return True
    return False


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
        # Cross-game priority dict: {game_slug: weight 0..1}.
        # If missing → single-game site, all weights treated as 1.0.
        game_priorities: dict[str, float] = dict(
            self.site_config.get("game_priorities") or {}
        )
        # Per-game blacklist overrides the flat blacklist when present.
        per_game_blacklist: dict[str, list[str]] = dict(
            self.site_config.get("type_blacklist_per_game") or {}
        )
        game_metadata: dict[str, dict] = dict(
            self.site_config.get("game_metadata") or {}
        )

        with get_db_connection() as conn, conn.cursor() as cur:
            # Compute the per-type pass-rate signal once
            track_record = _qa_pass_rate_table(cur, str(site_id))

            # Pre-compute per-type priority adjustment (cached for the
            # whole candidate set — much faster than per-candidate)
            type_adj: dict[str, tuple[float, str]] = {
                t: _type_adjustment(track_record.get(t)) for t in ALL_TYPES
            }

            # Fetch ALL eligible planned keywords (no LIMIT). The old
            # `limit max(cap*2, 50)` was a silent deadlock: the top-50 by
            # priority can saturate with permanently-ineligible keywords
            # (blacklist-guessed types / duplicate topics) that never leave
            # 'planned', hiding perfectly good candidates below the cutoff —
            # ntecodex sat at 0 output for 28h with 37 eligible keywords
            # ranked 51+. Pools are a few hundred rows; fetching all is cheap.
            # Filtering happens below, then the top `cap` ELIGIBLE are kept.
            cur.execute(
                """
                select id, keyword, intent, priority_score, source, notes,
                       extract(epoch from (now() - created_at))/86400.0 as age_days,
                       competition, search_volume
                  from keywords
                 where site_id = %s
                   and status = 'planned'
                   and (last_used_at is null
                        or last_used_at < now() - interval '6 hours')
                 order by priority_score desc nulls last,
                          last_used_at nulls first
                """,
                (str(site_id),),
            )
            cand_rows = cur.fetchall()
            if not cand_rows:
                raise RuntimeError("No keywords with status='planned' available")

            # Duplicate-topic guard: signatures of already-published titles
            # (and their source keywords) so we never re-pick a covered topic.
            cur.execute(
                "select title, keyword from articles a "
                "left join article_keywords ak on ak.article_id = a.id "
                "left join keywords k on k.id = ak.keyword_id "
                "where a.site_id = %s and a.status = 'published'",
                (str(site_id),),
            )
            published_sigs = []
            for t, k in cur.fetchall():
                for txt in (t, k):
                    s = _topic_signature(txt or "")
                    if s:
                        published_sigs.append(s)

            # Heuristic: infer the keyword's likely article_type from its
            # text/intent so we can apply the type-rate adjustment to its
            # priority BEFORE the LLM sees the list. Coarse but enough
            # to push the ordering in the right direction.
            # Pattern matchers for affiliate-style "best X for/under Y" —
            # these are buying-guide round-ups, not gaming comparisons. The
            # COMPARISON_PROMPT (with the 4 hard rules) handles both shapes.
            import re as _re
            _AFFILIATE_PAT = _re.compile(
                r"\bbest\s+\w+.*\b(for|under\s+\$?\d+|over\s+\$?\d+|in\s+\d{4})\b",
                _re.IGNORECASE,
            )

            def _guess_type(kw: str, intent: str | None, notes: str | None = None) -> str | None:
                # Explicit override from notes (set by ingest_affiliate_keywords.py):
                #   "...|article_type=comparison|..."
                if notes:
                    m = _re.search(r"\barticle_type=([a-z_]+)", notes)
                    if m:
                        return m.group(1)

                kwl = (kw or "").lower()
                if any(s in kwl for s in ("banner", "patch", "release date", "schedule")):
                    return "news"
                # Versus-style ("X vs Y") AND "best X for Z" / "best X under $N"
                # both route to comparison so the COMPARISON_PROMPT picks them up.
                if "vs " in kwl or " vs" in kwl or " or " in kwl:
                    return "comparison"
                if _AFFILIATE_PAT.search(kwl):
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
            # Permanently-ineligible keywords (blacklisted-type guess or
            # duplicate of a published topic) are ARCHIVED, not skipped:
            # skipping leaves them in 'planned' forever, silently clogging
            # the pool (and the planned-count signals the gardener's top-up
            # threshold reads). Both filters are deterministic, so an
            # ineligible keyword today is ineligible every day.
            _archive_blacklisted: list[str] = []
            _archive_duplicate: list[str] = []
            for kid, kw, intent, pri, src, notes, age_days, competition, sv in cand_rows:
                game_slug = _game_from_notes(notes)
                # Per-game blacklist (preferred) else fall back to the flat one.
                effective_blacklist = (
                    per_game_blacklist.get(game_slug, type_blacklist)
                    if game_slug else type_blacklist
                )
                guessed_type = _guess_type(kw, intent, notes)
                if guessed_type and guessed_type in effective_blacklist:
                    _archive_blacklisted.append(str(kid))
                    continue
                # Topic already covered by a published article (dedup).
                if _is_duplicate_topic(_topic_signature(kw), published_sigs):
                    _archive_duplicate.append(str(kid))
                    continue
                base = float(pri) if pri is not None else 0.0
                gsc_bonus = GSC_LONGTAIL_BONUS if src == "gsc_longtail" else 0.0
                type_bonus, type_label = type_adj.get(
                    guessed_type or "_unknown_", (0.0, "no_type_guess")
                )
                game_weight = float(game_priorities.get(game_slug, 0.0)) if game_slug else 0.0
                game_bonus = round(game_weight * GAME_PRIORITY_WEIGHT, 2)
                trend_bonus = _trend_freshness_bonus(src, age_days)
                # Competition-aware bias (2026-05-30). A zero-authority site
                # can only realistically rank LOW-competition long-tail. Boost
                # those; penalise head terms it can't win. Volume nudges so we
                # don't favour zero-demand keywords. Neutral when unscored
                # (competition IS NULL) so it never blocks selection.
                comp_bonus = 0.0
                if competition is not None:
                    c = float(competition)
                    if c <= 0.3:      comp_bonus = COMP_LOW_REWARD      # rankable long-tail
                    elif c >= 0.7:    comp_bonus = COMP_HIGH_PENALTY    # unwinnable head term
                    else:             comp_bonus = COMP_MID_REWARD
                    if sv and int(sv) >= 2:  # has real demand
                        comp_bonus += COMP_VOLUME_BONUS
                candidates.append({
                    "keyword_id": str(kid),
                    "keyword": kw,
                    "intent": intent,
                    "source": src or "manual",
                    "game": game_slug or "unknown",
                    "guessed_type": guessed_type,
                    "priority": base,
                    "type_adjustment": type_bonus,
                    "type_adjustment_reason": type_label,
                    "game_bonus": game_bonus,
                    "game_weight": game_weight,
                    "trend_bonus": trend_bonus,
                    "competition": round(float(competition), 2) if competition is not None else None,
                    "volume_band": int(sv) if sv else None,
                    "competition_bonus": comp_bonus,
                    "priority_with_bonus": round(
                        base + gsc_bonus + type_bonus + game_bonus + trend_bonus + comp_bonus, 2
                    ),
                })
            # Archive the permanently-ineligible (self-healing: drains the
            # clogged pool so planned-counts and future runs reflect reality).
            _to_archive = _archive_blacklisted + _archive_duplicate
            if _to_archive:
                cur.execute(
                    "update keywords set status='archived' where id = any(%s)",
                    (_to_archive,),
                )
                conn.commit()
                print(
                    f"  ♻️  archived {len(_to_archive)} permanently-ineligible "
                    f"planned keyword(s) (blacklisted-type: "
                    f"{len(_archive_blacklisted)}, duplicate-topic: "
                    f"{len(_archive_duplicate)})"
                )

            if not candidates:
                raise RuntimeError(
                    f"Keyword pool STARVED for this site: {len(cand_rows)} planned "
                    f"fetched, {len(_archive_blacklisted)} blacklisted-type and "
                    f"{len(_archive_duplicate)} duplicate-topic (now archived), "
                    f"0 eligible remain. Top up the pool (trend scan / gardener / "
                    f"manual seed) — selection cannot proceed."
                )
            # Force-source narrowing (footprint-expansion reserved slice,
            # 2026-07-14) — MUST run BEFORE the priority cap below. Expansion
            # keywords are deliberately low-priority (their throughput is
            # guaranteed by run_batch_smoke's reserved slice, not by score), so
            # capping first would drop them all and the reserve would silently
            # no-op — reintroducing the very crowd-out it exists to prevent.
            # Narrow to the forced source on the FULL eligible set; fall back to
            # the full pool only if that source has no eligible candidate.
            force_source = input_data.get("force_source")
            if force_source:
                _src_cands = [c for c in candidates
                              if (c.get("source") or "") == force_source]
                if _src_cands:
                    candidates = _src_cands
                else:
                    print(f"  ⚠️  force_source={force_source!r} but no eligible "
                          f"candidates of that source — using full pool")

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

        # Game-priorities + per-game blacklist sections for the prompt
        if game_priorities:
            gp_lines = [
                f"  - {slug}: {weight*100:.0f}% "
                f"(blacklist: {per_game_blacklist.get(slug) or 'none'}) "
                f"display: {game_metadata.get(slug, {}).get('display_name', slug)}"
                for slug, weight in sorted(
                    game_priorities.items(), key=lambda x: -x[1]
                )
            ]
            game_priorities_section = "\n".join(gp_lines)
        else:
            game_priorities_section = "  (single-game site; no cross-game balancing)"

        # Niche-aware allowed types (Phase 1B 2026-05-14):
        #   sites.config.allowed_article_types — when set, this is the
        #   authoritative list. The KeywordSelector LLM is told to pick
        #   ONLY from this list. Pixelmatch uses
        #   [tool_guide, vs_comparison, use_case, policy_guide];
        #   ntecodex leaves this null so we fall back to ALL_TYPES
        #   (the gaming taxonomy) minus type_blacklist as before.
        allowed_override = self.site_config.get("allowed_article_types")
        if isinstance(allowed_override, list) and allowed_override:
            allowed_list = [t for t in allowed_override if t not in type_blacklist]
        else:
            allowed_list = [t for t in ALL_TYPES if t not in type_blacklist]

        # Force-type narrowing (article_type_floors revenue guarantee).
        # When run_batch_smoke passes force_article_type, restrict the
        # selector to ONLY that type so the comparison-floor logic actually
        # produces comparison articles (instead of the LLM picking any
        # type and the orchestrator forcibly relabeling it after — which
        # would mean wrong prompt for the keyword).
        force_type = input_data.get("force_article_type")
        if force_type and force_type in allowed_list:
            allowed_list = [force_type]
            # Also pre-filter candidates to favor those whose guessed
            # type matches — drops the entire candidate set to ~10% but
            # keeps the LLM focused on relevant keywords.
            forced_candidates = [
                c for c in candidates
                if c.get("guessed_type") == force_type
                or (c.get("notes") or "").find(f"article_type={force_type}") >= 0
            ]
            if forced_candidates:
                candidates = forced_candidates

        # NOTE: force_source narrowing happens EARLIER, before the priority cap
        # (see the block just above the sort/truncate) — expansion keywords are
        # deliberately low-priority, so filtering after the cap would drop them
        # all and silently no-op the reserved slice.

        prompt = PROMPT.format(
            candidates=json.dumps(candidates, indent=2, ensure_ascii=False),
            track_record="\n".join(tr_lines),
            type_blacklist=json.dumps(
                per_game_blacklist or {"_default": type_blacklist}
            ),
            game_priorities_section=game_priorities_section,
            lookback_days=QA_RATE_LOOKBACK_DAYS,
            allowed_types=", ".join(allowed_list),
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
        # Validation universe (Phase 1B 2026-05-14):
        #   - If site_config.allowed_article_types is set, that list IS
        #     the universe. Reject anything outside it.
        #   - Otherwise universe = ALL_TYPES (the gaming taxonomy);
        #     the *blacklist* check below handles blacklisted-but-known
        #     types with its own clearer error message.
        if isinstance(allowed_override, list) and allowed_override:
            if atype not in allowed_override:
                raise ValueError(
                    f"LLM picked article_type={atype!r} outside the "
                    f"site's allowed_article_types={allowed_override}"
                )
        elif atype not in ALL_TYPES:
            raise ValueError(
                f"LLM picked unknown article_type={atype!r}; "
                f"allowed={ALL_TYPES}"
            )

        # Look up the chosen keyword's game from the candidate list so
        # we can apply the right per-game blacklist (the LLM may or may
        # not echo `game` correctly; trust the DB-derived value).
        chosen = next(
            (c for c in candidates if c["keyword_id"] == choice.get("keyword_id")),
            None,
        )
        game_slug = (
            (chosen or {}).get("game")
            or choice.get("game")
            or "unknown"
        )
        # Defense in depth: even if the LLM ignored the per-game blacklist,
        # we re-check the chosen type against the right blacklist.
        effective_blacklist = (
            per_game_blacklist.get(game_slug, type_blacklist)
            if game_slug != "unknown" else type_blacklist
        )
        if atype in effective_blacklist:
            raise ValueError(
                f"LLM picked blacklisted article_type={atype!r} "
                f"for game={game_slug!r}; blacklist={effective_blacklist}"
            )

        # Normalize output shape: both keys present so callers can use
        # either; older orchestrator reads `article_type`. `game` is
        # always set so downstream agents can pick the right wiki sources.
        choice["article_type"] = atype
        choice["suggested_article_type"] = atype
        choice["game"] = game_slug

        # Annotate selector output with full track-record snapshot so
        # post-hoc audits can see what the LLM was looking at.
        choice["_diversity_snapshot"] = {
            "track_record": track_record,
            "type_blacklist_effective": effective_blacklist,
            "game_chosen": game_slug,
            "game_priorities": game_priorities,
            "type_adjustments": {k: {"delta": v[0], "label": v[1]} for k, v in type_adj.items()},
        }
        return choice
