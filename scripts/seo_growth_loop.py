"""SEO growth loop — the human+AI authority + retrospective engine.

Closes the loop the funnel diagnosis exposed: the sites produce content
fine but Google won't index/rank a zero-authority domain. Authority is
EARNED through off-page work that only a human can do (community posts,
outreach, getting cited). This script turns that into a concrete,
rotating weekly worklist in ops_tasks (the Dashboard /todos), and then
reviews — using real GSC/GA4 movement — whether last cycle's manual
work actually moved the needle, feeding the result back as a card.

Three jobs (run all by default, or pick with --mode):

  A. authority  — generate this week's SPECIFIC off-page tasks per site,
                  across 3 routes (community / linkable-asset / outreach),
                  niche-aware, referencing a real article so the action
                  is concrete, not a vague "build backlinks".

  C. review     — weekly retrospective per site: pull the GSC indexing +
                  impressions + GA4 sessions trend, count how many
                  authority tasks were completed last cycle, and write a
                  "what moved / what's next" card so the operator SEES
                  the action→outcome link (the 复盘).

(B — the staged indexing-escalation ladder — lives in ops_autoflag.py
 alongside the other health checks.)

Idempotent: authority cards are week-stamped in the title, so each ISO
week gets exactly one fresh set and old ones remain as history. The
review card is also week-stamped.

Usage:
  python -m scripts.seo_growth_loop                 # all sites, all modes
  python -m scripts.seo_growth_loop --site quvii.com
  python -m scripts.seo_growth_loop --mode authority
  python -m scripts.seo_growth_loop --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from src.db.client import get_db_connection
from src.utils.ops_tasks import upsert_open_task


# ─────────────────────────────────────────────────────────────────────
# Niche-aware authority targets. Like link_rewriter's NICHE_DEFAULTS:
# a new site inherits its route targets from its niche. Operator can
# refine per-site later via sites.config if needed.
# ─────────────────────────────────────────────────────────────────────
NICHE_AUTHORITY = {
    "security_cameras": {
        "subreddits": ["r/HomeSecurity", "r/homedefense", "r/homeautomation",
                       "r/smarthome", "r/HomeNetworking"],
        "forums": ["Reddit r/HomeSecurity weekly Q&A", "Quora 'home security camera' topic",
                   "r/homeautomation discussion threads"],
        "directories": ["AlternativeTo (security category)", "Product Hunt (if a tool launches)",
                        "relevant 'best home security blogs' roundups (pitch inclusion)"],
        "haro_beat": "Technology / Home & Garden / Consumer Electronics (security cameras, privacy)",
        "asset_ideas": [
            "a camera subscription-cost comparison table (Ring vs Nest vs Eufy vs Arlo, 3-yr TCO)",
            "a 'do I need a subscription?' decision flowchart",
            "a Wi-Fi bandwidth-per-camera calculator",
            "a local-vs-cloud storage privacy comparison",
        ],
    },
    "gaming": {
        "subreddits": ["r/MMORPG", "r/gachagaming", "r/battlestations",
                       "r/pcmasterrace", "r/buildapcsales"],
        "forums": ["r/gachagaming daily questions", "GameFAQs boards", "Discord LFG/help channels"],
        "directories": ["gaming-blog roundups", "AlternativeTo", "relevant wiki external-links sections"],
        "haro_beat": "Gaming / Technology / Esports gear",
        "asset_ideas": [
            "a gaming-gear price/spec comparison table for the target game's community",
            "a 'best budget setup under $X' build list",
            "an ergonomics-for-long-sessions guide with a posture checklist",
        ],
    },
    "ecommerce_tools": {
        "subreddits": ["r/FulfillmentByAmazon", "r/Etsy", "r/shopify",
                       "r/ecommerce", "r/AmazonSeller"],
        "forums": ["r/FulfillmentByAmazon weekly threads", "Shopify Community forums",
                   "Etsy seller forums", "Indie Hackers"],
        "directories": ["AlternativeTo (ecommerce tools)", "Product Hunt", "SaaS directories",
                        "'best product photography tools' roundups"],
        "haro_beat": "Small Business / E-commerce / Marketing",
        "asset_ideas": [
            "an Amazon/Etsy/Shopify image-spec cheat sheet (one table, all platforms)",
            "a before/after AI product-photo case study with real numbers",
            "an image-policy compliance checklist per marketplace",
        ],
    },
}


def _active_sites(site_filter):
    with get_db_connection() as conn, conn.cursor() as cur:
        if site_filter:
            cur.execute("select domain, config->>'niche' from sites where domain=%s", (site_filter,))
        else:
            cur.execute("select domain, config->>'niche' from sites order by domain")
        return [(r[0], r[1] or "") for r in cur.fetchall()]


def _site_id(domain):
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain=%s", (domain,))
        r = cur.fetchone()
        return str(r[0]) if r else None


def _pick_articles(domain, n=3):
    """Pick the n highest-qa recent published articles to anchor outreach
    on — a real URL + title makes the task concrete."""
    sid = _site_id(domain)
    if not sid:
        return []
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select title, coalesce(published_url, '/'||slug) url, qa_score
              from articles
             where site_id=%s and status='published'
             order by qa_score desc nulls last, published_at desc
             limit %s
            """,
            (sid, n),
        )
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def _rotate(items, week_idx):
    """Deterministic weekly rotation through a list (no randomness — keeps
    runs reproducible and avoids Date/random sandbox issues)."""
    if not items:
        return None
    return items[week_idx % len(items)]


# ───────────────────────────── A. authority ─────────────────────────────

def gen_authority(domain, niche, today, dry_run):
    cfg = NICHE_AUTHORITY.get(niche)
    if not cfg:
        print(f"  {domain}: no authority profile for niche={niche!r} — skip")
        return
    iso_year, iso_week, _ = today.isocalendar()
    wk = f"{iso_year}-W{iso_week:02d}"
    articles = _pick_articles(domain, 3)

    # Rotate targets weekly so the operator isn't asked to spam the same
    # subreddit every week.
    sub = _rotate(cfg["subreddits"], iso_week)
    asset = _rotate(cfg["asset_ideas"], iso_week)
    directory = _rotate(cfg["directories"], iso_week)

    art_line = ""
    if articles:
        t, u, _ = articles[0]
        art_line = f"\n   Anchor article: \"{t}\" ({u})"
    art2 = ""
    if len(articles) > 1:
        t2, u2, _ = articles[1]
        art2 = f"\n   Or this one: \"{t2}\" ({u2})"

    # Route 1 — Community value (low cost, sustainable)
    title1 = f"[Authority · Community] {domain} — {wk}"
    detail1 = (
        f"GOAL: earn a contextual mention/link by being genuinely helpful "
        f"(NOT spam — answer a real question first, reference the article "
        f"only if it truly helps).\n"
        f"THIS WEEK: find an unanswered/under-answered question in {sub} "
        f"(or another listed community) that one of your articles actually "
        f"answers, and post a real, useful reply.{art_line}{art2}\n"
        f"Other communities for this niche: {', '.join(cfg['forums'])}.\n"
        f"RULE: 1 high-quality answer > 10 drive-by links. Disclose if asked."
    )

    # Route 2 — Linkable asset (creates natural backlink magnets)
    title2 = f"[Authority · Asset] {domain} — {wk}"
    detail2 = (
        f"GOAL: build/promote a 'linkable asset' — content others cite "
        f"without being asked.\n"
        f"THIS WEEK's idea: {asset}.\n"
        f"If it already exists on the site, promote it (share in 1 community "
        f"+ note it in your next article). If not, it's a strong candidate "
        f"for the next high-effort piece — these earn links passively for "
        f"months. Data tables + calculators + original comparisons get cited "
        f"most."
    )

    # Route 3 — Directory / HARO / outreach (active, faster signal)
    title3 = f"[Authority · Outreach] {domain} — {wk}"
    detail3 = (
        f"GOAL: 1 active outreach action this week.\n"
        f"PICK ONE:\n"
        f"  • Submit the site to: {directory}.\n"
        f"  • Answer 1 journalist request on HARO/Qwoted/Featured in the "
        f"'{cfg['haro_beat']}' beat (a quote with your domain = an authority "
        f"backlink).\n"
        f"  • Email 1 complementary (non-competing) site in the niche "
        f"proposing a genuine resource swap or guest contribution.\n"
        f"Track replies — outreach is a numbers game; ~1 in 10 lands."
    )

    for title, detail in [(title1, detail1), (title2, detail2), (title3, detail3)]:
        if dry_run:
            print(f"  [DRY] would upsert: {title}")
        else:
            res = upsert_open_task(title, detail, priority="normal",
                                   category="authority", site_domain=domain)
            print(f"  {res}: {title}")


# ───────────────────────────── C. review ─────────────────────────────

def _trend(domain, days=14):
    """Pull GSC + GA4 movement: first-half vs second-half of the window."""
    sid = _site_id(domain)
    if not sid:
        return None
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select
              coalesce(sum(gsc_impressions) filter (where metric_date >= current_date - %s/2),0) impr_recent,
              coalesce(sum(gsc_impressions) filter (where metric_date <  current_date - %s/2),0) impr_prior,
              coalesce(sum(gsc_clicks)      filter (where metric_date >= current_date - %s/2),0) clk_recent,
              coalesce(sum(sessions)        filter (where metric_date >= current_date - %s/2),0) sess_recent,
              coalesce(sum(sessions)        filter (where metric_date <  current_date - %s/2),0) sess_prior
            from metrics_daily
            where site_id=%s and metric_date >= current_date - %s
            """,
            (days, days, days, days, days, sid, days),
        )
        r = cur.fetchone()
        return {
            "impr_recent": int(r[0]), "impr_prior": int(r[1]),
            "clk_recent": int(r[2]),
            "sess_recent": int(r[3]), "sess_prior": int(r[4]),
        }


def _authority_done_count(domain, days=14):
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select count(*) from ops_tasks
             where site_domain=%s and category='authority'
               and status='done' and completed_at >= now() - (%s||' days')::interval
            """,
            (domain, days),
        )
        return int(cur.fetchone()[0])


def gen_review(domain, niche, today, dry_run):
    iso_year, iso_week, _ = today.isocalendar()
    wk = f"{iso_year}-W{iso_week:02d}"
    t = _trend(domain, 14)
    if t is None:
        return
    done = _authority_done_count(domain, 14)

    def arrow(recent, prior):
        if recent > prior: return f"↑ {prior}→{recent}"
        if recent < prior: return f"↓ {prior}→{recent}"
        return f"→ flat at {recent}"

    impr = arrow(t["impr_recent"], t["impr_prior"])
    sess = arrow(t["sess_recent"], t["sess_prior"])

    # Narrative: connect the manual work to the outcome honestly, with the
    # 4-8 week backlink lag caveat so flat results aren't misread as failure.
    if t["impr_recent"] == 0 and t["impr_prior"] == 0:
        verdict = (
            "Still 0 GSC impressions. This is the authority wall, not a "
            "content problem. Backlinks take 4-8 weeks to move indexing — "
            f"you completed {done} authority task(s) in the last 2 weeks; "
            "keep the cadence, the signal lags the work."
            if done else
            "Still 0 GSC impressions AND 0 authority tasks completed in 2 "
            "weeks. Nothing is pushing on the bottleneck. Do at least the "
            "Community + Outreach cards this week — content alone will not "
            "break the index wall."
        )
    elif t["impr_recent"] > t["impr_prior"]:
        verdict = (
            f"Impressions moving UP ({impr}) — early authority traction. "
            f"{done} authority task(s) done in 2 weeks is correlating. "
            "Double down on whichever route you actually did."
        )
    else:
        verdict = (
            f"Impressions {impr}. Mixed. Keep the weekly authority cadence; "
            "review again next week."
        )

    title = f"[Funnel review] {domain} — {wk}"
    detail = (
        f"2-WEEK FUNNEL MOVEMENT ({domain}):\n"
        f"  GSC impressions: {impr}\n"
        f"  GSC clicks (recent): {t['clk_recent']}\n"
        f"  GA4 sessions: {sess}\n"
        f"  Authority tasks completed (last 14d): {done}\n\n"
        f"VERDICT: {verdict}\n\n"
        f"This card is the retrospective: it ties the manual work you did "
        f"to what actually moved. If impressions stay 0 for 30+ days with "
        f"authority work happening, escalate (reconsider niche competition "
        f"or go harder on outreach)."
    )
    if dry_run:
        print(f"  [DRY] would upsert: {title}")
        print("       " + verdict)
    else:
        res = upsert_open_task(title, detail, priority="normal",
                               category="seo-review", site_domain=domain)
        print(f"  {res}: {title}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--site")
    p.add_argument("--mode", choices=["authority", "review", "all"], default="all")
    p.add_argument("--dry-run", action="store_true")
    # today injectable for testing / reproducibility (scripts can't use
    # Date.now in the workflow sandbox, but this is a plain Python CLI).
    p.add_argument("--today", help="YYYY-MM-DD override")
    args = p.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    sites = _active_sites(args.site)
    print(f"=== seo_growth_loop {today} mode={args.mode} sites={len(sites)} ===")

    for domain, niche in sites:
        print(f"\n── {domain} (niche={niche}) ──")
        if args.mode in ("authority", "all"):
            gen_authority(domain, niche, today, args.dry_run)
        if args.mode in ("review", "all"):
            gen_review(domain, niche, today, args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
