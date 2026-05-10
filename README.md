# traffic-ops-core

Backend pipeline for the AI Site Operator system: data collection, content
generation agents, decision rules, scheduling, and reporting.

## Stack
- Python 3.11
- Supabase (Postgres) — schema in `src/db/migrations/`
- GitHub Actions — daily cron for content / data / report / alerts
- Gemini (writing, QA, images) + Claude (planned, complex reasoning)

## Status
Initialized. No business code yet. See `/docs` (one level up) for spec.

- `docs/PRD-AI-Site-Operator.md` — product decisions
- `docs/CODE-SPEC.md` — implementation blueprint
- `docs/CREDENTIALS-SETUP.md` — secrets & env layout

## Local setup
```bash
cp .env.example .env   # already done; fill values manually
# never commit .env
```

Env variable names mirror GitHub Secrets (19 total, see
`CREDENTIALS-SETUP.md §2`). Site-specific secrets are prefixed with the
uppercased `site_slug`. First site: `ntecodex` → `NTECODEX_*`.
