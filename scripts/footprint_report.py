"""Footprint-expansion acceptance report (quvii / security_cameras).

Resolves each cohort-tagged expansion keyword (source='expansion', notes carry
`cohort=A|B|C`) to its REAL published article via the article_keywords link
table (authoritative — NOT slug reconstruction, since slugs are LLM-generated
and don't equal a slugified keyword), pulls that page's GSC performance, and
reports QUERY-FOOTPRINT YIELD per cohort so we can see which axis of expansion
earns fresh impressions:

  A = brand expansion   B = problem expansion   C = intent/decision expansion
  D = CONTROL (published pages NOT linked to an expansion keyword)

Per cohort: keywords planned, pages published, pages earning >=1 impression,
coverage (earning/published), total impressions, distinct GSC queries, and
impr-per-PUBLISHED-page (the age-fair headline yield — divides by published,
not by earning, so a cohort isn't flattered by survivorship).

READ-ONLY. Usage:
  python -m scripts.footprint_report --days 14
  python -m scripts.footprint_report --days 14 --out /path/report.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from datetime import date, timedelta

from src.db.client import get_db_connection
from src.utils.google_oauth import get_user_credentials

SITE = "quvii.com"


def _cohort(notes: str) -> str:
    """Extract the cohort tag (A/B/C) from an expansion keyword's notes."""
    m = re.search(r"cohort=([ABC])", notes or "")
    return m.group(1) if m else "?"


def _norm_url(u: str) -> str:
    """Normalise a page URL for matching GSC page keys to article URLs:
    lowercase, drop scheme, strip a trailing slash."""
    if not u:
        return ""
    s = str(u).strip().lower()
    s = re.sub(r"^https?://", "", s)
    return s.rstrip("/")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    # ---- 1. DB: resolve expansion keywords -> real published articles --------
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain=%s", (SITE,))
        row = cur.fetchone()
        if not row:
            print(f"  site {SITE} not found"); return 2
        site_id = str(row[0])

        # expansion keywords with their cohort + (if written) the article they
        # produced, via the article_keywords link table. LEFT JOIN so we also
        # see keywords not yet published.
        cur.execute(
            """
            select k.keyword, k.notes, k.status,
                   a.published_url, a.status
              from keywords k
              left join article_keywords ak on ak.keyword_id = k.id
              left join articles a on a.id = ak.article_id
             where k.site_id = %s and k.source = 'expansion'
            """, (site_id,))
        exp = cur.fetchall()

        # total published articles on the site (to derive control-cohort size)
        cur.execute(
            "select count(*) from articles where site_id=%s and status='published'",
            (site_id,))
        total_published = int(cur.fetchone()[0])

    if not exp:
        print("  no expansion keywords ingested yet — run "
              "`keyword_gardener --expansion` first"); return 0

    # cohort per keyword; published-URL -> cohort; per-cohort published counts
    cohort_by_url: dict[str, str] = {}
    kw_planned = defaultdict(int)
    pages_published = defaultdict(int)
    seen_kw = set()
    for kw, notes, kstatus, purl, astatus in exp:
        c = _cohort(notes)
        if kw not in seen_kw:
            kw_planned[c] += 1
            seen_kw.add(kw)
        if purl and astatus == "published":
            key = _norm_url(purl)
            if key and key not in cohort_by_url:
                cohort_by_url[key] = c
                pages_published[c] += 1
    exp_published_total = sum(pages_published.values())

    # ---- 2. GSC: per-page (impr/clk/pos) + per page->query distinct ----------
    svc = _gsc()
    end = date.today()
    start = end - timedelta(days=args.days)
    page_rows = svc.searchanalytics().query(siteUrl=f"sc-domain:{SITE}", body={
        "startDate": start.isoformat(), "endDate": end.isoformat(),
        "dimensions": ["page"], "rowLimit": 5000}).execute().get("rows", [])
    pq_rows = svc.searchanalytics().query(siteUrl=f"sc-domain:{SITE}", body={
        "startDate": start.isoformat(), "endDate": end.isoformat(),
        "dimensions": ["page", "query"], "rowLimit": 25000}).execute().get("rows", [])

    queries_by_url: dict[str, set] = defaultdict(set)
    for r in pq_rows:
        if int(r["impressions"]) > 0:
            queries_by_url[_norm_url(r["keys"][0])].add(r["keys"][1])

    # ---- 3. bucket every EARNING page into A/B/C (expansion) or D (control) ---
    agg = {c: {"earning": 0, "impr": 0, "clk": 0, "queries": set(),
               "pos_sum": 0.0, "pos_n": 0} for c in ("A", "B", "C", "D")}
    for r in page_rows:
        url = _norm_url(r["keys"][0])
        c = cohort_by_url.get(url, "D")   # not an expansion page => control
        a = agg[c]
        if int(r["impressions"]) > 0:
            a["earning"] += 1
        a["impr"] += int(r["impressions"])
        a["clk"] += int(r["clicks"])
        a["queries"] |= queries_by_url.get(url, set())
        a["pos_sum"] += r["position"] * r["impressions"]
        a["pos_n"] += int(r["impressions"])

    # published-page denominators: A/B/C from the join; D = everything else
    pages_published["D"] = max(total_published - exp_published_total, 0)

    # ---- 4. print + CSV ------------------------------------------------------
    # A = brand × exact failure, B = integration/API failure, C = CVE/security
    # advisory (reweighted 2026-07-16 to the QDF-proven winners), D = control.
    labels = {"A": "A brand-fix", "B": "B integ", "C": "C cve/sec", "D": "D control"}
    print(f"\n# footprint expansion — {args.days}d GSC ({start}..{end}), site={SITE}")
    print(f"# {len(seen_kw)} expansion keywords · {exp_published_total} of them "
          f"published · {total_published} total published pages on site\n")
    print(f"{'cohort':>10} {'kw_plan':>7} {'pub':>4} {'earn':>4} {'cover':>6} "
          f"{'impr':>7} {'clk':>4} {'queries':>7} {'impr/pub':>8} {'q/pub':>6} {'wpos':>6}")
    out_rows = [["cohort", "kw_planned", "pages_published", "pages_earning",
                 "coverage", "impressions", "clicks", "distinct_queries",
                 "impr_per_published_page", "queries_per_published_page", "wpos"]]
    for c in ("A", "B", "C", "D"):
        a = agg[c]
        pub = pages_published.get(c, 0)
        cover = (a["earning"] / pub) if pub else 0.0
        ipp = (a["impr"] / pub) if pub else 0.0
        qpp = (len(a["queries"]) / pub) if pub else 0.0
        wpos = (a["pos_sum"] / a["pos_n"]) if a["pos_n"] else 0.0
        nq = len(a["queries"])
        print(f"{labels[c]:>10} {kw_planned.get(c,0):>7} {pub:>4} {a['earning']:>4} "
              f"{cover:>6.0%} {a['impr']:>7} {a['clk']:>4} {nq:>7} {ipp:>8.1f} "
              f"{qpp:>6.2f} {wpos:>6.1f}")
        out_rows.append([labels[c], kw_planned.get(c, 0), pub, a["earning"],
                         round(cover, 3), a["impr"], a["clk"], nq,
                         round(ipp, 1), round(qpp, 2), round(wpos, 1)])

    print("\n# headline — impressions per PUBLISHED page (age-fair; higher = "
          "the expansion axis pulling its weight vs control D):")
    for c in ("A", "B", "C", "D"):
        pub = pages_published.get(c, 0)
        ipp = (agg[c]["impr"] / pub) if pub else 0.0
        print(f"   {labels[c]}: {ipp:.1f} impr/published-page ({pub} published)")
    print("  (D is older/accumulated — expect A/B/C to trail early and close "
          "the gap as their pages age past the ~14d QDF ramp.)")

    if args.out:
        with open(args.out, "w", newline="") as f:
            csv.writer(f).writerows(out_rows)
        print(f"\n  wrote {args.out}")
    return 0


def _gsc():
    from googleapiclient.discovery import build
    return build("searchconsole", "v1", credentials=get_user_credentials(),
                 cache_discovery=False)


if __name__ == "__main__":
    sys.exit(main())
