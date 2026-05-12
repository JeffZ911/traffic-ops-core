# Content Velocity (Phase 2.4)

How ntecodex.com moved from 1 attempt/day to 24 attempts/day on
2026-05-12, and the guardrails that keep that velocity sustainable.

---

## The shift

| Metric | Phase 2.3 (yesterday) | Phase 2.4 (today) |
|---|---|---|
| Cron frequency | 1× / day | **6× / day** (4-hour cadence) |
| Articles per cron | 1 | **4** |
| Daily attempts | 1 | **24** |
| Pass-rate target | not optimized | **≥ 65%** |
| Expected publishes / day | ~0.5 | **15-17** |
| Monthly budget | $30 | **$150** |
| At 65% pass-rate + $0.40/article: monthly burn | $12 | **~$200 worst case** |

Worst-case spend ($204 at 100% pass) exceeds the $150 budget, but the
`budget_guard` flips to `limit_extras` at 80% ($120) and `pause_all` at
95% ($142), so the actual ceiling is **hard $142** per month. That's
the safety net.

## What changed in code

### 1. `sites.config.monthly_budget_usd`: 30 → 150
One SQL update. Budget guard thresholds (50/80/95) are percent-based
so they re-anchor automatically.

### 2. `KeywordSelector` 4-band aggressive thresholds
3-band → 4-band, with stronger reward at the top and stronger penalty
at the bottom:

|  pass_rate | adjustment | rationale |
|---|---|---|
| ≥ 70% | **+50** | Push hard toward winners — at 24/day a high-pass type is gold |
| 50 – 70% | **+20** | Mild reward |
| 30 – 50% | 0 | Neutral |
| < 30% | **−80** | Strong penalty — wastes ~$1/day at 24 attempts |
| 3 consec fails | **−150** | Force-avoid recent regression |

The old `QA_RATE_REWARD` constant is kept as alias for back-compat.

### 3. `scripts/run_batch_smoke.py`
Replaces `run_one_article_smoke` in the cron. Per-iteration:
- re-checks `budget_guard.action`; on `pause_all` aborts the loop
- wraps `run_one_article` in try/except so a single crash doesn't
  sink the batch
- accumulates per-article cost + game distribution in the scorecard

### 4. `.github/workflows/content_daily.yml` schedule
```
- cron: '0 2 * * *'
- cron: '0 6 * * *'
- cron: '0 10 * * *'
- cron: '0 14 * * *'
- cron: '0 18 * * *'
- cron: '0 22 * * *'
```
Concurrency group `content-daily` (existing) prevents overlap if a
slow run pushes into the next slot. `cancel-in-progress: false` means
the new slot waits.

### 5. `src/maintenance/velocity_check.py`
Tail step of every cron. Looks at the last 3 calendar days of
qa_passed vs qa_failed and decides:

|  3-day pass-rate signal | action |
|---|---|
| all 3 days < 50% | warning email — "slowdown_alert" |
| all 3 days > 75% | info email — "headroom_alert" (push to 8 cron/day) |
| mixed | no-op |
| < 1 attempt per day | no-op (insufficient data) |

Dedup'd via `daily_reports.data_snapshot.velocity_alert` so the same
alert doesn't fire 6× / day during a slowdown.

---

## Monitoring (recommended Dashboard tiles)

| Tile | Query / signal | Healthy band |
|---|---|---|
| **Articles published / day** | `count(*) from articles where status='published' and created_at::date = today` | 12-18 |
| **3-day rolling pass-rate** | `velocity_check` output | 50-75% |
| **Per-game distribution / 7d** | `articles group by outline->>'game' last 7d` | each primary game ≥ 5 |
| **Daily $ spend (agent_runs)** | `sum(cost_usd) group by created_at::date` | $4-7/day |
| **MTD spend %** | `budget_guard` JSON output | < 80% by day 25 |

## Risk register

| Risk | Trigger | Mitigation |
|---|---|---|
| **Quality regression at scale** | velocity_check fires `slowdown_alert` 3+ consecutive days | Don't lower QA threshold; instead seed more keywords in winning categories, archive losers, consider rewriter run on borderline articles |
| **Budget overrun** | Spend hits 95% before end of month | `budget_guard.pause_all` exits the cron immediately with email; auto-resumes on month rollover |
| **AdSense violation** | Auto-flagged for excessive AI-generated content | Mitigation already in place: P0.5 honesty rule, Z post-hoc filter, manual review of weekly_pass_rate < 60% reports |
| **Cron concurrency overlap** | A 4-hour cron slot runs past its budget | `concurrency: group: content-daily, cancel-in-progress: false` — next slot queues, doesn't double-fire |
| **Pool exhaustion** | All 120 planned keywords consumed in 5 days | Gardener `--auto-balance-types` runs daily; long-tail GSC discovery (weekly) tops up. If both fall behind, `keyword_gardener --force` on demand. |

## Anti-patterns

- **Don't drop QA `min_quality_score`** below 7.0 to chase pass-rate.
  Cheap publishes with thin facts hurt the brand and AdSense long-term.
- **Don't add a 7th game** without first validating the existing 5 are
  publishing balanced (≥3 articles/week per primary game). Adding more
  surface area before consolidating winners just dilutes velocity.
- **Don't raise `batch_smoke --count` above 6**. Each batch run is
  ~10-15 min; >6 articles risks running past the next cron slot.
  Increasing daily volume is done via more cron, not bigger batches.
- **Don't disable `velocity_check`**. The slowdown alert is the only
  signal you'll get before three days of burned budget pile up.
