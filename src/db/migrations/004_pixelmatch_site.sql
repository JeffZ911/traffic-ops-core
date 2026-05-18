-- 004_pixelmatch_site.sql
-- Phase 1A (2026-05-14): register pixelmatch.art as a second tenant.
--
-- ⚠️  REQUIRES the article_type CHECK constraint to also accept the four
-- ecommerce types. Run migration 005 FIRST (or paste the ALTER block from
-- there ahead of this insert) or every pixelmatch article will fail to
-- write to the articles table with `check constraint
-- articles_article_type_check`.
--
-- This migration is INSERT-only — no schema change. The agents picked up
-- niche="ecommerce_tools" in this same commit, so once this row exists
-- the orchestrator can produce ecommerce content for pixelmatch.art with
-- zero further code changes.
--
-- Idempotent: ON CONFLICT DO UPDATE so re-running just refreshes the
-- config blob without duplicating sites.

insert into sites (id, domain, site_name, config, created_at, updated_at)
values (
  gen_random_uuid(),
  'pixelmatch.art',
  'PixelMatch Blog',
  jsonb_build_object(
    'niche', 'ecommerce_tools',
    'brand', jsonb_build_object(
      'name', 'PixelMatch',
      'tagline', 'Batch AI product images for ecommerce sellers',
      'tone', 'B2B SaaS, conversion-focused, ROI-driven',
      'tool_url', 'https://pixelmatch.art',
      'signup_url', 'https://pixelmatch.art/signup'
    ),
    'cta', jsonb_build_object(
      'primary_url', 'https://pixelmatch.art/signup',
      'primary_label', 'Generate 50 product images free'
    ),
    'monthly_budget_usd', 500,
    'qa_thresholds', jsonb_build_object(
      'min_quality_score', 7.0,
      'max_retry_rounds', 1
    ),
    -- content_plan is required by orchestrator.py (min/max word band)
    -- and KeywordSelector (type_blacklist). Mirrors ntecodex's shape.
    'content_plan', jsonb_build_object(
      'min_word_count', 1400,
      'max_word_count', 2400,
      'daily_articles', 6,
      'type_blacklist', jsonb_build_array(),
      'diversity', jsonb_build_object(
        'required_types', jsonb_build_array(
          'tool_guide', 'vs_comparison', 'use_case', 'policy_guide'
        ),
        'min_types_per_week', 3
      )
    ),
    'text_provider', jsonb_build_object(
      'qa_model', 'gemini-3.1-pro-preview',
      'outline_model', 'gemini-3.1-pro-preview',
      'writing_model', 'gemini-3-flash-preview',
      'keyword_research_model', 'gemini-3-flash-preview'
    ),
    'platform_metadata', jsonb_build_object(
      'amazon_fba', jsonb_build_object(
        'display_name',    'Amazon FBA sellers',
        'subreddit',       'FulfillmentByAmazon',
        'official_docs',   jsonb_build_array('sellercentral.amazon.com', 'brandservices.amazon.com'),
        'image_specs',     '1000x1000+, pure white #FFFFFF, product fills 85%+',
        'key_terms',       jsonb_build_array('ASIN', 'main image', 'Brand Registry', 'A+ Content', 'PPC', 'COGS')
      ),
      'shopify', jsonb_build_object(
        'display_name',    'Shopify store owners',
        'subreddit',       'shopify',
        'official_docs',   jsonb_build_array('help.shopify.com', 'shopify.dev'),
        'image_specs',     '2048x2048 square, multiple angles for theme galleries',
        'key_terms',       jsonb_build_array('theme', 'collection', 'product photo', 'DTC', 'dropshipping')
      ),
      'etsy', jsonb_build_object(
        'display_name',    'Etsy and print-on-demand sellers',
        'subreddit',       'EtsySellers',
        'official_docs',   jsonb_build_array('help.etsy.com', 'community.etsy.com'),
        'image_specs',     '2000x2000 minimum, 10 photo slots, lifestyle + flat lay',
        'key_terms',       jsonb_build_array('listing', 'mockup', 'POD', 'handmade', 'digital download')
      ),
      'tiktok_shop', jsonb_build_object(
        'display_name',    'TikTok Shop and short-video sellers',
        'subreddit',       'TikTokShop',
        'official_docs',   jsonb_build_array('seller.tiktok.com/help', 'newsroom.tiktok.com'),
        'image_specs',     'Product cover 1:1 800x800+, in-video 9:16',
        'key_terms',       jsonb_build_array('live shopping', 'cover image', 'short video', 'creator')
      )
    ),
    -- Allowed article types for this site. KeywordSelector reads this
    -- (when present) to constrain its suggested_article_type output.
    'allowed_article_types', jsonb_build_array(
      'tool_guide', 'vs_comparison', 'use_case', 'policy_guide'
    ),
    -- AdSense slot inherits ntecodex's publisher ID; revisit before
    -- ramp-up if you want separate publisher accounts per site.
    'ads', jsonb_build_object(
      'adsense_enabled', true
    )
  ),
  now(),
  now()
)
on conflict (domain) do update
   set config = excluded.config,
       site_name = excluded.site_name,
       updated_at = now();

-- Sanity check the new row.
select id, domain,
       config->>'niche' as niche,
       config->'brand'->>'name' as brand,
       jsonb_array_length(config->'allowed_article_types') as article_types,
       (config->>'monthly_budget_usd')::int as budget
  from sites
 where domain = 'pixelmatch.art';
