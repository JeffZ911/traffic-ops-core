# New Site Setup SOP

How to add site N+1 to the autonomous content platform. ntecodex.com
(gaming) and pixelmatch.art (ecommerce) are the two reference tenants;
everything below is proven on both.

**TL;DR effort to add a 3rd site: ~45–60 min of human work**, almost
all of it in third-party consoles (GA4 / GSC / Cloudflare / GoDaddy /
GitHub). The code + DB side is ~10 min.

---

## The 3 reuse tiers

| Tier | Meaning | What it covers |
|---|---|---|
| **A. Config-level** (0 code) | Just fill `sites.config` jsonb (via SQL or `/sites/new`) | Niche, article types, brand, CTA, budget, GA4/AdSense IDs, platform/game metadata, QA thresholds |
| **B. Semi-auto** (copy-paste) | `/sites/new` wizard generates artifacts you paste | sites row (auto-written), workflow YAML (copy → repo), SQL preview |
| **C. One-time manual** (external consoles) | Can't be automated — needs your login to 3rd parties | GA4 property, GSC property + verify, Cloudflare Pages project, DNS records, GitHub secrets, OAuth (already done once, covers all sites) |

---

## What's reused automatically (Tier A — 0 code, 0 new files)

Once a site row exists with the right `config`, **every** pipeline
capability works with no code change, because all agents/scripts read
`SITE_DOMAIN` → `sites.config`:

- **Content generation**: KeywordSelector → Outline → Writing → QA →
  Tier classification → Publish → Image. Niche-aware prompts switch on
  `config.niche` (`gaming` | `ecommerce_tools`).
- **SEO discovery**: sitemap auto-resubmit, IndexNow push, Indexing API
  nudge (if SA configured), internal links (RelatedGuides + listing
  pages).
- **GA4 / AdSense injection**: `inject_site_env.py` reads
  `config.ga4_measurement_id` + `config.adsense_publisher_id` at build
  time → baked into HTML.
- **Metrics collection**: `run_collectors.py` iterates every active
  site; resolves per-site GA4 property via `config.ga4_property_id`
  (or `<SLUG>_GA4_PROPERTY_ID` env).
- **Dashboard**: `/sites`, Mission Control, Viability, Articles all
  scope by the site switcher automatically — no per-site code.
- **Maintenance**: budget guard, velocity check, GSC signals, mobile
  check, finalize-stuck — all `SITE_DOMAIN`-aware.

---

## Step-by-step: add site N+1

### Step 0 · Prereqs (already done once, never repeat)
- [x] GSC OAuth token with `webmasters` write scope in GitHub Secret
      `GOOGLE_OAUTH_REFRESH_TOKEN` (covers ALL sites — user-level scope)
- [x] Cloudflare account + `CLOUDFLARE_API_TOKEN` / `_ACCOUNT_ID` secrets
- [x] `GEMINI_API_KEY`, `SUPABASE_*`, `SMTP_*` secrets

### Step 1 · Create the site row — **Tier A/B, 5 min**
Two ways:
- **Dashboard**: open `/sites/new`, fill the 4-step wizard, click
  Create. It writes the `sites` row directly and shows you the workflow
  YAML + manual checklist.
- **SQL**: copy `src/db/migrations/004_pixelmatch_site.sql`, swap
  domain/niche/brand/article_types, run in Supabase.

Required `config` fields (the wizard fills these for you):
```
site_slug, niche, brand{name,tool_url,signup_url}, cta{primary_url},
monthly_budget_usd, qa_thresholds, text_provider, image_provider,
content_plan{min/max_word_count, type_blacklist, diversity},
allowed_article_types, platform_metadata|game_metadata
```

### Step 2 · New Astro site repo — **Tier B, 5 min**
```bash
cd ~/Documents/traffic-ops
cp -R pixelmatch-site <new>-site      # ecommerce template
# OR cp -R ntecodex-site <new>-site   # gaming template
cd <new>-site && rm -rf .git node_modules dist
# Edit: astro.config.mjs (site/base), Header/Footer brand, tailwind
#       colors, content/config.ts collections, public/ads.txt + robots
git init -b main && git add -A && git commit -m "init <new>-site"
gh repo create JeffZ911/<new>-site --public --source=. --push
```

### Step 3 · GitHub workflow — **Tier B, 3 min**
```bash
cp .github/workflows/content_daily.yml \
   .github/workflows/content_<new>.yml
```
Swap exactly 5 markers (see `NEW-SITE-PLAYBOOK.md` §7):
1. `env.SITE_DOMAIN`
2. `ntecodex-site` → `<new>-site` (all occurrences)
3. `--project-name ntecodex` → `--project-name <new>-blog`
4. add `<NEW>_*` secret refs (keep existing ones — collectors iterate
   all sites)
5. schedule block — start with 2 crons/day, expand later

### Step 4 · External services — **Tier C, ~30 min** (the real work)
| # | Console | Action | Time |
|---|---|---|---|
| 4a | analytics.google.com | Create GA4 property + data stream → grab **Property ID** (9-digit) + **Measurement ID** (G-XXX) | 8 min |
| 4b | Dashboard `/sites` | Paste Property ID + Measurement ID into the new site's row (writes `config.ga4_*`) | 1 min |
| 4c | dash.cloudflare.com | Create Pages project `<new>-blog`, connect to `JeffZ911/<new>-site`, production branch `main` | 5 min |
| 4d | Cloudflare Pages | Add custom domain (`blog.<new>.com` for ecommerce, apex for gaming) | 2 min |
| 4e | GoDaddy/registrar | CNAME `blog` → `<new>-blog.pages.dev` (ecommerce) or A record (gaming). **Never delete the apex A record.** | 5 min |
| 4f | search.google.com/search-console | Add domain property `<new>.com`, verify (DNS TXT). OAuth user is auto-authorized for sitemaps. | 5 min |
| 4g | GitHub repo secrets | Add `<NEW>_SITE_REPO_PAT` (contents:write on the site repo) | 3 min |

### Step 5 · Seed + smoke — **Tier A, 10 min**
```bash
# Seed keywords (adapt seed_keywords_for_pixelmatch.py axis if needed)
python -m scripts.seed_keywords_for_<niche> --count 60

# Local smoke before enabling cron
SITE_DOMAIN=<new>.com SITE_REPO_PATH=~/Documents/traffic-ops/<new>-site \
  python -m scripts.run_batch_smoke --count 3 --max-retries 1
SITE_DOMAIN=<new>.com SITE_REPO_PATH=... python -m scripts.publish_articles
SITE_DOMAIN=<new>.com SITE_REPO_PATH=... python -m scripts.run_image_for_articles --new-only --inline 6 --budget-usd 0.5
```

### Step 6 · Generate IndexNow key — **Tier A, 2 min**
```bash
# generates + stores config.indexnow_key, then host the key file:
python -c "import secrets; print(secrets.token_hex(16))"   # or reuse gen script
echo "<key>" > ~/Documents/traffic-ops/<new>-site/public/<key>.txt
# commit + push the key file
```

### Step 7 · Flip cron on
Confirm `/sites` shows the new site at 14/14 (or close), then let the
scheduled cron run. Verify the first CI run is green + content lands.

---

## Reuse verdict by capability

| Capability | Reuse tier | New-site cost |
|---|---|---|
| Content pipeline (all 7 agents) | A | 0 — `SITE_DOMAIN` routes everything |
| Niche prompts (gaming/ecommerce) | A | 0 (or write a 3rd niche prompt set if truly new vertical) |
| SEO: sitemap resubmit | A | 0 (auto in cron) |
| SEO: IndexNow | A | gen 1 key + host file (Step 6) |
| SEO: internal links | A | 0 (RelatedGuides + listing pages inherited) |
| GA4/AdSense HTML injection | A | paste IDs in `/sites` (Step 4b) |
| Metrics collectors | A | 0 (iterates all active sites) |
| Dashboard (all pages) | A | 0 (site switcher) |
| Astro site shell | B | cp template + rebrand (Step 2) |
| GitHub workflow | B | cp + swap 5 markers (Step 3) |
| sites row | B | wizard or SQL (Step 1) |
| GA4 property | C | manual console (Step 4a) |
| GSC property + verify | C | manual console (Step 4f) |
| Cloudflare Pages project | C | manual console (Step 4c–d) |
| DNS records | C | manual registrar (Step 4e) |
| Per-site GitHub secret (PAT) | C | manual (Step 4g) |

**~80% of the platform is Tier A** (pure config reuse). The remaining
work is shell-cloning two repos (B) and clicking through 3rd-party
consoles (C) that genuinely require a human login.

---

## What canNOT be reused / needs thought per site
- **Brand visual identity** — colors, logo, copy tone (manual design)
- **Keyword seed taxonomy** — each niche needs its own keyword axes
  (gaming = per-game; ecommerce = per-platform; a new vertical needs a
  new seed script or `--axis` adaptation)
- **A genuinely new niche** (beyond gaming/ecommerce) needs a 3rd
  prompt family in `src/agents/_prompts_<niche>.py` + niche branch in
  outline/writing/qa (~1–2h one-time, then Tier A for all future sites
  in that niche)
