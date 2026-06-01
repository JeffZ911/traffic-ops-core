"""Indexing census — measure the real GSC coverageState distribution per site.

Answers the only question that matters for a young content site: of the URLs
Google has *discovered*, how many does it actually *index*? Samples each site's
LIVE sitemap (the exact URLs Google sees), runs read-only `urlInspection`, and
tallies coverageState. Writes a timestamped JSON snapshot so a later run can
quantify the lift from Request-Indexing + link-building.

Per-site sitemap handling (CRITICAL — get the path right or you measure noise):
  - ntecodex.com    → https://ntecodex.com/sitemap.xml          (apex)
  - quvii.com       → https://quvii.com/sitemap.xml             (apex)
  - pixelmatch.art  → https://pixelmatch.art/blog/sitemap.xml   (blog lives at
                       /blog subpath; the apex root is a separate SPA app)

Usage:
  python -m scripts.indexing_census                 # all sites, 40/site
  python -m scripts.indexing_census --per-site 30
  python -m scripts.indexing_census --baseline      # also write the baseline snapshot
  python -m scripts.indexing_census --compare data/indexing_baseline.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.utils.google_oauth import get_user_credentials  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

# (gsc_property, sitemap_url, min_slash_depth_for_article)
SITES = {
    "ntecodex.com":   ("sc-domain:ntecodex.com",   "https://ntecodex.com/sitemap.xml",        4),
    "pixelmatch.art": ("sc-domain:pixelmatch.art", "https://pixelmatch.art/blog/sitemap.xml", 5),
    "quvii.com":      ("sc-domain:quvii.com",      "https://quvii.com/sitemap.xml",           4),
}
_UA = {"User-Agent": "Mozilla/5.0 (indexing-census)"}
DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _sitemap_urls(sitemap_url: str) -> list[str]:
    try:
        req = urllib.request.Request(sitemap_url, headers=_UA)
        raw = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
        return re.findall(r"<loc>([^<]+)</loc>", raw)
    except Exception as e:  # noqa: BLE001
        print(f"  ! could not fetch sitemap {sitemap_url}: {e}")
        return []


def census(svc, per_site: int) -> dict:
    out: dict = {}
    for site, (prop, sm_url, depth) in SITES.items():
        locs = _sitemap_urls(sm_url)
        articles = [u for u in locs if u.rstrip("/").count("/") >= depth]
        # Deterministic sample (sorted, strided) — no RNG so reruns are stable.
        articles_sorted = sorted(articles)
        if per_site and len(articles_sorted) > per_site:
            step = len(articles_sorted) / per_site
            sample = [articles_sorted[int(i * step)] for i in range(per_site)]
        else:
            sample = articles_sorted
        states: Counter = Counter()
        for u in sample:
            try:
                r = svc.urlInspection().index().inspect(
                    body={"inspectionUrl": u, "siteUrl": prop}
                ).execute()
                cs = r.get("inspectionResult", {}).get(
                    "indexStatusResult", {}).get("coverageState", "?")
                states[cs] += 1
            except Exception as e:  # noqa: BLE001
                states[f"ERR:{str(e)[:40]}"] += 1
            time.sleep(0.4)
        tot = sum(v for k, v in states.items() if not k.startswith("ERR"))
        indexed = sum(v for k, v in states.items() if "indexed" in k.lower()
                      and "not indexed" not in k.lower())
        out[site] = {
            "sitemap_total_locs": len(locs),
            "sitemap_article_urls": len(articles),
            "sampled": len(sample),
            "indexed_pct": round(100 * indexed / max(1, tot), 1),
            "states": dict(states),
        }
        print(f"\n=== {site} ===")
        print(f"  sitemap: {len(locs)} locs ({len(articles)} article URLs) | sampled {len(sample)}")
        for k, v in states.most_common():
            pct = f"{100*v/max(1,tot):.0f}%" if not k.startswith("ERR") else ""
            print(f"    {v:>3} {pct:>4}  {k}")
        print(f"  → indexed: {out[site]['indexed_pct']}%")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-site", type=int, default=40)
    ap.add_argument("--baseline", action="store_true",
                    help="write data/indexing_baseline.json")
    ap.add_argument("--compare", help="path to a prior snapshot to diff against")
    ap.add_argument("--today", help="YYYY-MM-DD stamp override (snapshots are date-stamped)")
    args = ap.parse_args()

    svc = build("searchconsole", "v1",
                credentials=get_user_credentials(), cache_discovery=False)
    result = census(svc, args.per_site)

    stamp = args.today or os.environ.get("CENSUS_DATE") or "undated"
    snapshot = {"date": stamp, "sites": result}

    DATA_DIR.mkdir(exist_ok=True)
    dated = DATA_DIR / f"indexing_census_{stamp}.json"
    dated.write_text(json.dumps(snapshot, indent=2))
    print(f"\nSAVED {dated}")
    if args.baseline:
        base = DATA_DIR / "indexing_baseline.json"
        base.write_text(json.dumps(snapshot, indent=2))
        print(f"SAVED {base}")

    if args.compare and Path(args.compare).exists():
        prior = json.loads(Path(args.compare).read_text())
        print(f"\n=== LIFT vs {prior.get('date','baseline')} ===")
        for site in result:
            now = result[site]["indexed_pct"]
            was = prior.get("sites", {}).get(site, {}).get("indexed_pct")
            if was is None:
                print(f"  {site}: {now}% (no baseline)")
            else:
                d = round(now - was, 1)
                print(f"  {site}: {was}% → {now}%  ({'+' if d >= 0 else ''}{d} pts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
