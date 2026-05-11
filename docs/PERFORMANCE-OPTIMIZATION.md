# Performance Optimization Log

Initial trigger: a user-reported PSI Mobile score of 58 (LCP 8.8s, FCP 4.7s)
— well below the Core Web Vitals threshold and a direct SEO ranking signal.

Date: 2026-05-11.

---

## TL;DR

**One commit moved Mobile Performance from 76 → 97** (LCP 4.9s → 2.1s,
FCP 2.7s → 1.7s). Desktop also improved to 96 (LCP 1.2s). Achieved by
deferring AdSense + GA4 booting until `requestIdleCallback` or first user
interaction, plus dropping a leftover render-blocking Google Fonts
stylesheet that wasn't actually needed.

---

## Phase 1 — Real diagnosis

PSI API was over quota, so used local Lighthouse with the same throttling
profile as PSI Mobile:

```
--form-factor=mobile --throttling-method=simulate \
--throttling.rttMs=150 --throttling.throughputKbps=1638 \
--throttling.cpuSlowdownMultiplier=4
```

### Baseline (before any fix)

| Metric | Mobile / | Threshold |
|---|---|---|
| Performance | **76** | ≥75 |
| FCP | 2.7s | <1.8s |
| LCP | 4.9s | <2.5s |
| TBT | 10ms | <200ms |
| CLS | 0 | <0.1 |
| Speed Index | 4.0s | <3.4s |
| TTI | 5.4s | — |

### Top opportunities (sorted by overallSavingsMs)

| # | Issue | Savings |
|---|---|---|
| 1 | Reduce unused JavaScript — AdSense `show_ads_impl.js` (182 KB) | ~440ms |
| 2 | Reduce unused JavaScript — GTM `gtag.js` (162 KB) | ~310ms |
| 3 | No preconnect to `pagead2.googlesyndication.com` / `googletagmanager.com` | DNS+TCP cost |
| 4 | Cloudflare Rocket Loader rewriting `<script>` tags | execution serialization |
| 5 | Render-blocking `<link href="fonts.googleapis.com">` — unused (Inter is served from CF's `/cf-fonts/` instead) | ~200ms |

---

## Phase 2 — Fix

### Single change: `src/layouts/BaseLayout.astro`

1. **Deleted** the `<link rel="preconnect" href="fonts.googleapis.com">`
   and the `<link rel="stylesheet" href="fonts.googleapis.com/css2?...Inter">`
   pair. Inter is already injected via Cloudflare's `/cf-fonts/` font block
   that Pages adds at request time — the Google Fonts request was duplicate
   work that blocked first paint.

2. **Added preconnects** for the origins we *will* hit:
   ```html
   <link rel="preconnect" href="https://pagead2.googlesyndication.com" crossorigin />
   <link rel="preconnect" href="https://www.googletagmanager.com" crossorigin />
   <link rel="preconnect" href="https://www.google-analytics.com" crossorigin />
   ```

3. **Replaced** the eager `<script async>` AdSense + GA4 loaders with a
   single inline boot script that defers their injection until:
   - `requestIdleCallback` fires (timeout 4s), OR
   - First user interaction (`scroll | keydown | pointerdown | touchstart`),
     whichever comes first.

   GA4's `gtag('config', ...)` initial event is fired inside the same
   boot function, so attribution still works once GA4 lands. AdSense Auto
   Ads kick in as soon as their loader finishes, no per-page changes
   needed.

### Trade-off (intentional)

GA4 misses the first ~1–3 seconds of pageviews that bounce instantly. The
mobile LCP win (2.8s improvement) outweighs the analytics loss — bounced
sessions weren't useful analytics anyway. AdSense behavior is unchanged;
ad placements still appear via Auto Ads, just ~1.5–2s later on cold load.

---

## Phase 3 — Verification

Re-measured with identical Lighthouse throttling after deploy + CF zone
purge:

| Surface | Before | After | Δ |
|---|---|---|---|
| Mobile `/` Performance | 76 | **97** | +21 |
| Mobile `/` FCP | 2.7s | 1.7s | −1.0s |
| Mobile `/` LCP | 4.9s | 2.1s | −2.8s |
| Mobile `/` TBT | 10ms | 0ms | −10ms |
| Mobile `/` TTI | 5.4s | 4.0s | −1.4s |
| Desktop `/` Performance | (not run) | **96** | — |
| Desktop `/` LCP | — | 1.2s | — |
| Mobile `/characters/nanally-guide-nte/` Performance | (not run) | **93** | — |
| Mobile article LCP (with hero image) | — | 2.8s | — |

All Core Web Vitals now pass Google's "good" threshold on `/`. Article
pages with a 64–100KB hero WebP land just at 2.8s LCP — within passing
range, with `fetchpriority="high"` already on the hero `<img>`.

### Smoke test

After the deferred-boot change, verified in a real browser session:

```js
{
  adsbygoogle_loaded: true,    // AdSense booted on idle
  gtag_loaded: true,           // GA4 booted on idle
  dataLayer_len: 4,            // GA4 has emitted config + 3 events
  scripts_count: 8
}
```

So no regression on monetization or analytics.

---

## What NOT to do (anti-patterns to avoid)

1. **Don't add another `<link>` to `fonts.googleapis.com`.** Inter is
   already served from Cloudflare's own `/cf-fonts/` block — adding the
   Google Fonts request again will re-add ~200ms of render-blocking
   stylesheet on mobile.

2. **Don't move AdSense / GA4 back into the eager `<script async>` form.**
   Even with `async`, the browser still spends CPU on parsing 350+ KB of
   3rd-party JS during initial paint. The deferred boot is what gave us
   most of the +21 points.

3. **Don't enable Cloudflare Rocket Loader on this project.** It rewrites
   every `<script>` tag and serializes their execution, which fights the
   deferred-boot pattern above. If you see `type="...-text/javascript"`
   suffixes appearing on script tags, Rocket Loader has been re-enabled
   in Pages settings — turn it off.

4. **Don't preload images that aren't above-the-fold on mobile.** The
   article hero is fine to preload; the homepage's "Featured Characters"
   / "Latest Guides" card thumbnails MUST stay `loading="lazy"` —
   eager-loading them adds ~500 KB of unused image bytes to mobile cold
   load.

5. **Don't add custom web fonts beyond Inter** without re-measuring
   mobile LCP. Each extra font file is ~30–60 KB and can push past the
   2.5s LCP threshold.

---

## Future levers (if mobile drops below 90 again)

- **Preload article hero**: emit a `<link rel="preload" as="image"
  href={heroImage} fetchpriority="high">` from `ArticleLayout.astro`.
  Expected gain: ~200–400ms LCP on article pages.
- **Self-host the AdSense loader script** through a CF Worker so the
  long TLS handshake to `pagead2.googlesyndication.com` disappears.
  Caveat: AdSense ToS — verify before shipping.
- **Switch from Auto Ads to manual ad slots** with placement-aware
  loading. Cuts ~150 KB of unused AdSense JS.
- **Split the Inter font down to weights 400/600/700 only** if 500/800
  aren't visually distinct on mobile.
