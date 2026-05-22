-- 006_ops_tasks.sql
-- Human action-item tracker surfaced in the Dashboard (/todos).
--
-- Purely ADDITIVE — new isolated table, touches no pipeline table. The
-- autonomous pipeline runs everything it can; some steps genuinely need
-- a human in a 3rd-party console (GA4, GSC, Cloudflare, DNS, GitHub
-- secrets, OAuth). Those land here as actionable cards: what to do, how
-- to do it, mark done. Completed rows persist so retrospectives + future
-- automation can see "what kept needing a human".

create table if not exists ops_tasks (
  id           uuid primary key default gen_random_uuid(),
  title        text not null,                 -- short imperative ("Add GA4 property for X")
  detail       text,                          -- what + exact how (steps, URLs)
  status       text not null default 'open'
               check (status in ('open', 'done', 'dismissed')),
  priority     text not null default 'normal'
               check (priority in ('high', 'normal', 'low')),
  category     text,                          -- 'seo' | 'infra' | 'content' | 'new-site' | 'billing' | ...
  site_domain  text,                          -- nullable; null = platform-wide
  source       text not null default 'manual' -- 'manual' (operator) | 'auto' (script-created)
               check (source in ('manual', 'auto')),
  created_at   timestamptz not null default now(),
  completed_at timestamptz
);

create index if not exists ops_tasks_status_idx on ops_tasks (status, priority, created_at desc);

-- Sanity check
select count(*) as ops_tasks_rows from ops_tasks;
