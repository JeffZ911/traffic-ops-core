# Database Migrations

SQL migrations for the Supabase Postgres backing `traffic-ops-core`.
Source of truth: [`docs/CODE-SPEC.md` §2](../../../docs/CODE-SPEC.md).

> **Per spec §2 line 165**: all schema changes go through migration files.
> Manual table creation in the Supabase UI is forbidden.

---

## Files

| File | Purpose |
|---|---|
| `migrations/001_initial_schema.sql` | Initial 13 tables + indexes + RLS + `model_catalog` seed |

---

## Running a migration (Supabase Dashboard, manual)

We do not use the Supabase CLI yet — migrations are applied by hand via the SQL Editor.

1. Open Supabase project → **SQL Editor** → **New query**.
2. Open the migration file locally, copy the **entire contents**.
3. Paste into the SQL Editor.
4. Click **Run** (top right).
5. Expect: `Success. No rows returned` (the final statement is policy creation, which returns nothing).
6. If any statement fails, **stop**. Do not edit and retry partial — fix the migration file, drop the partial state, and re-run from scratch (see "Rollback" below).

---

## Verifying `001_initial_schema.sql`

After running, paste the following into a new SQL Editor query and run:

```sql
-- 1. All 13 tables present
select table_name
from information_schema.tables
where table_schema = 'public'
order by table_name;
```

Expected output (13 rows):
```
ad_campaigns
agent_runs
agent_runs_summary
alerts
article_keywords
articles
daily_reports
images
keywords
metrics_daily
metrics_raw
model_catalog
sites
```

```sql
-- 2. RLS enabled on all 13 tables
select tablename, rowsecurity
from pg_tables
where schemaname = 'public'
order by tablename;
```
Every row should show `rowsecurity = true`.

```sql
-- 3. model_catalog seeded with 6 models
select count(*) as model_count from model_catalog;
-- expect: 6

select provider, model_id, modality, is_recommended
from model_catalog
order by modality, tier;
-- expect: 3 text rows + 3 image rows; the two `is_recommended=true` text
-- rows are gemini-3.1-pro-preview and gemini-3-flash-preview;
-- the recommended image row is gemini-2.5-flash-image
```

```sql
-- 4. CHECK constraints in place across all enum-style columns
select conrelid::regclass as table_name, conname, pg_get_constraintdef(oid)
from pg_constraint
where contype = 'c'
  and conrelid in (
    'sites'::regclass, 'keywords'::regclass, 'articles'::regclass,
    'agent_runs'::regclass, 'metrics_raw'::regclass, 'alerts'::regclass,
    'model_catalog'::regclass
  )
order by table_name, conname;
-- expect 9 rows total:
--   sites.status            in (active, paused, archived)
--   keywords.status         in (planned, in_progress, completed, skipped, archived)
--   articles.status         in (draft, writing, qa_pending, qa_failed,
--                               qa_passed, published, archived, failed)
--   articles.article_type   in (build, tier_list, boss_guide, reroll,
--                               character_db, weapon_db, news, faq, comparison)
--   agent_runs.status       in (started, success, failed, retried)
--   metrics_raw.source      in (ga4, gsc, adsense, fb_ads, cloudflare)
--   alerts.level            in (critical, warning, info)
--   model_catalog.modality  in (text, image, embedding)
--   model_catalog.status    in (preview, active, deprecated)
```

```sql
-- 4b. updated_at triggers in place
select event_object_table, trigger_name
from information_schema.triggers
where trigger_schema = 'public'
order by event_object_table;
-- expect 3 rows:
--   articles  trg_articles_set_updated_at
--   keywords  trg_keywords_set_updated_at
--   sites     trg_sites_set_updated_at
```

```sql
-- 5. Spot-check a few key indexes
select indexname from pg_indexes
where schemaname = 'public'
  and indexname in (
    'idx_keywords_priority',
    'idx_articles_site_status',
    'idx_agent_runs_cleanup',
    'uq_model_catalog'
  );
-- expect: 4 rows
```

If any check fails, the migration did not apply cleanly — see Rollback.

---

## Rollback (during early development only)

> ⚠️ Destroys all data. Only safe before any real content lives in the DB.

```sql
-- Drop in reverse-FK order
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
```

Then re-run `001_initial_schema.sql` from the top.

---

## Naming convention for future migrations

```
db/migrations/<NNN>_<snake_case_description>.sql
```

- `<NNN>`: three-digit zero-padded, monotonically increasing. Never reuse, never reorder.
- `<snake_case_description>`: short imperative phrase describing the change.
- One concern per file. Splittable changes go in separate migrations.

Examples:
```
002_add_competitor_tracking.sql
003_alter_keywords_add_serp_features.sql
004_seed_default_prompts.sql
```

Every migration must:
- Be idempotent only where the spec calls for it (e.g. `create extension if not exists`). New tables / columns should fail loudly if rerun.
- Never `drop` production data without an explicit ticket and a backup.
- Be applied in order: never run `003` before `002`.

---

## What is NOT in this directory

- ORM models / Pydantic schemas → `src/db/models.py` (per CODE-SPEC §1.1)
- Supabase client wrapper → `src/db/client.py`
- Seed data for `sites` (the first NTE site row) → handled by a separate
  bootstrap script, not a migration, because it depends on `auth.users.id`
  which only exists after you sign up in Supabase Auth.
