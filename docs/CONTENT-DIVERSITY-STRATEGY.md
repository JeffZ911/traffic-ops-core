# Content Diversity Strategy

How we keep the published catalog from collapsing into one genre.
Phase 1.B / Part A.1 — shipped 2026-05-11.

---

## The problem

`KeywordSelectorAgent` picks one keyword + article_type per day for the
daily cron. The LLM, given only `priority_score`, tends to pile up on
whatever's most popular in the candidate pool (often "build" guides for
hot characters). Result: after 8 published articles, the site had 4
guides, 2 character_db, 1 boss, 1 reroll, and **zero** banner / tier_list
/ FAQ / comparison content — leaving entire sub-sections of the site
empty.

## The fix

Two coupled signals injected into the selector prompt every run:

### 1. Type-deficit snapshot

We compute the published count per `article_type` over the last 14 days
and compare to an even cadence target (`14 / 9 ≈ 1.55` articles per type
per 14 days). The per-type deficit (`published - target`) is rendered
into the prompt **sorted by deficit ascending** — so the most under-
published types appear first.

```
Type deficit (negative = under-published vs. target cadence):
{
  "news": -1.55,
  "tier_list": -1.55,
  "faq": -0.55,
  "comparison": -0.55,
  "weapon_db": -0.55,
  "boss_guide": 0.45,
  "character_db": 1.45,
  "reroll": 0.45,
  "build": 2.45
}
```

The prompt rule:

> "Prefer article_types with the LARGEST deficit. These should be your
> strong default unless a top-priority keyword clearly fits a different
> type."

### 2. Last-day type guardrail

Soft anti-repeat rule:

> "Do NOT pick the same article_type two days running unless that type
> still has the largest deficit AFTER today's planned write."

This prevents the LLM from anchoring on yesterday's choice and keeps
the rotation flowing.

### 3. GSC long-tail priority bonus (Part B coupling)

Keywords sourced from `gsc_longtail` get a **+20** priority bonus
surfaced in the candidate list as `priority_with_bonus`. The prompt
explicitly tells the LLM to prefer them on ties, because they're the
cheapest possible ranking win — Google is already surfacing the site
for those queries.

---

## What this is NOT

- **Not a hard quota.** The LLM is still free to pick any type if a
  candidate clearly outranks the type bonus. We don't force "Monday =
  banner day" — that produces worse content than letting the model
  weigh signals.
- **Not a substitute for keyword research.** If the keyword pool has
  zero tier_list-intent keywords, the deficit signal can't conjure one.
  Operators should keep seeding diverse intents via
  `scripts/keyword_gardener` and `scripts/seed_banner_keywords`.
- **Not retroactive.** The diversity weighting only affects future
  cron runs. Existing imbalance must be remedied via manual
  `workflow_dispatch` runs (see `.github/workflows/banner_batch.yml`).

---

## Verification

Check `agent_runs.output->'_diversity_snapshot'` for any
`agent_name='keyword_selector'` row. The selector now annotates its
output with the snapshot it saw:

```json
{
  "keyword_id": "...",
  "article_type": "news",
  "reason": "Banner schedule keywords have +20 long-tail bonus AND news has the largest deficit",
  "_diversity_snapshot": {
    "recent_7": {"build": 2, "character_db": 1},
    "recent_14": {"build": 4, "character_db": 2, "boss_guide": 1, "reroll": 1},
    "deficit": {"news": -1.55, "tier_list": -1.55, ...}
  }
}
```

Good selection traces should show the LLM picking against the deficit
in its `reason` field. If `reason` consistently ignores the snapshot,
either the prompt isn't loading (check `keyword_selector.py:_execute`)
or the LLM is mis-calibrated — try lowering `temperature` from 0.3.
