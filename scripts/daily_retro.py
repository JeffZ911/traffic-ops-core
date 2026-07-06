"""Daily PORTFOLIO retrospective — one AI assessment across ALL sites.

Jeff's directive (2026-06-23): stop the per-site scatter; evaluate every site
together once a day and leave a readable retrospective LOG. This is the
human-facing strategic read on the whole operation — distinct from the per-site
qdf_report, which stays as the machine self-improvement loop that feeds each
site's keyword generation.

What it does:
  1. Gather, per site: articles published (7d), avg QA, impressions this settled
     week vs the prior week, latest index-coverage split, and the single top
     impression page.
  2. Feed the whole portfolio snapshot to ONE Gemini call for a holistic
     retrospective: what's working, what isn't, cross-site patterns, and the
     3 highest-leverage actions for tomorrow.
  3. Append a dated entry to the retrospective LOG (metrics_raw payload
     'daily_retro'; the dashboard renders the accumulating history).

No schema change — reuses the append-only metrics_raw store, hosted under one
site row but written site-agnostically (the dashboard reads it globally).

Usage:
  python -m scripts.daily_retro
  python -m scripts.daily_retro --no-ai   # data-only log entry
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from src.collectors.base import store_raw
from src.db.client import get_db_connection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SITES = ("ntecodex.com", "quvii.com", "pixelmatch.art", "imade4u.com")
_INDEXED = {"Submitted and indexed", "Indexed, not submitted in sitemap"}


def _site_stats(cur, svc, domain: str) -> dict:
    """One site's snapshot: volume, quality, impressions WoW, coverage, top page."""
    cur.execute("select id from sites where domain=%s", (domain,))
    row = cur.fetchone()
    if not row:
        return {"site": domain, "error": "no site row"}
    site_id = str(row[0])

    cur.execute(
        """select count(*), round(avg(qa_score)::numeric, 1)
             from articles where site_id=%s and status='published'
              and published_at >= now() - interval '7 days'""",
        (site_id,),
    )
    n7, avg_qa = cur.fetchone()

    # latest index-coverage snapshot the monitor recorded
    cur.execute(
        """select payload->'index_coverage' from metrics_raw
            where site_id=%s and payload ? 'index_coverage'
            order by metric_date desc limit 1""",
        (site_id,),
    )
    cov_row = cur.fetchone()
    cov = cov_row[0] if cov_row else None

    prop = f"sc-domain:{domain}"
    t = date.today()
    tw = svc and _imp(svc, prop, (t - timedelta(days=9)).isoformat(), (t - timedelta(days=3)).isoformat())
    pw = svc and _imp(svc, prop, (t - timedelta(days=16)).isoformat(), (t - timedelta(days=10)).isoformat())
    top_page = None
    if svc:
        try:
            r = svc.searchanalytics().query(siteUrl=prop, body={
                "startDate": (t - timedelta(days=9)).isoformat(),
                "endDate": (t - timedelta(days=3)).isoformat(),
                "dimensions": ["page"], "rowLimit": 1}).execute().get("rows", [])
            if r:
                top_page = {"url": r[0]["keys"][0], "impr": int(r[0]["impressions"]),
                            "pos": round(r[0]["position"], 1)}
        except Exception:
            pass
    return {
        "site": domain, "published_7d": n7 or 0, "avg_qa": float(avg_qa) if avg_qa else None,
        "impressions_this_week": (tw or (0, 0))[0], "clicks_this_week": (tw or (0, 0))[1],
        "impressions_prev_week": (pw or (0, 0))[0],
        "index_coverage": cov, "top_page": top_page,
    }


def _imp(svc, prop, s, e):
    try:
        r = svc.searchanalytics().query(siteUrl=prop, body={
            "startDate": s, "endDate": e, "dimensions": []}).execute().get("rows", [])
        return (int(r[0]["impressions"]), int(r[0]["clicks"])) if r else (0, 0)
    except Exception:
        return (0, 0)


_PROMPT = """You are the autonomous optimization DIRECTOR for a portfolio of 4
young, low-authority SEO sites. TODAY IS {today}. Your job: judge what to
optimize next to raise impressions→clicks, and — critically — LEARN from whether
your OWN past directives actually moved the numbers (self-evolution).

PORTFOLIO DATA (JSON) — per site: 7-day publish volume, avg QA, impressions this
settled week vs prior week, clicks, index-coverage split, top page:
{data}

HOW YOUR PAST DIRECTIVES PERFORMED (your self-evolution scorecard — the site's
impressions when you issued the directive vs now):
{scorecard}

Context: these sites mass-produce high-QA content; the binding constraint is
crawl/indexing + ranking AUTHORITY, not volume/quality. Impressions lag
indexing; clicks lag rankings; GSC settles ~3 days late (last 1-2 days read low).

Levers you may direct (tag each directive with exactly one):
- keyword_guidance : what angles/attributes the keyword generators should favor
    or avoid next (this lever AUTO-EXECUTES — it is injected into generation).
- internal_links / content_structure : how articles should link/structure.
- product_seo : rewrite a store product/collection page's SEO (imade4u only).
- affiliate_placement : where/how to place monetization links.
- indexing_push : crawl/indexing tactics.
- backlinks : off-site authority (a human to-do).
- other.

Return ONLY JSON (no fence):
{{
  "health": "<1 sentence: portfolio trajectory>",
  "self_evaluation": "<2-4 sentences grounded in the SCORECARD: which of my past levers moved impressions and which didn't, and what I am therefore changing. If no scorecard yet, say so.>",
  "retrospective": "<3-5 sentences: what's working vs stalled per site, cross-site patterns, real figures.>",
  "directives": [
    {{"site": "<domain or 'portfolio'>",
      "lever": "<one lever from the list>",
      "action": "<concrete, specific instruction — not a platitude>",
      "rationale": "<why, citing a number>",
      "target_metric": "<the metric this should move, e.g. 'quvii impressions'>",
      "confidence": <integer 0-100>}}
  ]
}}
Give 4-7 directives, highest-leverage first. Make keyword_guidance directives
per-SITE and actionable (they are fed verbatim to that site's generator).
"""


def _ai_retro(records: list[dict], scorecard: str, model: str) -> dict:
    from src.utils.llm import get_llm_provider
    provider = get_llm_provider("gemini")
    prompt = _PROMPT.format(today=date.today().isoformat(), scorecard=scorecard or "  (no prior directives yet — first run)",
                            data=json.dumps(records, ensure_ascii=False, indent=1))
    resp = provider.generate(prompt=prompt, model=model, max_tokens=4000,
                             temperature=0.3, json_mode=True)
    text = (resp.text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        obj = json.loads(text)
    except Exception:
        i, j = text.find("{"), text.rfind("}")
        obj = json.loads(text[i:j + 1]) if i >= 0 and j > i else {}
    obj["_cost"] = getattr(resp, "cost_usd", 0)
    return obj


def _scorecard(cur, records: list[dict], lookback_days: int = 8) -> str:
    """Self-evolution feedback: pull the director's directives from ~3-8 days ago
    and grade each by whether its site's weekly impressions rose since. This is
    what makes the loop LEARN instead of repeating advice that never worked."""
    cur.execute(
        """select metric_date, payload->'daily_retro'
             from metrics_raw
            where payload ? 'daily_retro'
              and metric_date <= current_date - 3
              and metric_date >= current_date - %s
            order by metric_date desc limit 3""",
        (lookback_days,))
    now_impr = {r["site"]: r.get("impressions_this_week", 0) for r in records}
    lines = []
    for mdate, dr in cur.fetchall():
        if not dr:
            continue
        at_issue = {s.get("site"): s.get("impressions_this_week", 0)
                    for s in (dr.get("stats") or [])}
        for d in (dr.get("directives") or [])[:6]:
            site = d.get("site", "")
            was, now = at_issue.get(site), now_impr.get(site)
            if was is None or now is None:
                moved = "n/a"
            elif now > was * 1.15:
                moved = f"UP {was}→{now}"
            elif now < was * 0.85:
                moved = f"DOWN {was}→{now}"
            else:
                moved = f"flat {was}→{now}"
            lines.append(f"  [{mdate} · {d.get('lever','?')} · {site}] {moved} — "
                         f"{(d.get('action') or '')[:80]}")
    return "\n".join(lines[:12])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-3.1-pro-preview")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--no-route", action="store_true",
                    help="don't auto-inject keyword_guidance directives into generation")
    args = ap.parse_args()

    svc = None
    if not args.no_ai:
        try:
            from googleapiclient.discovery import build
            from src.utils.google_oauth import get_user_credentials
            svc = build("searchconsole", "v1", credentials=get_user_credentials(),
                        cache_discovery=False)
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️  GSC unavailable ({type(e).__name__}) — coverage/DB only")

    with get_db_connection() as conn, conn.cursor() as cur:
        records = [_site_stats(cur, svc, d) for d in SITES]
        scorecard = _scorecard(cur, records)
        cur.execute("select domain, id from sites")
        site_ids = {d: str(i) for d, i in cur.fetchall()}
        host_id = site_ids.get(SITES[0])

    print(f"📊 Portfolio daily retrospective — {date.today()}")
    for r in records:
        cov = r.get("index_coverage") or {}
        print(f"  {r['site']:<16} pub7d={r.get('published_7d')} qa={r.get('avg_qa')} "
              f"impr {r.get('impressions_prev_week')}→{r.get('impressions_this_week')} "
              f"clk={r.get('clicks_this_week')} idx={cov.get('pct_indexed','?')}%")

    retro = {}
    directives = []
    if not args.no_ai:
        try:
            retro = _ai_retro(records, scorecard, args.model)
            directives = [d for d in (retro.get("directives") or []) if isinstance(d, dict)]
            print(f"\n🤖 {retro.get('health','')}")
            if retro.get("self_evaluation"):
                print(f"   🧬 自评: {retro['self_evaluation']}")
            print(f"   {retro.get('retrospective','')}")
            for d in directives:
                print(f"   • [{d.get('lever','?')}·{d.get('site','')}·conf{d.get('confidence','?')}] "
                      f"{d.get('action','')}")
            print(f"   (model={args.model}, cost ${retro.get('_cost',0):.4f})")
        except Exception as e:  # noqa: BLE001 — never let the AI step break the log
            print(f"  ⚠️  AI director skipped: {type(e).__name__}: {str(e)[:120]}")

    # ── AUTONOMOUS ARM (safe lever only): keyword_guidance directives auto-inject
    # into that site's generator via the director channel — the same gated path
    # the qdf loop already uses. Every OTHER lever (product_seo/affiliate/links/
    # backlinks) is LOGGED for human review, never auto-executed.
    routed = 0
    if directives and not args.no_route:
        from src.utils.qdf_memory import save_director_guidance
        by_site: dict[str, list[str]] = {}
        for d in directives:
            if d.get("lever") == "keyword_guidance" and d.get("site") in site_ids:
                by_site.setdefault(d["site"], []).append(d.get("action", ""))
        for domain, actions in by_site.items():
            txt = " ".join(a for a in actions if a).strip()
            if txt:
                save_director_guidance(site_ids[domain], txt, model=args.model)
                routed += 1
        if routed:
            print(f"\n  🤖 auto-routed keyword_guidance to {routed} site(s); "
                  f"{len(directives)-sum(1 for d in directives if d.get('lever')=='keyword_guidance')} "
                  f"other directive(s) logged for human review")

    if host_id:
        store_raw(host_id, "gsc", date.today(), {"daily_retro": {
            "date": date.today().isoformat(),
            "health": retro.get("health", ""),
            "self_evaluation": retro.get("self_evaluation", ""),
            "retrospective": retro.get("retrospective", ""),
            "directives": directives, "routed_keyword_sites": routed,
            "stats": records, "model": args.model,
        }})
        print("  ✓ directives logged (metrics_raw 'daily_retro')")
    return 0


if __name__ == "__main__":
    sys.exit(main())
