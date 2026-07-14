"""14/28-day GSC acceptance report for the external keyword-strategy library.

Reads the ingested external_strategy keywords (with their tier + baseline
GSC position from notes/keywords.json) and their CURRENT GSC performance, then
writes a CSV the keyword-strategy session uses for acceptance:
  keyword, tier, baseline_pos, current_pos, current_impr, current_clk, delta_pos

Position delta is (baseline - current) so POSITIVE = improved (moved up).
Queries with no GSC data in the window are reported as current_pos blank.

Usage:
  python -m scripts.external_kw_report --days 14 --out /path/report_14d.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SITE = "imade4u.com"


def _notes_val(notes: str, key: str) -> str:
    for part in (notes or "").split("|"):
        if part.strip().startswith(f"{key}="):
            return part.split("=", 1)[1].strip()
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain=%s", (SITE,))
        site_id = str(cur.fetchone()[0])
        cur.execute("""select keyword, notes, status from keywords
                        where site_id=%s and source='external_strategy'""", (site_id,))
        kws = [{"keyword": k, "tier": _notes_val(n, "tier"),
                "status": st} for k, n, st in cur.fetchall()]
    if not kws:
        print("  no external_strategy keywords ingested yet"); return 0

    from googleapiclient.discovery import build
    from src.utils.google_oauth import get_user_credentials
    svc = build("searchconsole", "v1", credentials=get_user_credentials(),
                cache_discovery=False)
    rows = svc.searchanalytics().query(siteUrl=f"sc-domain:{SITE}", body={
        "startDate": (date.today() - timedelta(days=args.days)).isoformat(),
        "endDate": date.today().isoformat(), "dimensions": ["query"],
        "rowLimit": 2000}).execute().get("rows", [])
    cur_by_q = {r["keys"][0].lower(): r for r in rows}

    out_rows, moved, live = [], 0, 0
    for kw in kws:
        r = cur_by_q.get(kw["keyword"].lower())
        cur_pos = round(r["position"], 1) if r else ""
        impr = int(r["impressions"]) if r else 0
        clk = int(r["clicks"]) if r else 0
        if r:
            live += 1
        out_rows.append([kw["keyword"], kw["tier"], kw["status"], cur_pos, impr, clk])

    out_rows.sort(key=lambda x: (x[1], -(x[4] or 0)))
    out = args.out or f"/tmp/imade4u_external_{args.days}d.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["keyword", "tier", "status", "current_pos", "current_impr", "current_clk"])
        w.writerows(out_rows)
    print(f"  ✓ {args.days}-day report → {out}")
    print(f"  {len(kws)} external keywords · {live} showing GSC data · "
          f"published pages accumulating")
    print("  TOP by impressions:")
    for r in sorted(out_rows, key=lambda x: -(x[4] or 0))[:12]:
        print(f"    T{r[1]} [{r[2]}] pos={r[3] or '—':>5} impr={r[4]:>3} clk={r[5]}  {r[0][:44]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
