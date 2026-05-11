# Phase 1.B + 2.1 Completion Report

Date: 2026-05-11.

---

## What shipped (Phase 1.B)

### Site-level improvements (already live before this report)
- ✅ Dead-link root cause fix (subfolder slug + 301 redirect)
- ✅ Related Guides component on every article
- ✅ Auto internal-link injection (Nanally / Sakiri etc. cross-link)
- ✅ Multi-level Breadcrumb + JSON-LD BreadcrumbList
- ✅ Pagefind static search in header (`/` keyboard shortcut)
- ✅ Mobile performance 76 → 97 (deferred AdSense + GA4 boot)

### Content infrastructure (this PR)
- ✅ **Part A.1**  `KeywordSelectorAgent` diversity weighting — type-deficit
  snapshot + last-day anti-repeat. See `docs/CONTENT-DIVERSITY-STRATEGY.md`.
- ✅ **Part A.2**  `ImageAgent` upgraded to 1 hero + up to 6 inline images
  per article. Each inline image is keyed to a specific H2 section topic.
  Cap of 7 images / article enforced in code.
- ✅ **Part A.3**  `_inline_image_inject` helper interleaves each inline
  image directly below its matching H2 in the markdown body. Skips code
  fences, the Sources H2, and prevents duplicate-injection on re-runs.
- ✅ **Part A.4**  `.github/workflows/retrofit_images.yml` — manual
  `workflow_dispatch` to regenerate 1+6 images for every currently-
  published article and re-inject them into the body. Hard $3 cap.
- ✅ **Part A.5**  `/banners/current` now surfaces every `article_type='news'`
  + any guide with "banner" in the title. `/banners/upcoming` has a
  breadcrumb-correct placeholder. Banner batch dispatch workflow
  (`banner_batch.yml`) seeds 8 banner keywords + generates N news
  articles in one click.

## What shipped (Phase 2.1)

- ✅ **Part B.1**  `src/maintenance/seo_intelligence.py` — weekly GSC
  feedback loop. Discovers long-tail queries, flags page-2 rewrite
  candidates, extracts high-CTR title patterns, persists a structured
  weekly report to `daily_reports.data_snapshot.seo_intelligence`.
- ✅ **Part B.2**  `KeywordSelectorAgent` now adds a **+20 priority
  bonus** for `source='gsc_longtail'` keywords, surfaced in the prompt
  as `priority_with_bonus`. LLM is told to prefer them on ties.
- ✅ **Part B.3**  `.github/workflows/seo_intelligence_weekly.yml` —
  every Monday 03:00 UTC, plus `dry_run=true` manual dispatch.
- ✅ **Part B.4**  `docs/SEO-FEEDBACK-LOOP.md` — full mechanism doc with
  thresholds, monitoring signals, expected timeline, anti-patterns.

---

## Current state snapshot

| Metric | Value |
|---|---|
| Indexable URLs in sitemap | 16 |
| Published article-type breakdown (before retrofit/batch) | 2× character_db, 2× guides, 1× boss_guide, 1× reroll, 1× tier_list-source, 1× faq-source |
| Banner / news articles | **0** (will be 5 once `banner_batch.yml` is dispatched) |
| Mobile Performance (Lighthouse, PSI-strict throttling) | **97** / LCP 2.1s / FCP 1.7s / TBT 0ms |
| Desktop Performance | **96** / LCP 1.2s |
| Mobile article Performance | **93** |
| Core Web Vitals on `/` | ✅ all in "good" band |
| Active Cloudflare Pages projects | 2 (`ntecodex` site + `traffic-ops-dashboard`) |
| Active GitHub Actions workflows | 4 (`content_daily`, `model_health_check`, `retrofit_images`, `banner_batch`, `seo_intelligence_weekly`) |
| Cumulative spend so far | ~$13 (per Phase 1.A report) + this Phase ~$0 (no real LLM runs in this PR — all dispatches are operator-triggered) |

---

## Open operator actions

The user can trigger these via Actions tab → "Run workflow":

1. **`Retrofit images for published articles`** — regenerate 1+6 images
   for all 8 published articles + interleave inline images into body.
   Cost: ~$2.16 worst case. Budget: $3.
2. **`Banner batch`** — seed banner keywords + generate 5 news articles +
   1+6 images each + deploy. Cost: ~$2-3. Budget: $3.

The daily content cron is unchanged in cadence but will produce
diversity-aware article picks (Part A.1) and 1+6 images per article
(Part A.2 / A.3) starting with tomorrow's run.

---

## 14-day observation period — what to watch

| Day | Signal | What it means |
|---|---|---|
| D+1 | Daily cron picks a non-build article_type | Diversity weighting working |
| D+1 | New article has 4–7 images interleaved with H2s | A.2 + A.3 working |
| D+7 | First Monday email `[ntecodex] weekly SEO intelligence` | B.1/B.3 wired correctly. Likely 0 long-tail (GSC has no data yet) |
| D+7 | `keywords` table source breakdown | gsc_longtail rows starting to appear |
| D+10 | First content_daily run picks a `gsc_longtail` keyword | B.2 working end-to-end |
| D+14 | Mobile Lighthouse on a fresh article (7 images) | ≥85 confirms image-heavy pages don't regress CWV |
| Any day | Email alert from a workflow | Pipeline broke. Read message + open Actions tab. Fix root cause. |

## 14-day red lines (observation-period freeze)

- ❌ Don't modify `BaseLayout.astro` (perf is stable, sensitive)
- ❌ Don't change site colors / typography
- ❌ Don't add manual `<ins>` ad blocks (Auto Ads is fine)
- ❌ Don't change cron schedules
- ❌ Don't delete published articles
- ✅ Genuine P0 bug fixes OK
- ✅ Email-alert remediation OK

## 14 days later — 4 decision branches

| If you see... | Then... |
|---|---|
| **A.** GSC > 200 imp/day + AdSense approved | Phase 2.2 Option B/C — multi-brand SaaS lift |
| **B.** GSC > 100 imp/day + AdSense approved but few clicks | Phase 2.2 Option C — title rewrite + low-rank article overhaul |
| **C.** GSC < 50 imp/day | Phase 2.2 Option D — production scale-up (3 articles/day) |
| **D.** AdSense rejected | Read rejection reason, fix, re-apply. No new code until resolved. |
