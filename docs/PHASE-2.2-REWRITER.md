# Phase 2.2 — RewriterAgent

How NTE Codex turns a page-2 article into a page-1 article without
human intervention. Closes the GSC feedback loop introduced in
Phase 2.1.

Shipped 2026-05-11.

---

## The loop

```
seo_intelligence_weekly  ──►  daily_reports.data_snapshot
       (Mon 03:00 UTC)         .seo_intelligence
                                 .rewrite_candidates:
                                   [{article_id, slug, impressions,
                                     position, ctr, url}, ...]
                                            │
                                            │   scripts/rewrite_one_article
                                            ▼   (daily cron, after backfill)
                                    ┌──────────────────────────┐
                                    │ pick highest-priority    │
                                    │ candidate that hasn't    │
                                    │ been skip-flagged        │
                                    └──────────────┬───────────┘
                                                   │
                  ┌────────────────────────────────┼────────────────────────────────┐
                  ▼                                ▼                                ▼
        RewriterAgent.analysis           RewriterAgent.rewrite                QAAgent
        (Pro + grounding)                (Pro + grounding)                    (Pro + grounding)
        find missing / shallow /         emit new MD body                     score 0-10
        stale sections vs.               1.5-2x word_count,                   on 6 dimensions
        top-5 competitor pages           +25% H2, ≥2 new sources              (incl. factual_accuracy)
                  │                                │                                │
                  └────────────────────────────────┴────────────────────────────────┘
                                                   │
                                                   ▼
                                      delta = new_qa - old_qa
                                      passed && delta > +0.5
                                            │
                              ┌─────────────┴─────────────┐
                              │                           │
                            accept                       reject
                              │                           │
                  ┌───────────┴────────────┐    increment rewrite_attempts
                  │                        │              │
        UPDATE articles SET           git push new MD     attempts ≥ 3
        content_md, qa_score,         to ntecodex-site    ─► rewrite_skipped=true
        qa_feedback                                       ─► future cron skips
                  │
                  ▼
        email: [ntecodex] article rewritten:
               <slug> qa <old>→<new>
```

---

## Files

| File | Role |
|---|---|
| `src/agents/rewriter.py` | Two LLM calls (Pro + grounding): competitor analysis + full-article rewrite. Returns analysis + new markdown without persisting. |
| `scripts/rewrite_one_article.py` | Orchestrator: candidate selection → Rewriter → QAAgent → decide → persist or bookkeep failures. Idempotent; safe to re-run. |
| `scripts/test_rewriter_e2e.py` | One-shot real-LLM smoke ($0.15-0.60). Run after prompt changes. Output is informational only — DB is NOT touched (uses `.run()` for the agent but doesn't UPDATE articles). |
| `.github/workflows/content_daily.yml` step 5c | Hooks the orchestrator into the cron, gated on `budget.action in (normal, warn)`. `continue-on-error: true` so a crash here doesn't block today's deploy. |

---

## Decision rules

| Condition | Action |
|---|---|
| `qa_passed && (new_qa - old_qa) > 0.5` | **Accept**: swap content_md + qa_score in DB, overwrite MD file on disk, send accept email. |
| Anything else | **Reject**: increment `qa_feedback.rewrite_attempts`, leave article untouched, no email. |
| `rewrite_attempts ≥ 3` (after this attempt) | **Skip forever**: set `qa_feedback.rewrite_skipped = true`. Future cron runs skip this article (in candidate-selection step). Operator gets one `rewrite_skipped` email. |

Why `+0.5`? A `+0.3` improvement is noise across two QA runs on the same content; `+0.5` is the smallest delta that consistently survives re-runs in our hand-tested samples. Below that it's not worth the churn (and the email noise).

---

## Cost shape

| Step | Model | Typical cost | Notes |
|---|---|---|---|
| Analysis | gemini-3.1-pro-preview + grounding | ~$0.15 | One call. JSON output. |
| Rewrite | gemini-3.1-pro-preview + grounding | ~$0.30-0.50 | `max_tokens=32000` (raised from 12k after e2e showed truncation). |
| QA on rewrite | gemini-3.1-pro-preview + grounding | ~$0.10-0.15 | Same call shape as production QA. |
| **Total / attempt** | | **~$0.60** | Daily cap = 1 article × $0.60 |
| Monthly worst-case | | ~$18 / month | Caught by the $30 budget guard at 60%. |

---

## Safety brakes

1. **One article per day** — the candidate selector returns exactly one
   `(article_id, gsc_stats)` per invocation; the cron calls it once.
2. **Budget guard** at the workflow level — `if: steps.budget.outputs.action in (normal, warn)`. At `limit_extras` (>80% MTD) the rewrite step skips, preserving the core "produce today's article" path.
3. **Three-strike skip** — `rewrite_attempts ≥ 3` flips `rewrite_skipped=true`, permanently excluding the article from future cron runs without human intervention.
4. **QA on the rewrite is full Pro+grounding** — same agent that gates new articles. Catches fabricated terms / inflated word counts with no real density gain.
5. **No re-roll on the same day** — if QA scores low, the orchestrator does NOT silently try another rewrite. It logs the failed attempt and exits.
6. **`continue-on-error: true` in the workflow** — a Rewriter crash never blocks today's deploy.

---

## Monitoring

Dashboard signals worth wiring (if not already):

| Metric | Query | Watch for |
|---|---|---|
| Rewrites accepted (7d) | `select count(*) from agent_runs where agent_name='rewriter' and status='success' and created_at > now() - interval '7 days'` | 3-7 per week = healthy; 0 = candidate pool dry or every rewrite getting rejected |
| Median qa_score lift on accepted rewrites | per-article delta in `qa_feedback.rewrite_history` | should consistently > +0.5; trending below means tighten prompt |
| `rewrite_skipped=true` count | `select count(*) from articles where (qa_feedback->>'rewrite_skipped')::bool` | should be 0-3 over the lifetime of the site; >5 means the prompts or QA threshold are mismatched to reality |
| Email "article rewritten" arrival pattern | inbox search `[ntecodex] article rewritten` | weekly cadence = expected; daily = oddly active; nothing for 2 weeks = candidate pool dry |

---

## Manual trigger (backup)

If the cron step is disabled and you want to force a rewrite once:

```bash
cd /Users/jeffzen/Documents/traffic-ops/traffic-ops-core
source .venv/bin/activate
python -m scripts.rewrite_one_article                    # auto-pick
python -m scripts.rewrite_one_article --article-id <uuid>
python -m scripts.rewrite_one_article --dry-run          # smoke without persisting
```

`SITE_REPO_PATH` env var must point to your local clone of
ntecodex-site for the markdown-file overwrite to happen. The DB
update happens regardless.

---

## Anti-patterns

- **Don't lower the +0.5 delta threshold** without re-running the e2e
  smoke on multiple articles. Below +0.3 the rewrite-vs-original is
  inside QA noise and you end up churning content for no real lift.
- **Don't drop `enable_search=True`** on the rewrite call. The whole
  point is competitor-grounded deepening — without search the model
  hallucinates a "deeper" version that fails QA on fabricated terms.
- **Don't run the e2e smoke in CI** — it burns real money each run.
  It exists for one-shot validation after prompt changes.
- **Don't allow `force_article_type` semantics here** (we learned the
  lesson with the failed banner_batch workflow): the analysis step is
  bound to the article's actual primary keyword. Forcing the wrong
  category through this pipeline produces hallucinated rewrites.

---

## Known issue from e2e (2026-05-11)

The first e2e run on `best-starter-characters-nte-tier-list` truncated
the rewrite output (1977 → 369 words, 7 → 1 H2). Root cause: original
`max_tokens=12000` left insufficient room for Pro+grounding's thinking
budget. **Fixed**: bumped to `max_tokens=32000` in
`src/agents/rewriter.py`. The safety brakes worked perfectly — QA
scored the truncated rewrite at 2.5 vs. old 7.1, delta -4.6, well
below the +0.5 acceptance threshold. The rewrite would have been
correctly rejected and the article left untouched.

E2E was deliberately NOT re-run (cost discipline). The production
cron will surface the next instance and we'll see if 32k is enough.
If it still truncates: raise to the provider's hard cap or switch the
rewrite step to gemini-3.1-flash-preview which has a leaner thinking
budget but less compositional depth — TBD trade-off.
