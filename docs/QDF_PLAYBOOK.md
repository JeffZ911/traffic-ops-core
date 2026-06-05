# QDF Playbook — full-site freshness SEO + AI self-improvement loop

Canonical reference for the QDF (Query-Deserves-Freshness) system across all
sites. Any agent/human touching trend generation, cluster linking, the
retrospective, or onboarding a new site MUST read this first.

Strategy owner intent: **win IMPRESSIONS first on young, low-authority sites
via the QDF freshness window, then convert to CLICKS** — a self-reinforcing
loop (more impressions → more clicks → more authority → more impressions).

---

## 1. What QDF is (and its honest limits)

QDF = Google's freshness re-ranking. When a query spikes (breaking event, new
release, seasonal surge), Google temporarily favours *fresh* pages over
established ones for a short window (hours–3 days). In that window a new page
can rank **without** the authority it would normally need — the one lever a
zero-authority site can actually pull.

**Hard constraints (do not pretend otherwise):**
- Google has **no compliant push-crawl API** for general content (the Indexing
  API is JobPosting/livestream only). So Google-side QDF is bounded by how fast
  Google chooses to crawl the site — i.e. by crawl trust, which a brand-new
  site lacks. Newly published trend pages often sit `COLD` (unknown to Google)
  for a while. This is expected, not a bug.
- **IndexNow pushes Bing / Yandex / DuckDuckGo (+ AI search), NOT Google.** So
  the *immediate* QDF surface for a new site is Bing-side; Google ramps as
  crawl trust builds (request-indexing + earned links).
- QDF therefore runs **in parallel with** the authority/indexing work
  (daily GSC request-indexing, guest-post links). It is not a substitute.

**Eligible keyword types:** breaking events (recalls, launches, outages,
incidents), periodic (annual expos, holiday sales, yearly product cycles),
high-churn (version updates, monthly roundups, latest comparisons).
**Not eligible:** evergreen definitions/explainers — those use normal SEO.

---

## 2. The daily automated loop (all 3 sites, per-niche)

```
03:00 UTC  trend scan (Gemini + Google Search, niche-scoped, anti-fabrication)
   → inject prior AI guidance into the prompt   (self-improvement, §5)
   → seed source='trend' keywords (freshness bonus)
same run   KeywordSelector: fresh trend (+150, age<1d) DOMINATES → published
   → IndexNow + sitemap resubmit (push to Bing; Google via sitemap lastmod)
   → QDF cluster: inbound links from established pages → the fresh page
   → Build → Deploy → verify live (auto-redeploy if broken)
next day   QDF retrospective: GSC measure → Gemini-Pro guidance → store
```

Each site does this **only in its own niche** — see §6.

---

## 3. Components (traffic-ops-core)

| Concern | Code | Notes |
|---|---|---|
| Trend keyword gen | `scripts/keyword_gardener.py` `run_trending` | 3 niches: `TREND_PROMPT` (gaming), `ECOM_TREND_PROMPT`, `SECURITY_TREND_PROMPT`. salvage + max_tokens 6000 for reliability. Injects prior AI guidance. |
| Same-run fast publish | `src/agents/keyword_selector.py` `_trend_freshness_bonus` | age<1d → **+150** (dominates evergreen) → published the same run it's seeded → hits the 3-12h window. Decays 80/30/10. |
| Push to crawler | `scripts/indexnow_submit.py` (Bing side) + `scripts/resubmit_sitemap.py` (Google via lastmod) | Wired in all 3 content workflows. |
| Inbound cluster links | `scripts/qdf_cluster.py` | Established pages → fresh trend pages where the topic already appears. IDF niche-core filter + per-anchor diversity cap + per-page cap. Reuses `inject_internal_links` (frontmatter-safe, idempotent). Same-site only. |
| Live health + recovery | `scripts/verify_live_deploy.py` | Post-deploy: homepage + newest article must serve real HTML (200 + `</html>`/`<title>` + min length). Broken → re-push built dist once + alert. |
| Next-day retrospective | `scripts/qdf_report.py` | Per trend page: GSC `urlInspection` coverageState + `searchanalytics` page perf → WIN/PENDING/COLD → rolling `/todos` card. Flags COLD≥3d as the iteration signal. |
| AI self-improvement | `qdf_report._ai_retrospect` + `src/utils/qdf_memory.py` | `gemini-3.1-pro-preview` analyses outcomes + selection rationale → forward guidance → stored (metrics_raw payload `qdf_learning`) → injected into next `run_trending`. |

Workflows: `content_quvii.yml`, `content_daily.yml` (ntecodex),
`content_pixelmatch.yml` — structurally identical; steps mirror across all 3.

---

## 4. AI models in the pipeline

Only **Gemini** is wired (`src/utils/llm.py` → `GeminiLLMProvider`; Anthropic/
OpenAI are not enabled). Keys: `GEMINI_API_KEY` + per-site `*_GEMINI_API_KEY`.

| Step | Model | Why |
|---|---|---|
| Trend keyword gen | per-site `text_provider.keyword_research_model` (default `gemini-3-flash-preview`) + Google Search grounding | high-volume, grounded |
| **QDF retrospective / guidance** | **`gemini-3.1-pro-preview`** | best reasoning for "what worked + how to iterate" |
| Writing / QA | `text_provider.writing_model` / `qa_model` | — |
| cluster / verify / report-data | none | deterministic |

Fabrication guard stays ON everywhere (`factual_accuracy=0`): trend prompts
forbid inventing events; article-time QA + inline-citation binding catch any
slip → such a page fails QA, never publishes.

---

## 5. The self-improvement loop (knowledge flows)

```
publish trend pages
   → GSC measures each (coverage_state + impressions/clicks/position)
   → gemini-3.1-pro-preview reads outcomes + WHY each was picked (notes)
   → writes: retrospective + imperative selection guidance
   → qdf_memory.save_qdf_learning()  (metrics_raw 'qdf_learning')
   → next run_trending injects latest guidance into the trend prompt
   → better keywords → publish → measure → …
```

Objective is hard-coded into the analyst prompt: **impressions first, then
clicks.** The guidance is written *as instructions to the keyword generator*
and applied verbatim next run. Cost ~$0.004/retrospect. Visible at the bottom
of the `/todos` "QDF 次日复盘 — <site>" card.

---

## 6. Per-niche isolation (hard rule — never break)

"全站同步执行" = **every site runs QDF in ITS OWN niche**, never the same
content across sites. Cross-niche leakage (e.g. a gaming keyword in the
home-security pool) is a production incident.

Guards in place:
- `keyword_gardener`: unknown/missing `sites.config.niche` → **refuse to
  generate** (no more defaulting to "gaming"). `security_cameras` skips the
  generic top-up (it would fall through to the gaming prompt).
- `qdf_cluster`: same-site only.
- Niches today: `gaming` (ntecodex), `ecommerce_tools` (pixelmatch),
  `security_cameras` (quvii).

---

## 7. Adding a new site

1. Insert the `sites` row with `config.niche` set to a KNOWN niche (or add a
   new niche + a `*_TREND_PROMPT` + `*_TYPE_HINTS` first — never reuse another
   niche's prompts).
2. Copy a `content_*.yml` workflow; swap `SITE_DOMAIN`, the `*-site` repo +
   PAT, the CF Pages project name, and the per-site GA4/GSC secret prefix.
   Every QDF step (trend scan, cluster, verify, retrospective) carries over.
3. Seed an initial keyword pool (a `bootstrap_*` or `seed_*` script) — the
   trend loop tops up from there.
4. Confirm IndexNow key in `sites.config.indexnow_key` + hosted `<key>.txt`.

---

## 8. Operating reality

- New trend pages start `COLD`; they progress `COLD → PENDING → WIN` as crawl
  trust accrues. Track the progression on the retrospective card; don't expect
  same-day Google wins on a young site.
- The fastest QDF wins come from **hyper-acute, entity-specific breaking
  events** (exact model + recall/outage + urgent intent) — the AI guidance
  already steers selection this way.
- QDF is one lever. Keep the indexing basics running (daily request-indexing,
  earned links) — they unlock the crawl speed QDF depends on.
