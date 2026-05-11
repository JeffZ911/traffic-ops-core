# GSC Feedback Loop

How NTE Codex turns Google Search Console signal into the next day's
article topic. Updated 2026-05-11.

---

## Why this exists

Before Phase 2.1, `KeywordSelectorAgent` chose tomorrow's topic by looking
only at `keywords.priority_score`, which is *static* — a number we set when
we seed a keyword. That meant:

- Queries Google was already surfacing the site for, but for which we had
  no dedicated article, sat unaddressed indefinitely.
- Articles ranked on page 2 (avg position 11–30) — the cheapest possible
  ranking win in SEO — were invisible to the scheduler.
- The high-CTR title shapes that actually worked on the site got no
  reinforcement in future titles.

The feedback loop closes those three gaps.

---

## Data flow

```
Mon 03:00 UTC                                       Tue–Sun 02:00 UTC
─────────────                                       ─────────────────
seo_intelligence (weekly)                           content_daily (every day)
│                                                                 │
├─→ fetch_gsc(last 14d)                                            │
│      └─ dimensions=[query], [page], [page,query]                 │
│                                                                  │
├─→ discover_longtail                                              │
│      └─ INSERT keywords                                          │
│         source='gsc_longtail'                                    │
│         priority_score=90                                        │
│         status='planned'                                         │
│         notes='Discovered from GSC: N imp, pos P, ctr%' ───┐    │
│                                                              │   │
├─→ find_rewrite_candidates  ──────────────────┐              │   │
│                                              │              │   │
├─→ extract_high_ctr_patterns ──────────────┐  │              │   │
│                                            │  │              │   │
└─→ daily_reports.data_snapshot ─────────────┘  │              │   │
       .seo_intelligence  =                     │              │   │
         { longtail_discovered: N,              │              │   │
           rewrite_candidates: [...],           │              │   │
           high_ctr_examples: [...] }           │              │   │
                                                │              │   │
                                                │              ▼   ▼
                                                │       KeywordSelectorAgent
                                                │            │
                                                │       ┌────┴────┐
                                                │       │ pulls 50 candidates  │
                                                │       │ adds +20 priority    │
                                                │       │ to gsc_longtail rows │
                                                │       │ surfaces type-deficit │
                                                │       │ in LLM prompt        │
                                                │       └────┬────────────────┘
                                                │            │
                                                ▼            ▼
                                       (operator reads      one keyword picked,
                                       weekly report,       article generated
                                       decides which
                                       rewrites to
                                       schedule manually)
```

---

## Components

### 1. `src/maintenance/seo_intelligence.py`

Single Python module, runs as a CLI:

```bash
python -m src.maintenance.seo_intelligence            # full run
python -m src.maintenance.seo_intelligence --dry-run  # compute, no writes
```

Thresholds (all easily tunable at the top of the file):

| Constant | Value | Purpose |
|---|---|---|
| `LOOKBACK_DAYS` | 14 | Window of GSC data to read |
| `MIN_IMPRESSIONS` | 5 | Min impressions to count as long-tail |
| `MIN_POSITION` | >5 | Skip queries already ranking great |
| `MAX_POSITION` | ≤50 | Skip queries with no realistic upside |
| `REWRITE_MIN_IMPRESSIONS` | 50 | Min imp to flag for rewrite |
| `REWRITE_POSITION_LOW/HIGH` | 11 / 30 | "Page 2" band — cheapest CWV win |
| `HIGH_CTR_THRESHOLD` | 0.03 (3%) | Title-pattern qualifying CTR |

### 2. `.github/workflows/seo_intelligence_weekly.yml`

- Cron: `0 3 * * 1` → Monday 03:00 UTC
- Manual trigger supports `dry_run=true`
- Sends an email summary on success; on failure invokes
  `src.utils.send_alert --severity=warning`.

### 3. `src/agents/keyword_selector.py` updates

Two coupled changes:

1. **+20 priority bonus** for `source='gsc_longtail'` rows, applied at the
   sort step so the LLM sees them ranked higher in the candidate list.
2. **Type-deficit snapshot** injected into the prompt — both 7-day and
   14-day distribution, plus a per-type deficit metric (negative = under-
   published vs. the even-cadence target).

The LLM is told:
> "Keywords with source='gsc_longtail' should win ties: Google already
> surfaces the site for those queries, so converting them is the cheapest
> ranking win."

---

## Monitoring

Watch these in the dashboard:

| Signal | What it means |
|---|---|
| `keywords.source = 'gsc_longtail'` row count, weekly delta | Long-tail discovery is finding new queries each week |
| `daily_reports.data_snapshot.seo_intelligence.summary.longtail_discovered` time-series | Weekly inflow into the keyword pool |
| Articles whose `published_url` appears in `rewrite_candidates` two Mondays running | Real rewrite targets — the operator should act |
| Email subject `[ntecodex] weekly SEO intelligence — YYYY-MM-DD` arriving Monday morning | Loop is alive |
| Email NOT arriving | Either GSC OAuth refresh-token expired (Testing-mode OAuth lasts 7 days), SMTP creds wrong, or the run crashed — check Actions tab |

---

## Expected timeline

| Phase | Expected long-tail / week | What you do |
|---|---|---|
| Week 1 (Mon after this ships) | 0–2 | Confirm the email lands. GSC has barely any data yet. |
| Week 2–4 | 5–15 | Don't act. Let the system fill the keyword pool. |
| Week 5–8 | 20–40 | Should start seeing diversity-balanced articles with `source='gsc_longtail'` keywords on cron output. |
| Week 8+ | 30+ | Compounding: each new article surfaces for more queries → more long-tail → more articles. |

If after **4 weeks** the weekly count is still 0, the loop is broken.
Likely causes (in descending probability):

1. GSC OAuth `refresh_token` expired (Testing-mode OAuth is 7-day TTL —
   we keep promising ourselves we'll push the consent screen out of Testing)
2. Site too new — Google hasn't indexed enough pages to generate
   impressions in interesting query bands
3. Thresholds too strict — drop `MIN_IMPRESSIONS` to 3

---

## What this is NOT

- **Not a live rewriter.** Rewrite candidates are *listed* in the report.
  No agent automatically picks them up — that's a deliberate handbrake
  while we observe whether the discovery quality is good enough.
- **Not a real-time signal.** GSC API has ~2-day processing lag, so the
  window runs from `today-15` to `today-2`.
- **Not a substitute for keyword research.** It's purely additive: finds
  what Google already shows us for. Net-new opportunity discovery still
  requires `keyword_gardener` + human judgment.

---

## Anti-patterns

- **Don't lower `MIN_POSITION`** below 5. Queries already ranking top-5
  don't need a dedicated article; writing one risks cannibalization.
- **Don't raise `MAX_POSITION`** above 50. Positions 50–100 are noise
  and the resulting keyword pool will be huge but mostly worthless.
- **Don't write to `articles.metadata`** — that column doesn't exist on
  the current schema. The rewrite list lives in `daily_reports.data_snapshot`.
- **Don't run this more than once a week.** It writes one row per Monday
  into `daily_reports`; re-running same Monday is idempotent (ON CONFLICT
  UPDATE merges) but won't surface fresher data because GSC's 2-day lag
  means daily runs add no new signal.
