# Infrastructure Map

Single source of truth for "which thing lives where" across the NTE Codex /
traffic-ops stack. Update this file whenever a new service, repo, or
credential is added.

Last verified: 2026-05-11.

---

## 1. Cloudflare Pages projects

Account ID: `98ccff1f0df4342c72c1a4a06bf48433`
Zone (`ntecodex.com`) ID: `804bd8908c64a8f20508ff71c9258cc8`

| Project | Custom domains | Source | Purpose |
|---|---|---|---|
| `ntecodex` | `ntecodex.com`, `www.ntecodex.com`, `ntecodex.pages.dev` | Direct upload via `wrangler pages deploy` | **Production site.** Astro static build + `/api/comments` Pages Function. |
| `traffic-ops-dashboard` | `admin.ntecodex.com`, `traffic-ops-dashboard.pages.dev` | Direct upload | Internal admin dashboard (Next.js 15 + React 19). |

**Deleted 2026-05-11:** `ntecodex-site` — orphaned GitHub-integrated project
that no longer served any custom domain. Auto-deploys on every push were
burning build minutes without reaching production.

**Deploy command** (from `ntecodex-site/` repo working tree):

```bash
set -a; source ../traffic-ops-core/.env; set +a
npm run build
npx wrangler pages deploy dist --project-name=ntecodex --branch=main --commit-dirty=true
```

After deploy, purge the zone cache so `ntecodex.com` picks up the new build:

```bash
curl -X POST "https://api.cloudflare.com/client/v4/zones/804bd8908c64a8f20508ff71c9258cc8/purge_cache" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"purge_everything":true}'
```

---

## 2. GitHub repositories

Owner: `JeffZ911`

| Repo | Used by | Purpose |
|---|---|---|
| `JeffZ911/ntecodex-site` | `ntecodex-site/` worktree | Astro source for the public site. Now manual deploy (GitHub→Pages integration removed with the orphan project). |
| `JeffZ911/traffic-ops-core` | `traffic-ops-core/` worktree | Python content pipeline: KeywordSelector → Outline → Writing → QA → Publish agents. Runs locally + scheduled job. |
| `JeffZ911/traffic-ops-dashboard` | `traffic-ops-dashboard/` worktree | Next.js admin: comment moderation, GA4/GSC stats, pipeline trigger UI. |

No GitHub Actions / branch protection configured yet — deploys are manual.

---

## 3. Production services

### 3.1 Supabase (Postgres + RLS)
- Project ref: `***REDACTED***`
- URL: `https://***REDACTED***.supabase.co`
- Tables in use: `user_messages` (comments + contact), `articles`, `keywords`, `agent_runs`
- Migrations: `traffic-ops-core/migrations/*.sql` (manual apply)
- RLS: anon role denied SELECT on `user_messages`. Pages Function `/api/comments` uses `service_role` key (server-side only) to bypass RLS.

### 3.2 Cloudflare Turnstile
- Site key (public, in HTML): `0x4AAAAAADM08-ygJBMR__JG`
- Mode: Managed (auto-solves invisibly for clean browsers)
- Used on: `/contact` form + `<CommentSection>` on every article

### 3.3 Google AdSense
- Publisher ID: `ca-pub-5523706123080113`
- Mode: **Auto Ads** (no manual `<ins>` blocks in source)
- Loader injected via `BaseLayout.astro`

### 3.4 Google Analytics 4 + Search Console
- GA4 property: `G-FBQDFV7CXF`
- Auth: OAuth user credentials (stored as `GOOGLE_OAUTH_CLIENT_JSON` + `GOOGLE_OAUTH_REFRESH_TOKEN` in `traffic-ops-core/.env`)
- Consumed by the dashboard's `/api/analytics/*` routes

### 3.5 AI providers (used by traffic-ops-core)
- Vertex AI (default route via `vertexai: true` in `shared/ai/provider.py`)
- Claude (for complex reasoning agents)
- Gemini Flash (for high-volume tasks)

---

## 4. Credentials map

**Rule of thumb:** if a credential is needed at build/deploy time → `.env`.
If at runtime by the live CF Function → CF Pages env. Never both, never
hardcoded.

| Credential | Where it lives | Notes |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | `traffic-ops-core/.env` (local) | Used by wrangler deploys and `curl` cache-purges. |
| `CLOUDFLARE_ACCOUNT_ID` | `traffic-ops-core/.env` | `98ccff1f0df4342c72c1a4a06bf48433` |
| `SUPABASE_URL` | `traffic-ops-core/.env` + CF Pages env (Production + Preview) | Public-ish but kept in env for portability. |
| `SUPABASE_SERVICE_ROLE_KEY` | **CF Pages env only** (Production + Preview, encrypted) | Server-side bypass key. NEVER committed, NEVER in client JS. |
| `SUPABASE_ANON_KEY` | `traffic-ops-core/.env` | Used by core pipeline for safe reads. |
| `TURNSTILE_SECRET` | CF Pages env only | Validates tokens in `/api/comments` Function. |
| `PUBLIC_TURNSTILE_SITE_KEY` | Hardcoded default in `CommentSection.astro` + `contact.astro` | Public, exposed in DOM. Hardcoded so local `wrangler pages deploy` works without injecting Astro build env. |
| `GOOGLE_OAUTH_CLIENT_JSON` | `traffic-ops-core/.env` | User OAuth — drives GA4/GSC dashboard queries. |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | `traffic-ops-core/.env` | Refresh token from initial consent flow. |
| `ANTHROPIC_API_KEY` | `traffic-ops-core/.env` | Claude calls. |
| `GOOGLE_GENAI_API_KEY` | `traffic-ops-core/.env` | Gemini calls. |
| AdSense publisher ID (`ca-pub-5523706123080113`) | Hardcoded in `BaseLayout.astro` | Public. |

GitHub Secrets: **none used yet** (no GitHub Actions deployment).

---

## 5. Key IDs (quick reference)

| Thing | ID |
|---|---|
| Cloudflare Account | `98ccff1f0df4342c72c1a4a06bf48433` |
| Cloudflare Zone (ntecodex.com) | `804bd8908c64a8f20508ff71c9258cc8` |
| Supabase project ref | `***REDACTED***` |
| GA4 property | `G-FBQDFV7CXF` |
| AdSense publisher | `ca-pub-5523706123080113` |
| Turnstile site key | `0x4AAAAAADM08-ygJBMR__JG` |

---

## 6. Local repo layout

```
/Users/jeffzen/Documents/traffic-ops/
├── ntecodex-site/           # Astro public site (deploys to "ntecodex" CF Pages)
├── traffic-ops-core/        # Python pipeline + scripts
├── traffic-ops-dashboard/   # Next.js admin (deploys to "traffic-ops-dashboard" CF Pages)
└── docs/                    # This file lives here
    ├── INFRASTRUCTURE.md            ← you are here
    ├── PRD-AI-Site-Operator.md
    ├── CODE-SPEC.md
    ├── CREDENTIALS-SETUP.md
    ├── HUMAN-RUNBOOK.md
    ├── SITE-STRUCTURE.md
    └── PHASE-1A-COMPLETION-REPORT.md
```
