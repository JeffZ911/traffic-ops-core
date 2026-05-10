-- =============================================================================
--                          ⚠️  ROLLBACK SCRIPT  ⚠️
-- =============================================================================
--
--   THIS SCRIPT WILL DROP ALL 13 TABLES + THE updated_at FUNCTION.
--   ALL DATA IN THESE TABLES WILL BE LOST. NO BACKUP IS TAKEN.
--
--   Use only when:
--     1. You are still in early dev and the DB has no real content, OR
--     2. You have an explicit ticket + verified backup.
--
--   To run: paste into Supabase SQL Editor → Run.
--   After this completes you can safely re-apply 001_initial_schema.sql.
--
--   Notes:
--     - Drops are CASCADE so FK dependents go away with their parent.
--     - Order is reverse-FK to be explicit, even though CASCADE makes it
--       order-tolerant.
--     - The pgcrypto extension is intentionally NOT dropped — Supabase
--       installs it by default and other parts of the platform may use it.
-- =============================================================================

-- Tables (reverse-FK order)
drop table if exists model_catalog       cascade;
drop table if exists daily_reports       cascade;
drop table if exists agent_runs_summary  cascade;
drop table if exists alerts              cascade;
drop table if exists ad_campaigns        cascade;
drop table if exists metrics_daily       cascade;
drop table if exists metrics_raw         cascade;
drop table if exists images              cascade;
drop table if exists agent_runs          cascade;
drop table if exists article_keywords    cascade;
drop table if exists articles            cascade;
drop table if exists keywords            cascade;
drop table if exists sites               cascade;

-- Trigger function (triggers are dropped automatically with their tables above)
drop function if exists set_updated_at();

-- =============================================================================
-- End of 001_rollback.sql
-- =============================================================================
