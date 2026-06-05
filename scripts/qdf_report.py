"""QDF P3 — next-day retrospective for fresh trend pages.

Manual §5 (次日复盘): every day, check each recent QDF/trend page's index
status + search performance, so the keyword strategy can learn (which trend
angles got crawled/indexed/impressions = repeat; which got nothing = refine).

For each trend page published in the last N days:
  - GSC urlInspection → coverageState (unknown / discovered / crawled / indexed)
    + last crawl time. (READ-ONLY, fully compliant.)
  - GSC searchanalytics dimensions=['page'] → impressions / clicks / position.
Classify:
  WIN     indexed + impressions > 0
  PENDING crawled or discovered, no impressions yet
  COLD    still unknown to Google (not crawled) after the QDF window
A rolling /todos card per site shows the digest; COLD pages older than 3 days
are the signal to refine keyword selection (or that crawl trust is the blocker).

Usage:
  python -m scripts.qdf_report --site quvii.com
  python -m scripts.qdf_report --site quvii.com --days 7
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection
from src.utils.ops_tasks import upsert_open_task

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_INDEXED = {"Submitted and indexed", "Indexed, not submitted in sitemap"}


def _fresh_trend_pages(site_id: str, days: int) -> list[tuple[str, str, int]]:
    """(public_url, keyword, age_days) for trend pages published in last N days."""
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select distinct a.published_url, k.keyword,
                   floor(extract(epoch from (now() - a.published_at)) / 86400.0)::int
              from articles a
              join article_keywords ak on ak.article_id = a.id
              join keywords k on k.id = ak.keyword_id
             where a.site_id = %s and a.status = 'published'
               and a.published_url is not null and k.source = 'trend'
               and a.published_at >= now() - %s * interval '1 day'
             order by 3 asc
            """,
            (site_id, days),
        )
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--days", type=int, default=4)
    args = ap.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain=%s", (args.site,))
        row = cur.fetchone()
        if not row:
            print(f"❌ site {args.site!r} not in sites"); return 2
        site_id = str(row[0])

    pages = _fresh_trend_pages(site_id, args.days)
    if not pages:
        print(f"  no fresh trend pages (last {args.days}d) on {args.site}")
        return 0

    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from src.utils.google_oauth import get_user_credentials

    prop = f"sc-domain:{args.site}"
    svc = build("searchconsole", "v1", credentials=get_user_credentials(),
                cache_discovery=False)

    # Page-level performance (last 7d) → {full_url: (impr, clicks, pos)}
    perf: dict[str, tuple[int, int, float]] = {}
    try:
        body = {
            "startDate": (date.today() - timedelta(days=7)).isoformat(),
            "endDate": date.today().isoformat(),
            "dimensions": ["page"], "rowLimit": 1000,
        }
        resp = svc.searchanalytics().query(siteUrl=prop, body=body).execute()
        for r in resp.get("rows", []):
            perf[r["keys"][0].rstrip("/")] = (
                int(r.get("impressions", 0)), int(r.get("clicks", 0)),
                float(r.get("position", 0.0)),
            )
    except HttpError as e:
        print(f"  ⚠️  page-perf query failed: {e.resp.status}")

    wins = pending = cold = 0
    lines: list[str] = []
    cold_kws: list[str] = []
    for url, kw, age in pages:
        full = f"https://{args.site}{url}" if not url.startswith("http") else url
        state = "?"
        try:
            ins = svc.urlInspection().index().inspect(
                body={"inspectionUrl": full, "siteUrl": prop}
            ).execute()
            state = (ins.get("inspectionResult", {})
                        .get("indexStatusResult", {}).get("coverageState", "?"))
        except HttpError as e:
            state = f"inspect {e.resp.status}"

        impr, clk, pos = perf.get(full.rstrip("/"), (0, 0, 0.0))
        indexed = state in _INDEXED
        if indexed and impr > 0:
            tag = "WIN"; wins += 1
        elif indexed or "rawl" in state or "iscover" in state:
            tag = "PENDING"; pending += 1
        else:
            tag = "COLD"; cold += 1
            if age >= 3:
                cold_kws.append(kw)
        lines.append(
            f"  [{tag}] d{age} impr={impr} clk={clk} pos={pos:.0f} "
            f"{state[:34]} — {url}"
        )

    digest = "\n".join(lines)
    print(f"📊 QDF retrospective — {args.site} ({len(pages)} fresh trend page(s))")
    print(digest)
    print(f"  → WIN {wins} · PENDING {pending} · COLD {cold}")

    body_md = (
        f"昨日 QDF 热点页表现({len(pages)} 个,近 {args.days} 天):"
        f"WIN {wins} · PENDING {pending} · COLD {cold}\n"
        "WIN=已收录+有曝光 · PENDING=已爬/已发现待收 · COLD=谷歌还没爬到\n\n"
        f"{digest}\n\n"
    )
    if cold_kws:
        body_md += (
            "⚠️ 发了 3 天+仍没被爬到的词(迭代信号 —— 要么换更有热度的角度,"
            "要么是整站爬取信任不足,需配合收录基建):\n"
            + "\n".join(f"  · {k}" for k in cold_kws) + "\n\n"
        )
    body_md += ("注:谷歌无即时推送,新页被爬取受整站信任度限制;COLD 多属正常,"
                "随 request-indexing + 外链积累会转 PENDING→WIN。")

    upsert_open_task(
        f"QDF 次日复盘 — {args.site}",
        body_md,
        priority="low", category="qdf-report", site_domain=args.site,
    )
    print(f"  ✓ /todos card updated (QDF 次日复盘 — {args.site})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
