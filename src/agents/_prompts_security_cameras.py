"""Outline prompts for the security_cameras niche (quvii.com).

Mirrors the shape of _prompts_ecommerce.py but with:
- Camera-shopper factual rules (vs game-fact rules)
- Section templates that emphasize specs / install / troubleshooting
- Audience tags from the security-camera buyer market (renters, homeowners,
  business installs, etc.) — not gaming or seller demographics

The 8 article_types route to the 4 collections per PATH_BY_TYPE:
  blog/    ← camera_buying_guide / camera_comparison / camera_install /
            camera_troubleshoot / camera_news
  learn/   ← camera_learn (pillar-level deep dives)
  reviews/ ← camera_review (Quvii own + competitor reviews)
  support/ ← camera_support (post-purchase setup docs)
"""

from __future__ import annotations


# Section templates per article_type — feeds the OutlineAgent's "required
# H2 sections" list. Keep ordered: typical reader scan path.
SECURITY_CAMERAS_TYPE_SECTIONS: dict[str, list[str]] = {
    "camera_buying_guide": [
        "Who this guide is for",
        "Top picks at a glance",
        "Detailed product breakdown",
        "What to actually look for",
        "When to skip the upgrade",
        "What we didn't include and why",
    ],
    "camera_comparison": [
        "TL;DR verdict",
        "Side-by-side specs table",
        "How they differ in real use",
        "Privacy + data policy comparison",
        "Subscription + total-cost-of-ownership",
        "Bottom line — which to buy",
    ],
    "camera_install": [
        "What you'll need",
        "Step-by-step installation",
        "Common gotchas",
        "Renter-friendly tips (no drilling)",
        "Connecting to your home network",
        "Verifying it works",
    ],
    "camera_troubleshoot": [
        "The symptom",
        "What's likely happening",
        "Quick fixes (in order of likelihood)",
        "Deeper diagnostics",
        "When to contact support",
        "How to prevent it",
    ],
    "camera_news": [
        "What happened",
        "Why it matters for buyers",
        "Impact on existing owners",
        "What to do now",
    ],
    "camera_learn": [
        "What it means",
        "Why it exists",
        "How it works under the hood",
        "Real-world implications",
        "Common misconceptions",
        "Further reading",
    ],
    "camera_review": [
        "Quick verdict",
        "What we tested",
        "Build + design",
        "Image quality (day + night)",
        "App + features",
        "Privacy + data handling",
        "Value vs alternatives",
    ],
    "camera_support": [
        "Problem statement",
        "Required tools/info",
        "Step-by-step solution",
        "Verification",
        "Related issues",
    ],
}


FACTUAL_RULES = """
CRITICAL FACTUAL ACCURACY RULES — security camera content:
1. This article is about a CONSUMER home/small-business security camera
   purchase decision. The reader is shopping or already owns a camera.
2. You MUST use Google Search to find current 2026 product specs, pricing,
   firmware versions, and policy/privacy changes. Camera-industry product
   lines and subscription tiers move fast — never assume year-old info
   still holds.
3. Prefer these sources, in order:
   - Manufacturer spec sheets (eufy.com, ring.com, reolink.com, etc.)
   - FCC ID filings (when verifying RF/Wi-Fi claims)
   - RTINGS, Wirecutter, Tom's Guide, The Verge for independent test data
   - r/HomeSecurity, r/homedefense wiki + pinned threads for community consensus
   - YouTube hands-on reviews (cite by channel + video title, not random forum posts)
4. NEVER invent product names, model numbers, prices, or spec values. If
   search yields no reliable answer, write "[information unavailable]"
   rather than guessing. A wrong spec is worse than a missing one.
5. PRIVACY claims (cloud storage, encryption, third-party sharing, police
   cooperation) MUST trace to the brand's privacy policy or transparency
   report — these are legally sensitive and operationally important.
6. PRICING band only — never specific prices (they fluctuate daily on Amazon).
   Use "around $50", "under $100", "$150-200 range" instead of exact figures.
"""


GENERIC_PROMPT = """You are an SEO content strategist for {brand_name}, a D2C
security camera brand site (quvii.com) — slogan "See everything. Subscribe
to nothing." Audience: US consumers shopping for home/small-business
security cameras, frustrated with the subscription-heavy incumbents
(Ring/Nest/Arlo).

{factual_rules}

Your task: generate an outline for a single article.

Keyword (target search query): {keyword}
Article type: {article_type}
Required sections (use these as H2 headings, in order): {sections}
Target word count: {target_words}

Reply with a single JSON object (no surrounding prose, no fences). Schema:
{{
  "article_type": "{article_type}",
  "title": "<H1 / SEO title, 50-65 chars — concrete + specific>",
  "slug": "<kebab-case slug, ASCII only, max 60 chars>",
  "meta_description": "<140-160 chars>",
  "h1": "<the article H1>",
  "quick_answer": "<1-2 sentences answering the search query directly. Renders as a callout above the article body so readers get the answer without scrolling. Be specific (name a product or two when relevant); cap at 240 chars.>",
  "camera_category": "<one of: indoor | outdoor | doorbell | floodlight | pet | nvr | wireless | multi>",
  "search_intent": "<informational | commercial | transactional | navigational>",
  "sections": [
    {{
      "h2": "<exact section name from required list>",
      "key_points": ["<bullet 1>", "<bullet 2>", "..."],
      "data_required": ["<spec table | comparison chart | screenshot | benchmark>"],
      "h3_subsections": ["<optional H3>"]
    }}
  ],
  "internal_links": [
    {{"anchor_text": "<text>", "target_keyword": "<related quvii topic>"}}
  ],
  "image_specs": [
    {{"position": "after H2-1", "description": "<hero image desc — should show camera in real install context, NOT stock product shot>", "aspect_ratio": "16:9"}}
  ],
  "estimated_word_count": {target_words}
}}
"""


# Comparison/buying-guide prompt enforces the SAME 4 hard rules as ntecodex's
# affiliate comparison flow (single-audience, weakness paragraph, grounded
# facts, "why we skipped X") — these rules are universal across niches.
COMPARISON_PROMPT = """You are a buying-guide writer for quvii.com, a D2C
security camera brand. We compare cameras honestly — even when our own
isn't the winner for a specific use case.

This article will publish with Amazon Associates affiliate links to any
competitor cameras mentioned. The commission rate is the SAME across all
products in the category (typically 2-4%), so we have zero financial
incentive to favor one over another. Editorial independence is the only
reason readers come back.

CRITICAL — factual accuracy:
- Use Google Search for current 2026 specs, prices (price bands only —
  never exact figures), subscription tiers, and recent firmware/policy
  changes (especially Ring 2024 police-data policy, Eufy 2023 cloud
  uploads scandal, etc. — known historic issues should be cited fairly).
- Every claim about a competitor's "drawback" must trace to a verifiable
  source. We don't bash; we cite.
- ASINs: if uncertain, set null. NEVER guess (broken affiliate links are
  worse than missing ones).

FOUR HARD RULES (outputs failing any rule are rejected):

R1 SINGLE-AUDIENCE TITLE. Title MUST contain "for [demo or scenario]" or
   "under $N". NEVER "best security camera 2026".
   - Bad: "Best wireless security cameras 2026"
   - Good: "Best wireless security camera for renters under $150"

R2 WEAKNESS PARAGRAPH. Every product gets ≥2 specific cons. No "could be
   cheaper" hand-waves. Point at features/spec gaps/use-case mismatches.

R3 GROUNDED FACTS. `verdict` references concrete specs (sensor size,
   resolution, IR distance, subscription tier, app rating). No "great
   camera" filler.

R4 "WHY WE SKIPPED" h2. Name 2-3 popular cameras you DELIBERATELY left out,
   with specific reasons. Trust signal vs scraped roundups.

Reply with a single JSON object (no fences). Schema:
{{
  "article_type": "{article_type}",
  "title": "<must contain 'for [audience]' or 'under $N'>",
  "slug": "<kebab-case>",
  "meta_description": "<140-160 chars>",
  "h1": "<same as title>",
  "quick_answer": "<1-2 sentences: name the top pick + 1 runner-up for a different sub-segment. Max 240 chars.>",
  "camera_category": "<indoor | outdoor | doorbell | floodlight | pet | nvr | wireless | multi>",
  "target_audience": "<one sentence: WHO this article is for. e.g. 'Renters who want outdoor monitoring without drilling holes and without a $5/month subscription.'>",
  "products": [
    {{
      "name": "<exact product name from manufacturer>",
      "asin": "<Amazon ASIN or null if uncertain>",
      "image_url": "<m.media-amazon.com URL or null>",
      "price_band": "<e.g. '$80-100' or 'under $50'>",
      "rating": <0-5 approximate>,
      "review_count": <approximate integer>,
      "best_for": "<one short tag — what this is the top pick for>",
      "pros": ["<concrete pro 1>", "<concrete pro 2>", "<concrete pro 3>"],
      "cons": ["<specific weakness>", "<specific weakness>"],
      "verdict": "<2-3 sentences with concrete spec references>"
    }}
  ],
  "sections": [
    {{"h2": "Who this guide is for",        "key_points": ["..."], "data_required": []}},
    {{"h2": "Top picks at a glance",         "key_points": ["..."], "data_required": ["comparison table"]}},
    {{"h2": "Detailed product breakdown",    "key_points": ["..."], "data_required": []}},
    {{"h2": "What to actually look for",     "key_points": ["sensor/resolution","IR range/quality","local-vs-cloud storage","app + privacy policy"], "data_required": []}},
    {{"h2": "When to skip the upgrade",      "key_points": ["honest 'don't buy' guidance"], "data_required": []}},
    {{"h2": "What we didn't include and why","key_points": ["<brand A>","<brand B>"], "data_required": []}}
  ],
  "internal_links": [
    {{"anchor_text": "<text>", "target_keyword": "<adjacent quvii topic>"}}
  ],
  "image_specs": [
    {{"position": "hero", "description": "<3-4 cameras lined up on installer's workbench, real install context, no product-pack shots>", "aspect_ratio": "16:9"}}
  ],
  "estimated_word_count": {target_words}
}}

Aim for 5 products (3-7 acceptable). Quvii's own products should appear when
they genuinely fit the audience — NEVER as auto-promotion. If Quvii doesn't
fit, omit it and recommend competitors honestly.
"""
