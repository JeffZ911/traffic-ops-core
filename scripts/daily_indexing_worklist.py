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
import urllib.request
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection
from src.utils.ops_tasks import upsert_open_task, resolve_open_task

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DEFAULT_SITES = ("ntecodex.com", "pixelmatch.art", "quvii.com", "imade4u.com")
CAP_PER_SITE = 8   # GSC allows ~10-12/day/property; 8 = aggressive indexing push.
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


def _live_url(base: str, timeout: float = 8.0) -> str | None:
    """Return the form of `base` that serves 200 DIRECTLY (no redirect), or None
    if dead. Astro needs a trailing slash (no-slash 308s); Shopify needs none.
    GSC can't index a URL that 404s or only redirects, so we never list one."""
    base = base.rstrip("/")
    for cand in (base + "/", base):
        try:
            req = urllib.request.Request(
                cand, method="HEAD",
                headers={"User-Agent": "Mozilla/5.0 (indexing-worklist)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status == 200 and r.geturl() == cand:
                    return cand
        except Exception:
            continue
    return None


def _rotate(urls: list[str], day_ordinal: int, cap: int) -> list[str]:
    """Full list rotated by the day so each run starts at a different offset
    (so the operator cycles through the whole backlog over time)."""
    n = len(urls)
    if n == 0:
        return []
    start = (day_ordinal * cap) % n
    return urls[start:] + urls[:start]


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

    # Walk the rotated backlog, keeping only URLs that are actually LIVE (200) —
    # skips stale DB rows that were never deployed + redirect-only forms.
    picks, checked, dead = [], 0, 0
    for base in _rotate(candidates, day_ordinal, cap):
        if len(picks) >= cap or checked >= cap * 6:
            break
        checked += 1
        live = _live_url(base)
        if live:
            picks.append(live)
        else:
            dead += 1
    if not picks:
        resolve_open_task(f"每日 Request indexing — {domain}", site_domain=domain)
        return f"{domain}: no LIVE URLs in {checked} checked — card cleared"
    numbered = "\n".join(f"  {i+1}. {u}" for i, u in enumerate(picks))
    detail = (
        f"{len(picks)} 个 URL → 粘进 GSC 请求收录（每天轮换一批，做完标记 Done）。\n"
        "操作：点每条右边的 📋 复制 或 'GSC ↗' 直接打开对应站点的 URL Inspection → "
        "Request Indexing。GSC 每站每天约 10-12 个配额，这几个 2 分钟搞定。\n"
        "（已自动校验：只列实时返回 200 的页面，跳过 404/重定向）\n\n"
        f"URLs（编辑精华 + 最新优先，均已验活）:\n{numbered}"
    )
    res = upsert_open_task(
        f"每日 Request indexing — {domain}",
        detail,
        priority="high", category="seo-reqidx", site_domain=domain,
    )
    return f"{domain}: {res} ({len(picks)} live / {checked} checked, {dead} dead skipped)"


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
