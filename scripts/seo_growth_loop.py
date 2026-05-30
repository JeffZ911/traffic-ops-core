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


def _authority_stats(domain):
    """PERCEIVED action — not guessed. Reads the operator's actual 'done'
    clicks on authority cards: how many in the last 14d, how many total
    ever, and how many weeks since the FIRST one (= how long the campaign
    has actually been running, which decides whether flat traffic is
    'still in the lag window' vs 'something is wrong')."""
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select
              count(*) filter (where completed_at >= now() - interval '14 days') recent,
              count(*) total,
              min(completed_at) first_done
            from ops_tasks
            where site_domain=%s and category='authority' and status='done'
            """,
            (domain,),
        )
        recent, total, first = cur.fetchone()
        weeks = 0.0
        if first:
            cur.execute("select extract(epoch from (now()-%s))/604800.0", (first,))
            weeks = float(cur.fetchone()[0])
        return {"recent": int(recent or 0), "total": int(total or 0),
                "weeks_running": round(weeks, 1)}


def _latest_index_ratio(domain):
    """Latest indexed-ratio sample (are the pages actually in the index
    yet?). Feeds the diagnosis: links can't help rank a page that still
    isn't indexed."""
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select (payload->'indexing_coverage'->>'ratio')::float
              from metrics_raw
             where site_id=(select id from sites where domain=%s)
               and source='gsc' and payload ? 'indexing_coverage'
             order by metric_date desc limit 1
            """,
            (domain,),
        )
        r = cur.fetchone()
        return float(r[0]) if r and r[0] is not None else None


def _diagnose_flat(domain, stats, idx_ratio):
    """The operator DID the work (stats.total>0) and enough time has passed
    (weeks_running >= 4) but traffic is still flat. Don't say 'keep
    waiting' — produce a ranked, specific diagnosis of WHY, each with a
    concrete check the operator (or AI) can run next."""
    weeks = stats["weeks_running"]
    lines = [
        f"⚠️ DIAGNOSIS — you've completed {stats['total']} authority task(s) "
        f"over {weeks:.0f} weeks and traffic is still flat. Past the 4-8wk "
        f"backlink-lag window, so this is NOT just lag. Likeliest causes, "
        f"ranked — work top-down:",
        "",
    ]
    # 1. Are pages even indexed? Links can't rank an unindexed page.
    if idx_ratio is None or idx_ratio < 0.05:
        lines.append(
            "1) PAGES STILL NOT INDEXED (most likely). Indexed ratio is "
            f"{'unknown' if idx_ratio is None else f'{idx_ratio:.0%}'}. If "
            "Google still won't index, the backlinks aren't being COUNTED "
            "yet — either too few, or from pages Google itself hasn't "
            "indexed (a link only passes authority once the linking page "
            "is indexed). CHECK: in GSC URL-inspect 2 pages you built links "
            "to — indexed? And are your backlink SOURCES (the Reddit/forum/"
            "directory pages) themselves indexed? An unindexed source = a "
            "link worth ~nothing."
        )
    else:
        lines.append(
            f"1) Pages ARE indexed ({idx_ratio:.0%}) but not ranking → the "
            "problem moved downstream to ranking, not indexing. Good — that's "
            "progress. Focus shifts to keyword difficulty (below)."
        )
    # 2. Link quality
    lines.append(
        "2) LINK QUALITY, not quantity. 5 forum/profile links (often "
        "nofollow) move nothing; 1 editorial dofollow link from a real site "
        "in-niche moves a lot. CHECK: of the links you built, how many are "
        "(a) dofollow and (b) on a topically-relevant, already-indexed page? "
        "If the honest answer is 0, that's the gap — prioritise the Outreach "
        "card (a real site mention) over more community comments."
    )
    # 3. Keyword difficulty
    lines.append(
        "3) KEYWORD DIFFICULTY too high for current authority. A 3-week-old "
        "domain cannot rank head terms owned by DA-70+ incumbents, no matter "
        "the content. CHECK: are your target keywords long-tail + low-"
        "competition (4-6 word buyer questions), or broad head terms? If "
        "broad, the content pool needs to pivot to ultra-specific long-tail "
        "first — those rank on thin authority and build the base."
    )
    # 4. Intent / SERP match
    lines.append(
        "4) SEARCH-INTENT MISMATCH. CHECK: Google one target keyword "
        "incognito — are page-1 results the SAME FORMAT as your article "
        "(listicle vs guide vs tool vs video)? If Google ranks calculators "
        "and you wrote prose, you can't win that SERP regardless of links."
    )
    # 5. Time/escalation
    if weeks >= 8:
        lines.append(
            "5) 8+ WEEKS, real effort, still flat → make a CALL: either the "
            "niche is too saturated for this domain's runway, or the link "
            "work isn't landing real editorial links. Consider a focused "
            "1-2 week digital-PR push (one genuinely linkable data asset + "
            "10 targeted outreach emails) as a decisive test before "
            "committing more months."
        )
    return "\n".join(lines)


def gen_review(domain, niche, today, dry_run):
    iso_year, iso_week, _ = today.isocalendar()
    wk = f"{iso_year}-W{iso_week:02d}"
    t = _trend(domain, 14)
    if t is None:
        return
    stats = _authority_stats(domain)
    idx_ratio = _latest_index_ratio(domain)

    def arrow(recent, prior):
        if recent > prior: return f"↑ {prior}→{recent}"
        if recent < prior: return f"↓ {prior}→{recent}"
        return f"→ flat at {recent}"

    impr = arrow(t["impr_recent"], t["impr_prior"])
    sess = arrow(t["sess_recent"], t["sess_prior"])
    moved_up = t["impr_recent"] > t["impr_prior"] or t["sess_recent"] > t["sess_prior"]
    flat = (t["impr_recent"] == t["impr_prior"] and t["sess_recent"] == t["sess_prior"])

    # Branch on PERCEIVED effort × elapsed time × outcome. The 'done' count
    # is the operator's actual button-clicks (ground truth they DID it),
    # never inferred from traffic.
    if stats["total"] == 0:
        priority = "high"
        verdict = (
            "No authority tasks marked done yet. Nothing is pushing on the "
            "real bottleneck. Traffic CANNOT move from content alone here — "
            "do this week's Community + Outreach cards. (This isn't a guess "
            "from traffic; it's that the off-page work simply hasn't started.)"
        )
    elif moved_up:
        priority = "normal"
        verdict = (
            f"WORKING. You've done {stats['total']} authority task(s) over "
            f"{stats['weeks_running']:.0f} weeks and the funnel moved: "
            f"impressions {impr}, sessions {sess}. The off-page work is "
            "landing — double down on whichever route you actually did."
        )
    elif stats["weeks_running"] < 4:
        priority = "normal"
        verdict = (
            f"You've done {stats['total']} authority task(s), campaign is "
            f"{stats['weeks_running']:.0f} weeks old. Traffic flat — but "
            "backlink→ranking lag is 4-8 weeks, so this is EXPECTED this "
            "early, not a failure. Keep the weekly cadence; the real read "
            "is at week 4-6."
        )
    else:
        # Did real work, enough time elapsed, STILL flat → DIAGNOSE WHY.
        priority = "high"
        verdict = _diagnose_flat(domain, stats, idx_ratio)

    title = f"[Funnel review] {domain} — {wk}"
    detail = (
        f"2-WEEK FUNNEL MOVEMENT ({domain}):\n"
        f"  GSC impressions: {impr}   clicks(recent): {t['clk_recent']}\n"
        f"  GA4 sessions:    {sess}\n"
        f"  Index ratio:     {'unknown' if idx_ratio is None else f'{idx_ratio:.0%}'}\n"
        f"  Authority tasks DONE — last 14d: {stats['recent']}, "
        f"total: {stats['total']}, campaign age: {stats['weeks_running']:.0f}wk\n\n"
        f"{verdict}\n\n"
        f"(Action counts above are your actual 'done' clicks — perceived, "
        f"not inferred from traffic.)"
    )
    if dry_run:
        print(f"  [DRY] would upsert: {title}  [{priority}]")
        print("       " + verdict.split(chr(10))[0])
    else:
        res = upsert_open_task(title, detail, priority=priority,
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
