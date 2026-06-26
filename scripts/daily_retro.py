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


_PROMPT = """You are the head of SEO for a portfolio of 4 young, low-authority
content sites. TODAY IS {today}. Below is today's snapshot of every site:
volume (articles published in 7 days), quality (avg QA 0-10), Google
impressions this settled week vs the prior week, click count, index-coverage
split (indexed / discovered-not-indexed / unknown-to-Google), and the single
top impression page.

PORTFOLIO DATA (JSON):
{data}

Context you must apply: these sites mass-produce high-QA content, but the
binding constraint is crawl/indexing + ranking authority, NOT content volume or
quality. Impressions lag indexing; clicks lag rankings; GSC settles ~3 days
late so the most recent 1-2 days read low.

Write a portfolio retrospective. Be concrete, name sites, cite the numbers, and
do NOT pad with caveats about data being early. Return ONLY JSON (no fence):
{{
  "health": "<1 sentence: overall portfolio trajectory>",
  "retrospective": "<4-7 sentences: which sites are progressing and which are stalled, what the numbers say is working vs not, cross-site patterns. Reference real figures.>",
  "top_actions": ["<action 1>", "<action 2>", "<action 3>"]
}}
"""


def _ai_retro(records: list[dict], model: str) -> dict:
    from src.utils.llm import get_llm_provider
    provider = get_llm_provider("gemini")
    prompt = _PROMPT.format(today=date.today().isoformat(),
                            data=json.dumps(records, ensure_ascii=False, indent=1))
    resp = provider.generate(prompt=prompt, model=model, max_tokens=3000,
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-3.1-pro-preview")
    ap.add_argument("--no-ai", action="store_true")
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
        cur.execute("select id from sites where domain=%s", (SITES[0],))
        host = cur.fetchone()
        host_id = str(host[0]) if host else None

    print(f"📊 Portfolio daily retrospective — {date.today()}")
    for r in records:
        cov = r.get("index_coverage") or {}
        print(f"  {r['site']:<16} pub7d={r.get('published_7d')} qa={r.get('avg_qa')} "
              f"impr {r.get('impressions_prev_week')}→{r.get('impressions_this_week')} "
              f"clk={r.get('clicks_this_week')} idx={cov.get('pct_indexed','?')}%")

    retro = {}
    if not args.no_ai:
        try:
            retro = _ai_retro(records, args.model)
            print(f"\n🤖 {retro.get('health','')}")
            print(f"   {retro.get('retrospective','')}")
            for i, a in enumerate(retro.get("top_actions", []), 1):
                print(f"   {i}. {a}")
            print(f"   (model={args.model}, cost ${retro.get('_cost',0):.4f})")
        except Exception as e:  # noqa: BLE001 — never let the AI step break the log
            print(f"  ⚠️  AI retro skipped: {type(e).__name__}: {str(e)[:120]}")

    if host_id:
        store_raw(host_id, "gsc", date.today(), {"daily_retro": {
            "date": date.today().isoformat(),
            "health": retro.get("health", ""),
            "retrospective": retro.get("retrospective", ""),
            "top_actions": retro.get("top_actions", []),
            "stats": records, "model": args.model,
        }})
        print("\n  ✓ retrospective logged (metrics_raw 'daily_retro')")
    return 0


if __name__ == "__main__":
    sys.exit(main())
