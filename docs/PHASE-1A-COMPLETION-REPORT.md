# Phase 1.A Completion Report

NTE Codex / traffic-ops — first vertical slice of the AI-driven gacha guide
site, end-to-end from keyword pool to live revenue surface.

Date: 2026-05-11.

---

## 1. What shipped

### 1.1 Content pipeline (traffic-ops-core)
- ✅ Six-agent pipeline: `KeywordSelector → OutlineAgent → WritingAgent → QAAgent → PublishAgent` + `ImageAgent` for hero/inline images
- ✅ Google Search grounding wired into Outline + Writing
- ✅ QA score on six dimensions; articles with `<6.0` factual_accuracy are rewritten
- ✅ Banned-terms feedback loop (if a proper noun fails to verify, it's banned from the rewrite)
- ✅ Daily cron triggers (`[auto] daily pipeline` commits `fb4cb71`, `b47ca93`)
- ✅ Cost ceiling enforced per-run via shared AI provider

### 1.2 Public site (ntecodex-site, Astro 4)
- ✅ Six content collections: `guides`, `guides/reroll`, `characters`, `boss`, `faq-source`, `tier-list-source`
- ✅ 6 published articles live: 2 character guides, 2 build/comparison guides, 1 boss guide, 1 reroll guide (+ 1 FAQ entry, 1 tier list)
- ✅ Static SSG build, hosted on Cloudflare Pages (project `ntecodex`)
- ✅ Custom domains: `ntecodex.com`, `www.ntecodex.com`
- ✅ Hero + inline images (WebP, fetchpriority hints) — Lighthouse desktop perf 61 → 94
- ✅ Full SEO surface: per-page `<title>` (≤64 chars), meta description, OG tags, canonical, JSON-LD Article/WebSite/ItemList
- ✅ Sitemap (`/sitemap.xml`, 16 URLs) + robots.txt
- ✅ All public copy stripped of "AI" / "peer-reviewed by AI" / "Google Search grounding" language (commit `cbc6b83`)

### 1.3 User-facing interactivity
- ✅ Comment system: per-article `<CommentSection>` with Turnstile-protected POST → Supabase `user_messages` (status='pending')
- ✅ Contact form at `/contact` (same backend, different `message_type`)
- ✅ `/api/comments` Cloudflare Pages Function: GET (list approved) + POST (insert pending, validate Turnstile)
- ✅ E2E verified live: Chrome → form → Turnstile auto-solve → POST → DB row → dashboard moderation → cleanup

### 1.4 Monetization + analytics
- ✅ Google AdSense Auto Ads (publisher `ca-pub-5523706123080113`) — no manual `<ins>` blocks, lets Google place
- ✅ GA4 (`G-FBQDFV7CXF`) with `anonymize_ip: true`
- ✅ Google CMP loaded for GDPR consent (replaced hand-rolled cookie banner)
- ✅ Privacy policy + Terms + About pages

### 1.5 Admin dashboard (traffic-ops-dashboard)
- ✅ Next.js 15 + React 19 + Tailwind + shadcn/ui
- ✅ Comment moderation queue at `/comments` (Approve / Reject / Spam / Delete + filters)
- ✅ GA4 + GSC stats panels via OAuth user credentials
- ✅ Hosted on Cloudflare Pages at `admin.ntecodex.com`

### 1.6 Audit & polish (this sprint)
- ✅ 8-page visual/UX audit (homepage, tier-list, 2× characters, build guide, boss guide, FAQ, contact)
- ✅ P0 fix: broken Reroll Guide CTA (404 → working URL, `664056c`)
- ✅ P1 fix: hero image cap (`max-h-[400px]` so above-fold content isn't pushed below, `a990218`)
- ✅ P1 fix: hero section padding trimmed for desktop above-fold visibility (`664056c`)
- ✅ E2E unblocker: hardcoded Turnstile site_key default so wrangler builds work without Astro env injection (`0aeb46a`)
- ✅ Cross-page consistency: PASS (header/footer/typography/SEO meta all consistent across 8 pages)
- ✅ Infrastructure cleanup: orphan `ntecodex-site` CF Pages project deleted (was burning build minutes on every GitHub push without reaching prod)

---

## 2. Cost ledger (rough)

These are order-of-magnitude figures for Phase 1.A burn. Refine when the
dashboard adds a real cost panel.

| Bucket | Spend so far | Notes |
|---|---|---|
| Anthropic API (Claude) | ~$8 | Outline + QA + reasoning calls × ~30 article attempts (including QA rewrites) |
| Google Vertex AI (Gemini) | ~$3 | Writing + image classification |
| Image generation | ~$2 | Hero + inline images for 6 articles |
| Supabase | $0 | Free tier (well under limits) |
| Cloudflare Pages | $0 | Free tier (3 projects, now 2 after cleanup) |
| Cloudflare Turnstile | $0 | Free up to 1M challenges/month |
| Domain `ntecodex.com` | ~$10/yr | One-time annual |
| **Total run-rate so far** | **~$13** | Plus $10/yr domain |

Forecast at 1 article/day: ~$0.40/day × 30 = ~$12/month AI cost.

---

## 3. Known P2 backlog (not blocking ship)

| Area | Item |
|---|---|
| `/faq` | Sparse single-entry view — generate 4-6 more FAQ entries to fill the page |
| `/` hero | Could use a brighter accent / hero illustration for more above-fold WOW factor |
| Article hero images | Some look awkwardly cropped at `object-cover`; consider focal-point cropping or art direction per image |
| `/sitemap-index.xml` | Astro's default sitemap path returns SPA-fallback HTML. Harmless (robots.txt points to `/sitemap.xml`) but messy — either implement it or 410-gone it |
| Comment loading state | "Loading comments…" stub flashes briefly — could be SSR'd from Supabase at build time |
| Search | Header has a `<button aria-label="Search">` but no search backend wired up yet |
| Weapons collection | `src/content/weapons/` defined in config but empty — `/weapons` index is a 404-shaped placeholder |
| Lighthouse mobile | Desktop 94, mobile not re-measured since v1.0 — likely 70-80 range |
| Rich Results | Not yet validated against Google's Rich Results Test API |
| Cost dashboard | No live AI-spend panel in admin yet; report above is hand-tabulated |

---

## 4. Phase 1.B / Phase 2 route options

### Option A — Content depth (recommended first)
Push article count from 6 → 30 over the next 30 days at 1/day. This:
- Tests pipeline durability at sustained cadence
- Builds the long-tail SEO surface Google needs to index
- Surfaces real QA failure modes on rarer keywords

Cost: ~$12/month AI + your review time.
Risk: low — pipeline is already running daily.

### Option B — Multi-brand SaaS lift
Generalize the pipeline so a second brand (different game, different
keyword pool, different prompts) can run on the same code. This requires:
- Brand-config separation (keywords, prompts, JSON-LD publisher, AdSense pub-id)
- Per-brand Pages project + domain provisioning script
- Per-brand DB row scoping in `user_messages`

Cost: ~2 weeks of work, then near-zero marginal cost per brand.
Risk: medium — prompt portability across games is unproven.

### Option C — Comment & community surface
Real users are now able to comment — but discovery is zero. Add:
- Comment count badges on article cards
- "Latest comments" widget on homepage
- Email digest of pending comments for the moderator
- Maybe a Discord webhook for new submissions

Cost: ~1 week.
Risk: low. Locks in retention if comments ever pick up.

### Option D — Search + internal linking
Plug a search backend (Pagefind static index ships well with Astro) and
wire the CMS to auto-insert related-article links at publish time.
Improves dwell time + RPM, both AdSense and human-visit signals.

Cost: ~3-5 days.
Risk: low.

### Recommended sequence
1. **A (content depth)** — runs in the background, no extra work day-to-day
2. **D (search + linking)** — biggest UX/SEO lift for least effort
3. **C (comment retention)** — if comment traffic actually materializes
4. **B (multi-brand)** — only after Brand 1 proves it can pay its own rent

---

## 5. Sign-off checklist

- [x] Production site reachable at `ntecodex.com`
- [x] Comments E2E working (form → DB → moderation → display)
- [x] All AI-related copy removed from public surface
- [x] CF Pages projects deduplicated (orphan deleted)
- [x] Infrastructure documented (`docs/INFRASTRUCTURE.md`)
- [x] Completion report committed (this file)
- [ ] Phase 2 direction approved by Jeff
