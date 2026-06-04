"""Build the daily 'GSC request-indexing' worklist cards → /todos.

One card PER SITE (category='seo-reqidx'), each listing a small rotating
batch of public URLs to hand-paste into GSC's URL-Inspection tool and click
'Request Indexing'. The dashboard renders these via UrlCopyList (per-URL
Copy + 'GSC ↗' deep-link that pre-fills the right sc-domain property), so the
operator just opens /todos and works the list across all 3 sites in one place.

Why no GSC API here (changed 2026-06-04):
  There is NO compliant API to *request* indexing, and the read-only
  urlInspection path was fragile (OAuth 403s) and — with every site near 0%
  indexed — pointless (every URL is "not indexed", so inspecting to filter
  adds nothing). Measurement lives in the bi-weekly indexing_census.py. This
  script is now a robust, dependency-free rotating worklist:
    - candidates ordered by editorial tier (clean → note → strong → rest),
      newest first within a tier;
    - a date-rotating window of CAP urls/site so the operator cycles through
      the backlog instead of re-requesting the same URLs every day.

Usage:
  python -m scripts.daily_indexing_worklist                 # all 3 sites
  python -m scripts.daily_indexing_worklist --sites quvii.com
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection
from src.utils.ops_tasks import upsert_open_task, resolve_open_task

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DEFAULT_SITES = ("ntecodex.com", "pixelmatch.art", "quvii.com")
CAP_PER_SITE = 4   # GSC allows ~10-12/day/property; 4 is a 1-min task, easy to finish.
TIER_RANK = {"clean": 0, "note": 1, "strong": 2}


def _public_url(domain: str, published_url: str) -> str:
    """Absolute public URL. published_url already carries the full path
    (incl. the '/blog' prefix for pixelmatch), so apex + path is correct
    for all three sites."""
    if published_url.startswith("http"):
        return published_url
    path = published_url if published_url.startswith("/") else f"/{published_url}"
    return f"https://{domain}{path}"


def _ordered_candidates(cur, site_id: str, domain: str) -> list[str]:
    cur.execute(
        """
        select published_url,
               coalesce(qa_feedback->>'editorial_tier','') as tier,
               published_at
          from articles
         where site_id=%s and status='published' and published_url is not null
        """,
        (site_id,),
    )
    rows = cur.fetchall()

    def rank(r):
        _u, tier, pub = r
        return (TIER_RANK.get(tier, 3), -(pub.timestamp() if pub else 0))

    rows.sort(key=rank)
    out, seen = [], set()
    for pu, _t, _p in rows:
        u = _public_url(domain, pu)
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _rotating_window(urls: list[str], cap: int, day_ordinal: int) -> list[str]:
    """Pick `cap` urls, rotating the start by the day so consecutive days
    surface a different slice and the operator cycles through the backlog."""
    n = len(urls)
    if n <= cap:
        return urls
    start = (day_ordinal * cap) % n
    return [urls[(start + i) % n] for i in range(cap)]


def build_site_card(cur, domain: str, cap: int, day_ordinal: int) -> str:
    cur.execute("select id from sites where domain=%s", (domain,))
    row = cur.fetchone()
    if not row:
        return f"skip (no site row): {domain}"
    site_id = str(row[0])
    candidates = _ordered_candidates(cur, site_id, domain)
    if not candidates:
        resolve_open_task(f"每日 Request indexing — {domain}", site_domain=domain)
        return f"{domain}: no published URLs — card cleared"

    picks = _rotating_window(candidates, cap, day_ordinal)
    numbered = "\n".join(f"  {i+1}. {u}" for i, u in enumerate(picks))
    detail = (
        f"{len(picks)} 个 URL → 粘进 GSC 请求收录（每天轮换一批，做完标记 Done）。\n"
        "操作：点每条右边的 📋 复制 或 'GSC ↗' 直接打开对应站点的 URL Inspection → "
        "Request Indexing。GSC 每站每天约 10-12 个配额，这 4 个 2 分钟搞定。\n\n"
        f"URLs（编辑精华 + 最新优先）:\n{numbered}"
    )
    res = upsert_open_task(
        f"每日 Request indexing — {domain}",
        detail,
        priority="high", category="seo-reqidx", site_domain=domain,
    )
    return f"{domain}: {res} ({len(picks)}/{len(candidates)} urls)"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sites", default=",".join(DEFAULT_SITES),
                   help="comma-separated domains (default: all 3)")
    p.add_argument("--cap", type=int, default=CAP_PER_SITE)
    args = p.parse_args()

    sites = [s.strip() for s in args.sites.split(",") if s.strip()]
    day_ordinal = date.today().toordinal()

    with get_db_connection() as conn, conn.cursor() as cur:
        for domain in sites:
            print("  ✓", build_site_card(cur, domain, args.cap, day_ordinal))
    return 0


if __name__ == "__main__":
    sys.exit(main())
