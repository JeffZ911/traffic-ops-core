# Indexing Worklist — 2026-06-01

Generated from a live GSC `urlInspection` census (40-URL samples) + frontmatter audit.

## The one-line diagnosis
Discovery layer is **fine** (sitemaps correct, qa<6 already noindex'd by design).
The wall is **authority**: of ntecodex's ~95 good (qa≥6) discoverable pages,
**only ~2% are indexed**. Google reads them and declines. The only levers left
are (1) earn authority, (2) seed indexing manually, (3) prune crawl-budget waste.

## Census (real numbers)

| Site | Articles on disk | In sitemap (indiv URLs) | Indexed (sampled) | Status |
|---|---|---|---|---|
| ntecodex.com | 555 (was 697; pruned) | ~95 qa≥6 indiv | **~2%** (1/40) | Authority wall |
| pixelmatch.art/blog | 53 | 63 (sitemap, 72 locs) | **0%** | Authority wall |
| quvii.com | 15 | 15 | 0 (just fixed today) | Brand-new, needs time + seeding |

*ntecodex: 80% of sampled = "Discovered – currently not indexed", 12% unknown, 5% redirect-error, 2% indexed.*
*435 faq-source/tier-list-source entries correctly collapse into 2 hub pages (`/faq`, `/tier-list`) — not individual URLs.*
*pixelmatch CORRECTION: the blog is correctly live at `pixelmatch.art/blog/...` (SPA app at apex root, blog at /blog subpath). Canonical + sitemap consistent, GSC has it (72 URLs, downloaded 2026-05-31). The earlier "blog not deployed" reading was a census bug (fetched apex sitemap, not /blog/sitemap.xml). NOT a deploy problem — same authority wall: 90% "Discovered – not indexed", 0% indexed.*

**All three sites are delivery-healthy. The single universal bottleneck is the authority wall.**

---

## ACTION 1 — Manual Request Indexing (you click, GSC URL Inspection)
Do ~10/day per property. Paste URL → "Request Indexing". Start with highest qa.

### ntecodex.com — top 20 (qa≥6, live individual pages)
- [ ] https://ntecodex.com/guides/how-to-build-silver-wolf-hsr  (10.0)
- [ ] https://ntecodex.com/guides/neuvillette-vs-ayato-dps-comparison  (10.0)
- [ ] https://ntecodex.com/guides/pela-best-build-hsr-guide  (9.8)
- [ ] https://ntecodex.com/guides/neverness-to-everness-healer-build-guide  (9.8)
- [ ] https://ntecodex.com/guides/neverness-to-everness-stamina-guide  (9.6)
- [ ] https://ntecodex.com/guides/arlecchino-vs-hu-tao-dps-comparison  (9.6)
- [ ] https://ntecodex.com/guides/zzz-elemental-damage-explained-guide  (9.6)
- [ ] https://ntecodex.com/guides/ruan-mei-vs-robin-support-comparison-hsr  (9.6)
- [ ] https://ntecodex.com/guides/zzz-stun-vs-anomaly-build-guide  (9.4)
- [ ] https://ntecodex.com/boss/how-to-beat-early-bosses-nte  (9.2)
- [ ] https://ntecodex.com/guides/silver-wolf-debuff-build-hsr-guide  (9.2)
- [ ] https://ntecodex.com/guides/jing-yuan-vs-argenti-hsr-comparison-guide  (9.2)
- [ ] https://ntecodex.com/guides/wuwa-standard-banner-vs-limited  (9.2)
- [ ] https://ntecodex.com/guides/neverness-to-everness-character-level-up-guide  (9.2)
- [ ] https://ntecodex.com/guides/nte-efficient-stamina-usage-guide  (9.2)
- [ ] https://ntecodex.com/guides/best-build-for-mako-nte-20260521  (9.2)
- [ ] https://ntecodex.com/guides/rina-vs-nicole-zzz-support-comparison  (9.2)
- [ ] https://ntecodex.com/weapons/nte-legendary-weapon-stats-effects-guide  (8.8)
- [ ] https://ntecodex.com/guides/neverness-to-everness-character-level-up-guide  (9.2)
- [ ] https://ntecodex.com/boss/how-to-beat-abyss-floor-12-guide

### quvii.com — all 15 (brand new, all qa≥7.5)
- [ ] https://quvii.com/learn/arlo-camera-battery-draining-fast  (10.0)
- [ ] https://quvii.com/learn/ring-camera-keeps-disconnecting-wifi-fix  (10.0)
- [ ] https://quvii.com/blog/install-security-camera-no-wiring  (9.6)
- [ ] https://quvii.com/blog/best-indoor-security-camera-pet-monitoring  (9.6)
- [ ] https://quvii.com/blog/blink-vs-wyze-outdoor-camera-2026-comparison  (9.2)
- [ ] https://quvii.com/blog/how-to-set-up-security-camera-with-alexa  (9.2)
- [ ] https://quvii.com/learn/what-is-poe-camera-how-it-works  (8.8)
- [ ] https://quvii.com/learn/are-wireless-cameras-safe-from-hackers  (8.8)
- [ ] https://quvii.com/learn/are-security-cameras-worth-it-for-apartment-20260530  (8.8)
- [ ] https://quvii.com/blog/eufy-cloud-upload-controversy-update-2026-20260531  (8.8)
- [ ] https://quvii.com/learn/camera-resolution-explained-2k-vs-4k-difference  (8.3)
- [ ] https://quvii.com/blog/best-outdoor-security-camera-without-subscription  (8.3)
- [ ] https://quvii.com/blog/best-wireless-camera-without-wifi  (8.3)
- [ ] https://quvii.com/learn/how-night-vision-security-cameras-work  (7.5)
- [ ] https://quvii.com/learn/blink-camera-not-detecting-motion  (7.5)

### pixelmatch.art/blog — top 15 (note the `/blog/` path)
- [ ] https://pixelmatch.art/blog/learn/create-high-resolution-2048x2048-shopify-images/  (10.0)
- [ ] https://pixelmatch.art/blog/learn/batch-remove-background-amazon-variant-skus/  (10.0)
- [ ] https://pixelmatch.art/blog/stories/amazon-brand-registry-a-plus-content-conversion-lift-case-study/  (10.0)
- [ ] https://pixelmatch.art/blog/stories/shopify-alt-text-and-image-naming-for-seo/  (10.0)
- [ ] https://pixelmatch.art/blog/stories/amazon-prime-day-2026-image-prep-guide/  (10.0)
- [ ] https://pixelmatch.art/blog/stories/optimizing-product-images-for-amazon-rufus-search/  (10.0)
- [ ] https://pixelmatch.art/blog/stories/batch-generate-product-photos-for-etsy-pod/  (10.0)
- [ ] https://pixelmatch.art/blog/compare/ai-vs-traditional-photography-for-shopify-stores/  (10.0)
- [ ] https://pixelmatch.art/blog/policy/amazon-ai-video-generator-product-photos-policy/  (10.0)
- [ ] https://pixelmatch.art/blog/policy/etsy-handmade-policy-ai-assisted-product-photos/  (10.0)
- [ ] https://pixelmatch.art/blog/policy/tiktok-shop-thumbnail-aspect-ratio-requirements-2026/  (10.0)
- [ ] https://pixelmatch.art/blog/policy/amazon-fba-busy-background-policy-compliance-tips/  (10.0)
- [ ] https://pixelmatch.art/blog/compare/photoroom-vs-pixelcut-tiktok-shop/  (9.4)
- [ ] https://pixelmatch.art/blog/stories/shopify-ai-product-backgrounds-case-study/  (10.0)
- [ ] https://pixelmatch.art/blog/compare/ai-product-photo-tools-pod/  (varies)

> ⚠️ Cross-niche contamination spotted: `/blog/learn/nte-car-tuning-tool-guide/` is a
> gaming (Neverness-to-Everness) topic on the ecommerce-tools blog. Don't seed it; flag for removal.

---

## ACTION 2 — Earn authority (the actual gate)
Even 5–10 real links materially change index behavior for a small site.
- [ ] Post 2-3 genuinely helpful answers in niche communities, linking a relevant guide
      (gaming: r/HonkaiStarRail, r/ZZZ_Official, gacha Discords; cameras: r/homedefense, r/homeautomation)
- [ ] Submit each domain to 3-5 quality niche directories
- [ ] 1 guest post / resource-page outreach per site per week
- [ ] (seo_growth_loop.py already drafts a week-stamped version of this into /todos)

## ACTION 3 — Done / resolved
- [x] Hard-deleted 142 noindex'd qa<6 ntecodex pages (697 → 555, committed 621b046)
- [x] pixelmatch "deploy fix" — RETRACTED, nothing broken (blog live at /blog, same authority wall)
- [x] quvii production-branch deploy fixed (routes + sitemap + brand VI live)

## What does NOT move the needle (stop doing)
- Writing more articles on zero-authority domains. Volume on an AI-content domain
  with no authority is the exact pattern the Helpful-Content system suppresses.
- More QA tuning. The qa≥6 pages are already not getting indexed — quality isn't the gate now.
