# Autonomous Operations

Map of every recurring task: what's automated, what guard-rails enforce
sanity, and the one remaining manual step (OAuth refresh-token rotation).

Updated 2026-05-11.

---

## What's now fully automated

| Task | Trigger | What it does | Cost ceiling |
|---|---|---|---|
| **Daily content article** | `content_daily.yml` cron `0 2 * * *` | Collect GA4/GSC → garden keywords → produce 1 article → 1+6 images → publish → deploy | per-article ~$0.40 + $0.27 images |
| **Type-deficit auto-seed** | Inside `keyword_gardener --auto-balance-types` (in daily cron) | If any `article_type` has 0 published / 14d, seed 5-8 keywords for that type | ≤ $0.50 / day, only when starvation detected |
| **Image backfill** | Tail of daily cron (`backfill_one_under_imaged`) | Find 1 oldest published article with < 4 images, regenerate 1+6 set | ~$0.27 / day, capped at one article per cron |
| **Weekly SEO intelligence** | `seo_intelligence_weekly.yml` cron `0 3 * * 1` (Monday 03:00 UTC) | Pull 14d GSC, discover long-tail, mark rewrite candidates, email summary | LLM-free (just API + DB) |
| **Model health check** | `model_health_check.yml` (existing) | Verify configured Gemini models still reachable | $0 |

## What's automatically prevented

| Guardrail | Where | Behaviour |
|---|---|---|
| **Monthly budget guard** | `src/utils/budget_guard.py`, called first in daily cron | Reads `sites.config.monthly_budget_usd` (default $30) and sums `agent_runs.cost_usd` for the current calendar month. Sets a 4-state action: `normal` / `warn` (>50%) / `limit_extras` (>80%) / `pause_all` (>95% or `sites.config.cron_paused=true`). |
| **Workflow short-circuit** | `if: steps.budget.outputs.action != 'pause_all'` on every cost-incurring step | At `pause_all`, the workflow exits 0 (controlled stop, not a failure) after sending one warning email. Resumes automatically on the 1st of next month. |
| **Limit extras** | At `limit_extras`, the auto-balance LLM call and the image-backfill step are skipped; only the core "produce today's article" path still runs. |
| **Per-step cost caps** | Each LLM/image script takes `--budget-usd`. The daily cron's image steps pass `0.50` and `0.40`; the gardener passes `0.50` (covers both top-up and auto-balance). |
| **QA gate** | `QAAgent` rejects articles with `factual_accuracy < 6.0` (and other thresholds); the daily cron tolerates `qa_failed` as a no-op outcome — no markdown is published, no images burnt. |

## Manual kill switch

Set `sites.config.cron_paused = true` from the dashboard or by SQL:

```sql
update sites
   set config = jsonb_set(config, '{cron_paused}', 'true'::jsonb)
 where domain = 'ntecodex.com';
```

The next daily cron will exit immediately with a `pause_all` email.
Toggle back to `false` to resume.

---

## Remaining manual touchpoints

### OAuth refresh-token rotation (the last manual chore)

Google OAuth consent screens in **Testing** mode invalidate
`refresh_token` after **7 days**. When that happens, `run_collectors`
and `seo_intelligence` fail to authenticate to GA4/GSC and silently
return empty data (both jobs have `continue-on-error: true` so the
cron still ships an article, but the analytics feed dries up).

**Symptom**: A few days of zero GSC long-tail discovery + the email
alert from the weekly job stops arriving. Or `[ntecodex] daily cron`
failure email mentions `oauth refresh failed`.

**Current workaround** (manual, ~5 min, must do weekly):

```bash
cd /Users/jeffzen/Documents/traffic-ops/traffic-ops-core
source .venv/bin/activate
python -m scripts.oauth_setup
# follow the prompt — login as admin@sunaofe.com, paste callback URL
```

Then push the new `GOOGLE_OAUTH_REFRESH_TOKEN` to GitHub Secrets.

---

## OAuth permanence — three evaluated options

| Option | Steps | Risk | Recommended? |
|---|---|---|---|
| **A. Publish OAuth app to "In Production"** | (a) Open https://console.cloud.google.com/apis/credentials/consent?project=traffic-ops-495905<br>(b) Click "PUBLISH APP"<br>(c) Submit homepage + privacy policy URL (already at /privacy on ntecodex.com)<br>(d) Wait 4-6 weeks for Google verification | Refresh tokens become **permanent** immediately upon publishing (verification only blocks public consent UX for unverified third parties — your own admin@sunaofe.com login still works fine through verification window per Google docs). Tiny risk: app could be rejected if the privacy policy is inadequate; remediation is just editing the page and re-submitting. | ✅ **Yes — first** |
| **B. Service Account** | (a) Create a service account in GCP<br>(b) Add it as a **GA4 Property user** with Viewer role<br>(c) Add it as an **GSC user** with Restricted Owner<br>(d) Rotate code from `get_user_credentials()` to service-account JSON | Service accounts work cleanly for GA4 (just add as user). For GSC, Google added service-account support in 2023 but it requires the SA email to be added in the *Search Console property settings → Users and permissions*, which only the property *owner* can do. If the property is Domain-style (`sc-domain:ntecodex.com`), ownership is tied to DNS verification — service-account user must be added by the human who originally added the property. | ⚠️ Possible alternative if A fails verification |
| **C. Local launchd weekly re-auth** | (a) Build a small Mac launchd plist that runs `python -m scripts.oauth_setup --non-interactive` Mondays at 09:00<br>(b) Setup browser-automated login flow (Chrome MCP-style) to handle the OAuth consent screen<br>(c) Push fresh token to GitHub Secrets via gh CLI | Depends on Mac being on and unlocked. Browser automation against a Google login flow is fragile — Google routinely changes the consent UI and breaks automation. CAPTCHA may trigger. Not truly zero-touch. | ❌ Brittle, not recommended |

### Recommended execution order

1. **Try Option A first** (one button click, plus paste the privacy URL).
   Even during the 4-6 week verification window, your own
   `admin@sunaofe.com` login produces permanent refresh tokens because
   you're a project owner — the verification gate only matters for
   third-party users you'd want to share the app with. Read this
   carefully: https://support.google.com/cloud/answer/13463073
2. **If A is rejected**, fall back to Option B for GA4 (easy) and
   add the SA as a GSC user manually (one-time, ~2 min).
3. **Option C** is only justified if A is rejected AND B's GSC step
   somehow blocks. Won't happen in practice.

After Option A or B lands, this entire document collapses to:
"the cron runs by itself; check the email if the weekly summary
stops arriving."

---

## Verifying the loop end-to-end

Test the full automation chain once with a `workflow_dispatch` run of
`content_daily.yml` (no schedule wait). Expected log markers:

```
=== Budget guard ===
  spent: $X.XX / $30.00 (Y%)
  action: normal
=== Garden the keyword pool ===
  planned now: N
  ...
  ⚖️  Auto-balance starved article_types (budget cap $0.50)
=== Generate one article (orchestrator) ===
  Selected: '<keyword>' → <article_type>
  ...
=== Backfill 1 under-imaged article ===
  🩹 Backfill candidate: <slug>
  ...
=== Commit & push ===
  [auto] daily pipeline: 2026-MM-DD
```

If any of those markers are missing, the chain has a gap — read the
GitHub Actions log to find which `if:` skipped the step.
