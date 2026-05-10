-- =============================================================================
-- Migration: 001_initial_schema
-- Source:    CODE-SPEC.md v1.3 §2.2 (tables) + §2.3 (RLS)
-- Tables:    13 (sites, keywords, article_keywords, articles, agent_runs,
--                images, metrics_raw, metrics_daily, ad_campaigns, alerts,
--                agent_runs_summary, daily_reports, model_catalog)
-- Run on:    Supabase SQL Editor (manual; see src/db/README.md)
-- =============================================================================

create extension if not exists pgcrypto;

-- =============================================================================
-- §2.2.1  sites
-- =============================================================================
create table sites (
  id          uuid primary key default gen_random_uuid(),
  domain      text unique not null,
  site_name   text not null,
  status      text not null default 'active'
              check (status in ('active','paused','archived')),
  config      jsonb not null default '{}',     -- mirrors site.config.yaml
  owner_id    uuid references auth.users(id),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index idx_sites_owner on sites(owner_id);

-- =============================================================================
-- §2.2.2  keywords
-- =============================================================================
create table keywords (
  id              uuid primary key default gen_random_uuid(),
  site_id         uuid not null references sites(id) on delete cascade,
  keyword         text not null,
  intent          text,                 -- informational | comparison | how-to | list
  search_volume   integer,
  competition     numeric(3,2),         -- 0.00-1.00
  status          text not null default 'planned'
                  check (status in ('planned','in_progress','completed','skipped','archived')),
  priority_score  numeric(5,2),
  source          text,                 -- initial_research | gsc_expansion | manual | competitor_gap
  cluster_id      uuid,
  notes           text,
  last_used_at    timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique(site_id, keyword)
);
create index idx_keywords_site_status on keywords(site_id, status);
create index idx_keywords_priority on keywords(site_id, priority_score desc) where status = 'planned';
create index idx_keywords_cluster on keywords(cluster_id);

-- =============================================================================
-- §2.2.4  articles
--   (defined before article_keywords so the FK resolves)
-- =============================================================================
create table articles (
  id               uuid primary key default gen_random_uuid(),
  site_id          uuid not null references sites(id) on delete cascade,
  slug             text not null,
  title            text,
  article_type     text
                   check (article_type is null or article_type in (
                     'build','tier_list','boss_guide','reroll','character_db',
                     'weapon_db','news','faq','comparison'
                   )),
  outline          jsonb,
  content_md       text,
  word_count       integer,
  status           text not null default 'draft'
                   check (status in ('draft','writing','qa_pending','qa_failed',
                                     'qa_passed','published','archived','failed')),
  qa_score         numeric(3,1),
  qa_attempts      integer not null default 0,
  qa_feedback      jsonb,
  published_url    text,
  published_at     timestamptz,
  failure_reason   text,
  total_tokens     integer not null default 0,
  total_cost_usd   numeric(8,4) not null default 0,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique(site_id, slug)
);
create index idx_articles_site_status on articles(site_id, status);
create index idx_articles_published on articles(site_id, published_at desc);
create index idx_articles_type on articles(site_id, article_type);

-- =============================================================================
-- §2.2.3  article_keywords  (article ↔ keyword many-to-many)
-- =============================================================================
create table article_keywords (
  article_id   uuid not null references articles(id) on delete cascade,
  keyword_id   uuid not null references keywords(id) on delete cascade,
  is_primary   boolean not null default false,
  primary key (article_id, keyword_id)
);
create index idx_ak_keyword on article_keywords(keyword_id);

-- =============================================================================
-- §2.2.5  agent_runs  (retained 3 days; see §2.2.11 for daily summary)
-- =============================================================================
create table agent_runs (
  id            uuid primary key default gen_random_uuid(),
  site_id       uuid not null references sites(id) on delete cascade,
  article_id    uuid references articles(id) on delete cascade,
  agent_name    text not null,
                -- keyword_research | outline | writing | qa | image | publish
  status        text not null
                check (status in ('started','success','failed','retried')),
  input         jsonb,
  output        jsonb,
  error_msg     text,
  tokens_in     integer,
  tokens_out    integer,
  cost_usd      numeric(8,4),
  duration_ms   integer,
  model         text,
  created_at    timestamptz not null default now()
);
create index idx_agent_runs_site_time on agent_runs(site_id, created_at desc);
create index idx_agent_runs_article on agent_runs(article_id);
create index idx_agent_runs_cleanup on agent_runs(created_at);

-- =============================================================================
-- §2.2.6  images
-- =============================================================================
create table images (
  id            uuid primary key default gen_random_uuid(),
  site_id       uuid not null references sites(id) on delete cascade,
  article_id    uuid references articles(id) on delete cascade,
  prompt        text,
  url           text not null,
  alt_text      text,
  provider      text,                 -- gemini | replicate | ...
  model         text,
  aspect_ratio  text,
  cost_usd      numeric(8,4),
  created_at    timestamptz not null default now()
);
create index idx_images_article on images(article_id);

-- =============================================================================
-- §2.2.7  metrics_raw
-- =============================================================================
create table metrics_raw (
  id             bigserial primary key,
  site_id        uuid not null references sites(id) on delete cascade,
  source         text not null
                 check (source in ('ga4','gsc','adsense','fb_ads','cloudflare')),
  metric_date    date not null,
  payload        jsonb not null,
  fetched_at     timestamptz not null default now()
);
create index idx_metrics_raw_lookup on metrics_raw(site_id, source, metric_date);

-- =============================================================================
-- §2.2.8  metrics_daily
-- =============================================================================
create table metrics_daily (
  site_id              uuid not null references sites(id) on delete cascade,
  metric_date          date not null,

  -- Traffic
  sessions             integer,
  pageviews            integer,
  pv_per_session       numeric(5,2),
  avg_duration_sec     integer,
  bounce_rate          numeric(5,4),

  -- AdSense
  adsense_revenue_usd  numeric(10,4),
  adsense_pageviews    integer,
  adsense_impressions  integer,
  adsense_ctr          numeric(5,4),
  page_rpm_usd         numeric(8,4),
  invalid_traffic_pct  numeric(5,4),

  -- FB Ads
  fb_spend_usd         numeric(10,4),
  fb_clicks            integer,
  fb_impressions       integer,
  fb_cpc_usd           numeric(8,4),
  fb_ctr               numeric(5,4),
  fb_frequency         numeric(4,2),

  -- Derived
  ecpc_usd             numeric(8,4),    -- adsense_revenue / sessions
  roi                  numeric(7,4),    -- (revenue - spend) / spend

  -- SEO
  gsc_clicks           integer,
  gsc_impressions      integer,
  gsc_avg_position     numeric(5,2),

  computed_at          timestamptz not null default now(),
  primary key (site_id, metric_date)
);
create index idx_metrics_daily_date on metrics_daily(metric_date desc);

-- =============================================================================
-- §2.2.9  ad_campaigns
-- =============================================================================
create table ad_campaigns (
  id              uuid primary key default gen_random_uuid(),
  site_id         uuid not null references sites(id) on delete cascade,
  fb_campaign_id  text unique not null,
  name            text,
  status          text,                  -- active | paused | archived
  daily_budget    numeric(8,2),
  objective       text,
  last_synced_at  timestamptz,
  created_at      timestamptz not null default now()
);

-- =============================================================================
-- §2.2.10  alerts
--   (site_id is nullable per spec → allows system-level alerts)
-- =============================================================================
create table alerts (
  id              uuid primary key default gen_random_uuid(),
  site_id         uuid references sites(id) on delete cascade,
  level           text not null
                  check (level in ('critical','warning','info')),
  category        text not null,
  title           text not null,
  message         text not null,
  context         jsonb,
  acknowledged    boolean not null default false,
  acknowledged_by uuid references auth.users(id),
  acknowledged_at timestamptz,
  created_at      timestamptz not null default now()
);
create index idx_alerts_site_unack on alerts(site_id, acknowledged, created_at desc);

-- =============================================================================
-- §2.2.11  agent_runs_summary  (daily aggregate retained after agent_runs cleanup)
-- =============================================================================
create table agent_runs_summary (
  site_id           uuid not null references sites(id) on delete cascade,
  summary_date      date not null,
  agent_name        text not null,
  total_runs        integer not null,
  success_count     integer not null,
  failure_count     integer not null,
  total_tokens_in   bigint not null,
  total_tokens_out  bigint not null,
  total_cost_usd    numeric(10,4) not null,
  avg_duration_ms   integer,
  primary key (site_id, summary_date, agent_name)
);

-- =============================================================================
-- §2.2.12  daily_reports
-- =============================================================================
create table daily_reports (
  id            uuid primary key default gen_random_uuid(),
  site_id       uuid not null references sites(id) on delete cascade,
  report_date   date not null,
  markdown      text not null,
  ai_summary    text,
  data_snapshot jsonb,
  sent_to       text[],
  created_at    timestamptz not null default now(),
  unique(site_id, report_date)
);

-- =============================================================================
-- §2.2.13  model_catalog  (system-wide LLM/image model registry)
-- =============================================================================
create table model_catalog (
  id                  uuid primary key default gen_random_uuid(),
  provider            text not null,          -- gemini | openai | anthropic
  model_id            text not null,
  display_name        text not null,
  modality            text not null
                      check (modality in ('text','image','embedding')),
  task_types          text[] not null,
  tier                text,                   -- pro | flash | flash-lite
  input_cost_per_1m   numeric(8,4),
  output_cost_per_1m  numeric(8,4),
  per_image_cost      numeric(8,4),
  context_window      integer,
  supports_json_mode  boolean default false,
  status              text not null default 'active'
                      check (status in ('preview','active','deprecated')),
  is_recommended      boolean not null default false,
  released_at         date,
  deprecate_at        date,
  last_verified_at    timestamptz,
  last_verify_error   text,
  notes               text,
  added_at            timestamptz default now()
);
create unique index uq_model_catalog on model_catalog(provider, model_id);
create index idx_model_catalog_modality_status on model_catalog(modality, status);

-- =============================================================================
-- §2.2.13 seed data — initial model catalog (snapshot 2026-05)
-- =============================================================================
insert into model_catalog (provider, model_id, display_name, modality, task_types, tier,
                           input_cost_per_1m, output_cost_per_1m, per_image_cost, context_window,
                           supports_json_mode, status, is_recommended, released_at, notes) values

-- Text models (recommended pairing: writing=Flash + qa=Pro for cross-check)
('gemini', 'gemini-3.1-pro-preview', 'Gemini 3.1 Pro (Preview) — 推荐用于质检', 'text',
 ARRAY['writing','qa','outline','keyword_research'], 'pro',
 2.00, 12.00, NULL, 1000000, true, 'preview', true, '2026-02-19',
 '当前最强推理模型；质检 Agent 首选，挑出 Flash 写作的问题'),

('gemini', 'gemini-3-flash-preview', 'Gemini 3 Flash (Preview) — 推荐用于写作', 'text',
 ARRAY['writing','outline','keyword_research','report_summary'], 'flash',
 0.30, 2.50, NULL, 1000000, true, 'preview', true, '2025-12-01',
 '写作 Agent 首选；速度快、成本低、质量足够'),

('gemini', 'gemini-3.1-flash-lite-preview', 'Gemini 3.1 Flash Lite (Preview)', 'text',
 ARRAY['keyword_research','keyword_expansion'], 'flash-lite',
 0.10, 0.40, NULL, 1000000, true, 'preview', false, '2026-03-01',
 '最便宜，适合大批量轻任务如关键词扩展'),

-- Image models
('gemini', 'gemini-2.5-flash-image', 'Nano Banana — 默认配图', 'image',
 ARRAY['image_gen'], 'flash',
 NULL, NULL, 0.039, NULL, false, 'active', true, '2025-10-07',
 '~$0.039/张，快速文章配图首选'),

('gemini', 'gemini-3.1-flash-image-preview', 'Nano Banana 2 (Preview)', 'image',
 ARRAY['image_gen'], 'flash',
 NULL, NULL, 0.067, NULL, false, 'preview', false, '2026-03-01',
 '~$0.067/张，速度+质量平衡'),

('gemini', 'gemini-3-pro-image-preview', 'Nano Banana Pro (Preview)', 'image',
 ARRAY['image_gen'], 'pro',
 NULL, NULL, 0.12, NULL, false, 'preview', false, '2025-12-15',
 '~$0.12/张，含文字渲染的高质量图');

-- =============================================================================
-- updated_at auto-refresh trigger
--   Tables with an updated_at column: sites, keywords, articles.
--   The function bumps updated_at to now() on every UPDATE.
-- =============================================================================
create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger trg_sites_set_updated_at
  before update on sites
  for each row execute function set_updated_at();

create trigger trg_keywords_set_updated_at
  before update on keywords
  for each row execute function set_updated_at();

create trigger trg_articles_set_updated_at
  before update on articles
  for each row execute function set_updated_at();

-- =============================================================================
-- §2.3  Row-Level Security
--   Pattern (per spec):
--     - Frontend uses anon key + RLS → can only read rows belonging to
--       sites where owner_id = auth.uid().
--     - Backend Pipeline uses service_role key → bypasses RLS entirely.
--   So we only need SELECT policies for authenticated users; no INSERT /
--   UPDATE / DELETE policies (those go through service_role).
-- =============================================================================

alter table sites              enable row level security;
alter table keywords           enable row level security;
alter table articles           enable row level security;
alter table article_keywords   enable row level security;
alter table agent_runs         enable row level security;
alter table images             enable row level security;
alter table metrics_raw        enable row level security;
alter table metrics_daily      enable row level security;
alter table ad_campaigns       enable row level security;
alter table alerts             enable row level security;
alter table agent_runs_summary enable row level security;
alter table daily_reports      enable row level security;
alter table model_catalog      enable row level security;

-- sites: owner-direct
create policy "users read own sites"
  on sites for select
  using (owner_id = auth.uid());

-- Tables with site_id FK: filter through sites.owner_id
create policy "users read keywords of own sites"
  on keywords for select
  using (site_id in (select id from sites where owner_id = auth.uid()));

create policy "users read articles of own sites"
  on articles for select
  using (site_id in (select id from sites where owner_id = auth.uid()));

create policy "users read agent_runs of own sites"
  on agent_runs for select
  using (site_id in (select id from sites where owner_id = auth.uid()));

create policy "users read images of own sites"
  on images for select
  using (site_id in (select id from sites where owner_id = auth.uid()));

create policy "users read metrics_raw of own sites"
  on metrics_raw for select
  using (site_id in (select id from sites where owner_id = auth.uid()));

create policy "users read metrics_daily of own sites"
  on metrics_daily for select
  using (site_id in (select id from sites where owner_id = auth.uid()));

create policy "users read ad_campaigns of own sites"
  on ad_campaigns for select
  using (site_id in (select id from sites where owner_id = auth.uid()));

create policy "users read agent_runs_summary of own sites"
  on agent_runs_summary for select
  using (site_id in (select id from sites where owner_id = auth.uid()));

create policy "users read daily_reports of own sites"
  on daily_reports for select
  using (site_id in (select id from sites where owner_id = auth.uid()));

-- alerts: site_id may be null (system-wide) → visible to all signed-in users
create policy "users read alerts of own sites or system"
  on alerts for select
  using (
    site_id is null
    or site_id in (select id from sites where owner_id = auth.uid())
  );

-- article_keywords: no site_id; join through articles
create policy "users read article_keywords of own articles"
  on article_keywords for select
  using (
    article_id in (
      select a.id from articles a
      join sites s on s.id = a.site_id
      where s.owner_id = auth.uid()
    )
  );

-- model_catalog: system-wide registry; readable by any authenticated user
create policy "authenticated read model_catalog"
  on model_catalog for select
  to authenticated
  using (true);

-- =============================================================================
-- End of 001_initial_schema.sql
-- =============================================================================
