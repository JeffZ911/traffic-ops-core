-- 005_article_types_ecommerce.sql
-- Phase 1B (2026-05-14): widen articles.article_type CHECK constraint to
-- include the four ecommerce types added for pixelmatch.art.
--
-- Why this is a schema change (not a jsonb config tweak):
--   `article_type` is a TEXT column with a CHECK constraint that pins
--   the allowed values. The original constraint only accepted the nine
--   gaming types (build, tier_list, …, comparison). Pixelmatch needs
--   tool_guide / vs_comparison / use_case / policy_guide. There is no
--   jsonb workaround — the INSERT itself fails before any row hits the
--   pipeline.
--
-- Safety:
--   - This is non-destructive (drop + recreate widens, doesn't narrow).
--   - All existing articles already pass the new constraint (the new
--     types are additions, the old types are still allowed).
--   - No data migration, no index rebuild.
--
-- After this runs, pixelmatch.art article generation can proceed.

alter table articles drop constraint articles_article_type_check;

alter table articles add constraint articles_article_type_check
  check (article_type is null or article_type = any (array[
    -- gaming (ntecodex)
    'build', 'tier_list', 'boss_guide', 'reroll',
    'character_db', 'weapon_db', 'news', 'faq', 'comparison',
    -- ecommerce (pixelmatch — Phase 1B)
    'tool_guide', 'vs_comparison', 'use_case', 'policy_guide'
  ]));

-- Sanity check.
select pg_get_constraintdef(oid) as new_constraint
  from pg_constraint
 where conname = 'articles_article_type_check';
