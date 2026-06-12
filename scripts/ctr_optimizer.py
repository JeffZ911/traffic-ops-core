"""CTR optimizer — rewrite title/meta of pages that EARN impressions but no
clicks (the "曝光→点击" last mile).

Targets: GSC pages with impressions >= --min-impressions and avg position
5-15 over the last 14 SETTLED days (data older than 3 days). Those pages are
on page 1-2 — visible, unclicked. The lever is the SERP snippet: freshness
markers, concrete numbers, and the words searchers actually type (taken from
the page's own top GSC queries).

Per run: up to --cap pages. A page is skipped if rewritten in the last 30
days (marker in metrics_raw payload 'ctr_rewrite'). The rewrite edits the
article .md frontmatter (title + description) in SITE_REPO_PATH — the cron's
content-push step ships it; the slug/URL NEVER changes.

Usage (CI, with SITE_REPO_PATH set):
  python -m scripts.ctr_optimizer --site ntecodex.com --cap 2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from src.collectors.base import store_raw
from src.db.client import get_db_connection
from src.utils.llm import get_llm_provider

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

EXCLUDE_PATHS = {"/", "/faq", "/tier-list", "/about", "/contact"}

_PROMPT = """You are a CTR copywriter. This page RANKS on Google (position
{pos:.1f}) and earned {impr} impressions in 2 weeks — but almost no clicks.
The snippet is the problem. Rewrite ONLY the SEO title and meta description.

PAGE: {url}
CURRENT TITLE: {title}
CURRENT META: {desc}
TOP REAL QUERIES it appears for (what searchers actually type):
{queries}
TODAY: {today}

Rules:
- Title <= 62 chars. Front-load the terms from the real queries. Add ONE
  concrete hook: a freshness marker ("{month} {year}"), a number, or a
  specific outcome. No clickbait, no ALL CAPS, intent must stay identical.
- Meta description 140-160 chars: what the reader GETS, in plain words,
  including 1-2 query terms naturally. End with a soft action phrase.
- NEVER invent facts/specs/numbers not implied by the current title/meta.

Reply ONLY JSON (no fence): {{"title": "...", "description": "..."}}
"""


def _recent_rewrites(site_id: str) -> set[str]:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """select payload->'ctr_rewrite'->>'url' from metrics_raw
               where site_id=%s and payload ? 'ctr_rewrite'
                 and fetched_at >= now() - interval '30 days'""",
            (site_id,),
        )
        return {r[0] for r in cur.fetchall() if r[0]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="ntecodex.com")
    ap.add_argument("--cap", type=int, default=2)
    ap.add_argument("--min-impressions", type=int, default=20)
    ap.add_argument("--repo", default=None, help="site repo path (or env SITE_REPO_PATH)")
    ap.add_argument("--model", default="gemini-3.1-pro-preview")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import os
    repo = Path(args.repo or os.getenv("SITE_REPO_PATH") or "")
    if not repo.exists():
        print(f"❌ site repo not found: {repo!r} (set SITE_REPO_PATH)"); return 2

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain=%s", (args.site,))
        row = cur.fetchone()
        if not row:
            print(f"❌ site {args.site} missing"); return 2
        site_id = str(row[0])

    from googleapiclient.discovery import build
    from src.utils.google_oauth import get_user_credentials
    svc = build("searchconsole", "v1", credentials=get_user_credentials(),
                cache_discovery=False)
    prop = f"sc-domain:{args.site}"
    end = date.today() - timedelta(days=3)          # settled data only
    start = end - timedelta(days=14)

    resp = svc.searchanalytics().query(siteUrl=prop, body={
        "startDate": start.isoformat(), "endDate": end.isoformat(),
        "dimensions": ["page"], "rowLimit": 500}).execute()
    done_recent = _recent_rewrites(site_id)
    cands = []
    for r in resp.get("rows", []):
        url = r["keys"][0]
        path = re.sub(r"^https?://[^/]+", "", url).rstrip("/") or "/"
        if path in EXCLUDE_PATHS or url in done_recent:
            continue
        impr, pos, clk = int(r["impressions"]), float(r["position"]), int(r["clicks"])
        ctr = clk / impr if impr else 0
        if impr >= args.min_impressions and 5 <= pos <= 15 and ctr < 0.03:
            cands.append((impr, pos, clk, url, path))
    cands.sort(key=lambda x: -x[0])
    if not cands:
        print("  no CTR candidates (impressions/position window empty) — done")
        return 0
    print(f"  {len(cands)} candidate(s); taking top {args.cap}")

    llm = get_llm_provider("gemini")
    today = date.today()
    fixed = 0
    for impr, pos, clk, url, path in cands[: args.cap]:
        slug = path.rstrip("/").split("/")[-1]
        md_files = list(repo.glob(f"src/content/**/{slug}.md"))
        if not md_files:
            print(f"  ⏭  no .md for {slug} — skip"); continue
        md_path = md_files[0]
        text = md_path.read_text(encoding="utf-8")
        mt = re.search(r'(?m)^title:\s*["\']?(.*?)["\']?\s*$', text)
        mdsc = re.search(r'(?m)^description:\s*["\']?(.*?)["\']?\s*$', text)
        cur_title = mt.group(1) if mt else slug
        cur_desc = mdsc.group(1) if mdsc else ""

        qresp = svc.searchanalytics().query(siteUrl=prop, body={
            "startDate": start.isoformat(), "endDate": end.isoformat(),
            "dimensions": ["query"], "rowLimit": 8,
            "dimensionFilterGroups": [{"filters": [
                {"dimension": "page", "operator": "equals", "expression": url}]}],
        }).execute()
        queries = "\n".join(
            f"  - {q['keys'][0]} ({int(q['impressions'])} imp)"
            for q in qresp.get("rows", [])) or "  (queries anonymized)"

        resp2 = llm.generate(prompt=_PROMPT.format(
            pos=pos, impr=impr, url=url, title=cur_title, desc=cur_desc,
            queries=queries, today=today.isoformat(),
            month=today.strftime("%B"), year=today.year),
            model=args.model, max_tokens=2500, temperature=0.4, json_mode=True)
        t = (resp2.text or "").strip()
        if t.startswith("```"):
            t = t.split("\n", 1)[-1].rsplit("```", 1)[0]
        i, j = t.find("{"), t.rfind("}")
        try:
            obj = json.loads(t[i:j + 1])
        except Exception:
            print(f"  ⚠️  parse failed for {slug}"); continue
        new_title = (obj.get("title") or "").strip()
        new_desc = (obj.get("description") or "").strip()
        if not new_title or len(new_title) > 75 or not new_desc:
            print(f"  ⚠️  bad rewrite for {slug}"); continue

        print(f"  ✏️  {path} ({impr} imp, pos {pos:.1f}, {clk} clk)")
        print(f"      旧: {cur_title}")
        print(f"      新: {new_title}")
        if args.dry_run:
            continue
        def _q(v: str) -> str:
            return '"' + v.replace('"', "'") + '"'
        if mt:
            text = text[:mt.start()] + f"title: {_q(new_title)}" + text[mt.end():]
        mdsc = re.search(r'(?m)^description:\s*["\']?(.*?)["\']?\s*$', text)
        if mdsc:
            text = text[:mdsc.start()] + f"description: {_q(new_desc)}" + text[mdsc.end():]
        md_path.write_text(text, encoding="utf-8")
        with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("update articles set title=%s where site_id=%s and slug=%s",
                        (new_title, site_id, slug))
        store_raw(site_id, "gsc", today,
                  {"ctr_rewrite": {"url": url, "at": today.isoformat(),
                                   "old_title": cur_title[:120], "new_title": new_title}})
        fixed += 1
    print(f"\n  ✓ rewrote {fixed} page snippet(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
