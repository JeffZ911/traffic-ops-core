-- 006_article_types_security_cameras.sql
-- Phase 0 (2026-05-27): widen articles.article_type CHECK constraint to
-- include the 8 security_cameras types added for quvii.com.
--
-- Mirrors 005_article_types_ecommerce.sql — additive widening, never
-- removes types. All existing rows still pass.
--
-- Without this migration the cron crashes:
--   CheckViolation: new row for relation "articles" violates check
--   constraint "articles_article_type_check"

alter table articles drop constraint articles_article_type_check;

alter table articles add constraint articles_article_type_check
  check (article_type is null or article_type = any (array[
    -- gaming (ntecodex)
    'build', 'tier_list', 'boss_guide', 'reroll',
    'character_db', 'weapon_db', 'news', 'faq', 'comparison',
    -- ecommerce (pixelmatch — Phase 1B)
    'tool_guide', 'vs_comparison', 'use_case', 'policy_guide',
    -- security_cameras (quvii — Phase 0)
    'camera_buying_guide', 'camera_comparison', 'camera_install',
    'camera_troubleshoot', 'camera_news', 'camera_learn',
    'camera_review', 'camera_support'
  ]));

-- Sanity check.
select pg_get_constraintdef(oid) as new_constraint
  from pg_constraint
 where conname = 'articles_article_type_check';
