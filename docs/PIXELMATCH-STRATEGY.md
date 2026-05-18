# PixelMatch Content Site — Strategy & Architecture

**Status**: Phase 1A landed 2026-05-14 — niche-aware agents.
**Target**: `pixelmatch.art/blog/<slug>` driving SEO + AdSense + tool signups.
**Hosting**: `blog.pixelmatch.art` (Cloudflare Pages) initially → path-rewrite to `pixelmatch.art/blog/*` (Cloudflare Worker) in Phase 3.

## 1. Why a second site

`traffic-ops-core` already proved an autonomous content engine works at
$0.15-0.30 / article with 60-95% pass rate. PixelMatch (`pixelmatch.art`,
Replit Autoscale, Stripe billing, multi-model AI image batch tool for
ecommerce sellers) is a natural second tenant: high-CPC niche
(ecommerce-tools), B2B audience that doesn't bounce, content topics
that map directly to tool features → measurable conversion funnel.

Same pipeline. Different prompts, keywords, voice. **Zero schema
change** — `sites.config` jsonb absorbs everything.

## 2. Routing — subdomain → path rewrite

| Phase | URL | Why |
|---|---|---|
| **1-2 (now)** | `blog.pixelmatch.art/<slug>` | Pages deploy, zero risk to live SaaS on Replit |
| **3 (~2 weeks)** | `pixelmatch.art/blog/<slug>` | CF Worker rewrites `/blog/*` from Replit-fronting CF zone → Pages. Astro emits `pixelmatch.art/blog/...` as canonical from day 1 so the cutover is invisible to Google. |

DNS: `pixelmatch.art` already points at Replit. Move it through
Cloudflare proxy (orange cloud) when we're ready for Phase 3 — Replit
keeps serving the app, Worker peels off the `/blog/*` prefix.

## 3. The four new article types

| `article_type` | Path | Word band | Primary intent | Why this type |
|---|---|---|---|---|
| `tool_guide` | `/blog/learn/<slug>` | 1400-2200 | how-to | Top-of-funnel: "how to X for Amazon listings" |
| `vs_comparison` | `/blog/compare/<slug>` | 1600-2400 | commercial-intent | Mid-funnel: "PixelMatch vs Photoroom for FBA" — captures buy-stage searches |
| `use_case` | `/blog/stories/<slug>` | 1800-2600 | E-E-A-T | "How [seller] doubled CTR with AI lifestyle shots" — real numbers, real workflow, hardest to fake → strongest organic moat |
| `policy_guide` | `/blog/policy/<slug>` | 1200-1800 | reference | "Amazon main image requirements 2026" — high return-visit, low produce-cost |

Distribution target: 35 / 25 / 25 / 15 — favor commercial-intent over
volume.

## 4. Audience × Platform matrix

4 platforms × 4 types = **16 content slots**. Seed keywords distributed
across the matrix; KeywordSelector's per-platform pass-rate weighting
emerges organically over the first 200 articles.

| | `tool_guide` | `vs_comparison` | `use_case` | `policy_guide` |
|---|---|---|---|---|
| **amazon_fba** | "white bg for amazon listing" | "photoroom vs canva amazon" | "FBA seller doubled CTR with AI" | "amazon main image requirements 2026" |
| **shopify** | "shopify product photo editor" | "best AI photo tool shopify" | "shopify dropshipper image workflow" | "shopify image SEO best practices" |
| **etsy** | "etsy listing photos that sell" | "remove bg etsy alternatives" | "POD seller mockup workflow" | "etsy photo guidelines 2026" |
| **tiktok_shop** | "tiktok shop thumbnail size" | "tiktok image tools 2026" | "tiktok viral product photos" | "tiktok shop image policy" |

## 5. Niche-aware agents

`sites.config.niche` toggles prompt family:
- `"gaming"` (default) → existing fandom-grounded prompts (ntecodex)
- `"ecommerce_tools"` → new B2B SaaS prompts (pixelmatch)

Each agent (`outline`, `writing`, `qa`) picks template based on niche.
No code duplication — `_prompts_ecommerce.py` is the alt registry,
agents do `if niche == "ecommerce_tools": <alt> else: <current>`.

## 6. CTA + UTM funnel

PublishAgent auto-injects 2 CTAs per ecommerce article:

1. **Mid-article soft sell** after 3rd H2:
   ```
   > 💡 Don't want to edit 50 product photos by hand? PixelMatch
   > batch-generates Amazon-ready images in 60 seconds.
   > [Try free →](https://pixelmatch.art/signup?utm_source=blog&utm_medium=mid&utm_campaign=<slug>)
   ```

2. **Footer hard CTA** before Sources:
   ```
   ### Ready to scale your listings?
   PixelMatch generates white-background + lifestyle + variant mockups
   from a single source photo. 50 free images on signup.
   **[Start free →](https://pixelmatch.art/signup?utm_source=blog&utm_medium=footer&utm_campaign=<slug>)**
   ```

Tool-specific deep-link when `outline.featured_tool` is set:
`https://pixelmatch.art/tools/<tool_slug>?utm_source=blog&utm_campaign=<slug>`.

UTM campaign = article slug → pixelmatch backend joins
`articles.slug` ↔ `signup.utm_campaign` for per-article conversion.

## 7. QA calibration

`niche="ecommerce_tools"` changes 2 of 6 dimensions:

| Dim | Gaming check | Ecommerce check |
|---|---|---|
| `factual_accuracy` | Are character / weapon / mechanic names real? | Are platform policies / specs / dollar figures verifiable via search? |
| `actionable` (new for ecommerce) | — | Does each H2 contain at least one concrete step (command, exact spec, or measurable threshold)? |

Honesty placeholder rule still applies, just rephrased. Tier
thresholds unchanged (≥7.5 clean, ≥6.0 note, ≥4.5 strong, <4.5 reject).

## 8. Cadence

Same as ntecodex:
- 24 cron / day × 3 articles
- $500 / month budget guard
- Velocity check band 50-75%
- All existing maintenance steps reused (mobile, finalize stuck,
  GSC signals, commit/push, image agent DESC)

Two sites share GitHub Actions matrix — independent budget guards,
independent commit/push to their respective Pages repos.

## 9. Phase delivery

- **Phase 1A** (this commit): niche-aware agents, 4 new article_types,
  CTA injector, seed script for pixelmatch. **No content yet — engine ready.**
- **Phase 1B**: `pixelmatch-site` Astro skeleton, workflow matrix.
- **Phase 2**: Seed 50 keywords, smoke 10 articles, human review, ramp.
- **Phase 3**: CF Worker path rewrite, dashboard 2-site selector.
- **Phase 4** (8 weeks out, conditional on data): UGC syndication
  (pixelmatch user images back into blog galleries).
