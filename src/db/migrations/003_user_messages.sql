-- =============================================================================
-- Migration: 003_user_messages
-- Purpose:   single table for both article comments and contact-form messages.
--            Public anon clients can INSERT (the Pages Function validates
--            Turnstile upstream and uses service_role to write); only the
--            site owner can read / update / delete via the dashboard.
-- =============================================================================

create table user_messages (
  id              uuid primary key default gen_random_uuid(),
  site_id         uuid not null references sites(id) on delete cascade,
  message_type    text not null
                  check (message_type in ('comment', 'contact')),
  -- For type='comment': slug of the article being commented on.
  -- For type='contact': NULL.
  article_slug    text,
  user_name       text,                       -- optional; "" → renders "Anonymous"
  user_email      text,                       -- optional (no SMTP reply path anyway)
  content         text not null
                  check (length(content) between 1 and 2000),
  status          text not null default 'pending'
                  check (status in ('pending', 'approved', 'rejected', 'spam')),
  ip_address      inet,
  user_agent      text,
  turnstile_token text,
  reviewed_by     uuid references auth.users(id),
  reviewed_at     timestamptz,
  created_at      timestamptz not null default now()
);

-- Hot path: rendering approved comments for one article
create index idx_user_messages_article_approved
  on user_messages(article_slug, created_at desc)
  where status = 'approved' and article_slug is not null;

-- Dashboard review queue
create index idx_user_messages_pending
  on user_messages(site_id, created_at desc)
  where status = 'pending';

-- Catch-all for status filters
create index idx_user_messages_site_status
  on user_messages(site_id, status, created_at desc);

-- ---- RLS ----
alter table user_messages enable row level security;

-- anon inserts: rate-gating + integrity guards.
-- (Real production traffic goes through the Pages Function with service_role,
--  which bypasses RLS; this policy is the safety net if a direct PostgREST
--  insert is ever exposed.)
create policy "anon insert messages"
  on user_messages for insert to anon
  with check (
    site_id in (select id from sites)
    and status = 'pending'
    and length(content) between 1 and 2000
  );

-- Owner reads everything for their site (dashboard list views)
create policy "owner read site messages"
  on user_messages for select to authenticated
  using (site_id in (select id from sites where owner_id = auth.uid()));

-- Owner approves / rejects / marks-spam
create policy "owner update site messages"
  on user_messages for update to authenticated
  using (site_id in (select id from sites where owner_id = auth.uid()))
  with check (site_id in (select id from sites where owner_id = auth.uid()));

-- Owner deletes (e.g. spam cleanup, user delete-request)
create policy "owner delete site messages"
  on user_messages for delete to authenticated
  using (site_id in (select id from sites where owner_id = auth.uid()));

-- service_role automatically bypasses RLS; no policy needed.
