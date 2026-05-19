# New-Site Bootstrap Playbook

**Standard template**: `.github/workflows/content_daily.yml` (ntecodex)
is the canonical workflow shape. Any new site copies this file, swaps
~5 things, never re-authors the body.

**Goal**: compress the "add a second site to the AI Site Operator
pipeline" workflow from ~2 days of debugging into a 30-minute checklist.

This document captures every landmine hit while adding `pixelmatch.art`
as the second tenant alongside `ntecodex.com`, plus a step-by-step
runbook + a bootstrap script that automates the deterministic parts.

---

## Phase 1B retrospective — what bit us

| # | Surface | Symptom | Root cause | Time lost |
|---|---|---|---|---|
| 1 | DB schema | `sites.site_name` NOT NULL | The original migration template inferred from the live `ntecodex` row missed the column | 5 min |
| 2 | DB constraint | `articles_article_type_check` rejects `tool_guide` etc. | CHECK constraint hardcodes gaming-only types; ALTER required | 30 min (incl. classifier block + manual run) |
| 3 | KeywordSelector | Picked `tier_list` for ecommerce keywords | Used hardcoded `ALL_TYPES` instead of reading `sites.config.allowed_article_types` | 20 min |
| 4 | Orchestrator | `KeyError: 'content_plan'` mid-pipeline | `site_config["content_plan"]["min_word_count"]` accessed without fallback; new sites didn't have the block | 10 min |
| 5 | publish.py | Frontmatter emitted `game: unknown` (wrong field) | Niche-agnostic frontmatter builder always wrote `game:`; pixelmatch schema declared `platform:` | 10 min |
| 6 | publish.py | `URL_BY_TYPE` missing `/blog` prefix | Wanted canonical URLs `pixelmatch.art/blog/<type>/<slug>` but URL pattern stored `/learn/<slug>` — caused inconsistent internal nav | 15 min (found during Chrome MCP audit) |
| 7 | run_image_for_articles.py | Silently no-op'd on ecommerce types — generated images but never patched MD | Local `PATH_BY_TYPE` dict didn't include the 4 new types (no DRY with publish.py) | 25 min |
| 8 | sites.config | Missing `image_provider` block | ImageAgent assumed it exists; failed after 3 attempts with cryptic AgentFailure | 15 min |
| 9 | DNS | `198.18.0.115` (GoDaddy parking IP) on `blog` subdomain | URL Forwarding rule shadowed the CNAME | 20 min |
| 10 | DNS | Main site briefly down | Apex `A @ 34.111.179.208` got deleted by mistake while cleaning blog records | 5 min |
| 11 | Astro routing | Subdomain serves at root, but Astro `base: '/blog'` emits prefixed URLs | Needed `_redirects` rewrite `/blog/* → /*` so prefixed URLs still resolve on `blog.pixelmatch.art` | 20 min |
| 12 | Astro layout | "Comments could not load" on every article | `<CommentSection>` calls `/api/comments` Pages Function which doesn't exist on pixelmatch deploy | 10 min |
| 13 | Astro layout | Duplicate Sources lists | `<SourceList>` rendered frontmatter sources alongside the writer-emitted `## Sources` body section | 5 min |

**Total time lost on this iteration: ~3 hours** of debugging across ~5 sessions.

---

## Lessons + design rules

### L1 · Niche-aware OR niche-agnostic — never half-and-half
Every agent + helper that touches article shape (outline, writing,
QA, publish, image, run_batch_smoke, run_image_for_articles, sitemap)
needs a single source of truth. We had `PATH_BY_TYPE` duplicated in
two files — the second one silently broke.

**Rule**: import shared constants from `src.agents.publish` — don't
re-declare. The agent prompts can branch on `site_config.niche` but
the **plumbing** must be unified.

### L2 · `sites.config` jsonb needs a schema doc
Every tenant must have these top-level keys:
- `niche` ("gaming" | "ecommerce_tools")
- `brand` {name, tagline, tool_url, signup_url, tone}
- `cta` {primary_url, primary_label}
- `qa_thresholds` {min_quality_score, max_retry_rounds}
- `content_plan` {min_word_count, max_word_count, daily_articles, type_blacklist, diversity}
- `text_provider` {qa_model, outline_model, writing_model, keyword_research_model}
- `image_provider` {provider, model, default_aspect_ratio, fallback_provider, extra_params}
- `monthly_budget_usd`
- `allowed_article_types`
- `ads` {adsense_enabled}

Plus niche-specific:
- gaming: `game_metadata`, `game_priorities`, `type_blacklist_per_game`
- ecommerce: `platform_metadata`

### L3 · The DB constraint trap
`articles.article_type` has a CHECK constraint with hardcoded values.
Adding new types is a schema change — bake the widening into the
migration template, don't discover it at runtime.

### L4 · Domain/DNS hygiene
- **Never** touch the apex (`@`) A record unless you're moving the
  main site. Always operate on subdomain rows.
- GoDaddy URL Forwarding is the culprit when `198.18.0.115` shows up
  — check **Forwarding** tab separately from DNS Records.
- Run `dig <subdomain> +short` immediately after every DNS change.

### L5 · Astro `base: '/blog'` is a foot-gun
When `base` is set, internal URLs get prefixed but dist structure
stays flat. On a bare subdomain deploy you need `_redirects` to map
`/blog/* → /*`. Stored URLs (in DB, frontmatter, sitemap) should
include the prefix so internal navigation matches canonical.

### L6 · Chrome MCP audit before declaring done
The 4 P0 fixes from the MCP audit (CTA URLs, comments widget, footer
links, duplicate sources) would have shipped to production unnoticed
otherwise. **Required step** before flipping the cron switch.

---

## New-site checklist (use this next time)

### Phase 0 · Decisions (30 min)
- [ ] Choose `niche`: existing (`gaming`, `ecommerce_tools`) or new
- [ ] Map content `article_types` (4-9 types per niche)
- [ ] Define target audiences (analogous to `game` or `platform`)
- [ ] Pick subdomain vs path: `blog.X.com` (fast) vs `X.com/blog` (CF Worker, +1 week)
- [ ] Identify DNS provider (CF / GoDaddy / Namecheap / Replit-owned)
- [ ] Decide cron cadence: slow start (2/day) → ramp to 24/day

### Phase 1 · Code (60 min, mostly automated)
- [ ] Run `scripts/bootstrap_new_site.py --domain X.com --niche Y` (see below)
- [ ] If new niche: add prompts to `src/agents/_prompts_<niche>.py`
- [ ] Add niche branch in `outline.py` / `writing.py` / `qa.py` (4-line `if`)
- [ ] Run `pytest tests/ -q` — must stay green

### Phase 2 · DB (5 min)
- [ ] Run generated SQL migration in Supabase SQL Editor
- [ ] Run `ALTER TABLE articles add article_type CHECK …` (if new types)
- [ ] Verify with `select config->>'niche' from sites where domain=…`

### Phase 3 · Astro site (90 min)
- [ ] `cp -R ntecodex-site <new>-site && rm -rf .git dist node_modules`
- [ ] Rebrand `tailwind.config.mjs` palette
- [ ] Update `astro.config.mjs` site + base
- [ ] Update `src/content/config.ts` collection schemas
- [ ] Rewrite `src/pages/{index,404}.astro` + add `[...slug].astro` per collection
- [ ] Rebrand Header/Footer
- [ ] Add `public/_redirects` if using `base: '/<prefix>'`
- [ ] `npm install && npm run build` — must succeed
- [ ] `git init && git push` to new GitHub repo

### Phase 4 · Cloudflare (15 min)
- [ ] Create Pages project via **CLI** (avoid the Workers-style UI flow):
  ```bash
  cd <new>-site && npm run build && \
  npx wrangler pages project create <new>-blog --production-branch main && \
  npx wrangler pages deploy dist --project-name <new>-blog --branch main
  ```
- [ ] Add Custom Domain `blog.<new>.com` → click **"My DNS provider"** → **"Begin CNAME setup"** (NOT "DNS transfer")
- [ ] At DNS provider: add CNAME `blog → <project>-blog.pages.dev`
- [ ] Delete any existing A record OR URL Forwarding for the same subdomain
- [ ] Wait 5-15min then `dig blog.<new>.com +short` → should return CF IP

### Phase 5 · Smoke (20 min)
- [ ] `python -m scripts.seed_keywords_for_<niche> --count 60 --dry-run` → check distribution
- [ ] Real seed → 60 keywords inserted
- [ ] `SITE_DOMAIN=X.com python -m scripts.run_batch_smoke --count 1`
- [ ] `SITE_DOMAIN=X.com python -m scripts.publish_articles`
- [ ] `SITE_DOMAIN=X.com python -m scripts.run_image_for_articles --new-only --inline 6`
- [ ] `cd <new>-site && git add . && git commit && git push`
- [ ] Wait 2min, hit `https://blog.<new>.com/blog/`

### Phase 6 · Chrome MCP UX audit (10 min)
- [ ] Logo + CTAs go to right URLs
- [ ] No JS console errors / failed fetches
- [ ] Article page: badge, banner, body, mid CTA, footer CTA, sources, **no double-source widget**, **no failed comments widget**
- [ ] Footer links all 200 on target SaaS
- [ ] AdSense `<Advertisement>` slot detected in DOM

### Phase 7 · GitHub Actions (5 min)

**Use `content_daily.yml` (ntecodex) as the canonical template,** not
`content_pixelmatch.yml` — both have identical step counts (25), but
ntecodex is the proven-in-prod template with 60+ successful runs.

- [ ] Add secrets:
  - `<NEW>_SITE_REPO_PAT` — fine-grained PAT, contents:write on the new site repo
  - `<NEW>_GA4_PROPERTY_ID` — for the GA4 collector (read via `site_env_prefix()`)
  - `<NEW>_GA4_MEASUREMENT_ID` — for GA4 client-side tracking on the site
- [ ] Copy `.github/workflows/content_daily.yml` → `content_<new>.yml`
- [ ] In the new file, change ONLY these markers:
  1. `env.SITE_DOMAIN: <new>.com`
  2. All occurrences of `ntecodex-site` → `<new>-site`
  3. `--project-name ntecodex` → `--project-name <new>-blog` (CF Pages)
  4. `NTECODEX_*` secret refs → also include `<NEW>_*` (don't remove ntecodex's — `run_collectors` iterates every active site and needs every site's env)
  5. Schedule block — start with 2 crons/day (`0 3 * * *` + `0 15 * * *`), expand later
- [ ] Test: `gh workflow run content_<new>.yml`

### Phase 8 · AdSense (variable, 1-14 days for review)
- [ ] After ~10-20 articles, log into AdSense → Sites → Add `blog.<new>.com`
- [ ] Verify `ads.txt` returns expected line
- [ ] Request review

---

## Automated bootstrap script (Phase 1+2 in one command)

See `scripts/bootstrap_new_site.py` for the implementation. It:

1. Asks for: `--domain`, `--niche`, `--brand-name`, `--cta-url`
2. Generates a `migrations/00X_<domain>_site.sql` with:
   - `INSERT INTO sites` with the full required jsonb shape
   - `ALTER TABLE articles add CHECK constraint` (additive widen)
3. Prints SQL to stdout for operator to run
4. Optionally creates an Astro skeleton via `cp -R` + sed of brand strings
5. Prints a commit-ready checklist

This replaces ~60% of manual work with a deterministic generator,
leaving only the prompt-engineering + UX-audit work as human.
