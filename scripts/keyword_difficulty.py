"""LLM-estimated keyword competition + volume → keywords.competition / .search_volume.

The funnel diagnosis showed the sites are pivoting to mainstream-gear
keywords (good for writability / low fabrication) but the selector has
NO awareness of keyword competition — so it can't tell a rankable
long-tail ('best standing desk for wfh back pain under $500') from an
unrankable head term ('best standing desk') that a zero-authority domain
can never win. The columns exist but were 100% empty.

No paid SEO API is configured, so this uses the writing LLM to estimate,
per keyword:
  • competition  ∈ [0,1]  (0 = easy/low-comp long-tail; 1 = head term
                            owned by high-DA incumbents)
  • search_volume_band ∈ {1,2,3} (1=niche, 2=moderate, 3=high) stored as
                            a coarse integer in the search_volume column
LLM estimates are rough but DIRECTIONAL — enough to bias the selector
toward the rankable sweet spot (decent volume × low competition).

Idempotent: only scores planned keywords whose competition IS NULL, in
batches. Cheap (one call per ~25 keywords).

Usage:
  python -m scripts.keyword_difficulty --site ntecodex.com
  python -m scripts.keyword_difficulty            # all sites
  python -m scripts.keyword_difficulty --limit 50 # cap per run
"""

from __future__ import annotations

import argparse
import json
import sys

from src.db.client import get_db_connection
from src.utils.llm import get_llm_provider
from src.agents._json_extract import extract_json


BATCH = 15

PROMPT = """You are an SEO analyst estimating keyword difficulty for a NEW,
low-authority website (think: a domain only a few weeks old, almost no
backlinks). For each keyword below, estimate:

  competition: 0.0-1.0 — how hard it is for a NEW site to rank page 1.
    0.0-0.3 = long-tail, specific, low competition (a new site CAN rank
              with good content) — e.g. "best standing desk for wfh back
              pain under $500", "how to mount a camera without drilling"
    0.4-0.6 = moderate — niche-specific but some established competition
    0.7-1.0 = head term, dominated by high-authority incumbents, a new
              site CANNOT realistically rank — e.g. "best standing desk",
              "security cameras", "gaming chair"

  volume: 1-3 — rough monthly search demand.
    1 = niche / low,  2 = moderate,  3 = high / popular

Favor giving SPECIFIC long-tail buyer questions a LOW competition score —
those are exactly what a new site should target.

Keywords (JSON array of {{id, keyword}}):
{items}

Reply with ONLY a JSON array, one object per keyword, no prose, no fences:
[{{"id": "<id>", "competition": <0.0-1.0>, "volume": <1-3>}}, ...]
"""


def _model(cfg):
    tp = (cfg or {}).get("text_provider") or {}
    return (tp.get("keyword_research_model") or tp.get("outline_model")
            or "gemini-3-flash-preview")


def score_site(domain, limit, dry):
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id, config from sites where domain=%s", (domain,))
        row = cur.fetchone()
        if not row:
            print(f"  {domain}: not found"); return
        sid, cfg = str(row[0]), row[1]
        cur.execute(
            """select id, keyword from keywords
               where site_id=%s and status='planned' and competition is null
               order by created_at desc limit %s""",
            (sid, limit),
        )
        rows = cur.fetchall()
    if not rows:
        print(f"  {domain}: no unscored planned keywords"); return

    llm = get_llm_provider("gemini")
    model = _model(cfg)
    scored = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        items = json.dumps([{"id": str(k), "keyword": kw} for k, kw in batch])
        try:
            resp = llm.generate(prompt=PROMPT.format(items=items), model=model,
                                 max_tokens=6000, temperature=0.2, json_mode=True)
            txt = (resp.text or "").strip()
            # The prompt asks for a JSON ARRAY. Parse it directly; fall back
            # to extract_json (object-shaped) only if that fails.
            data = None
            try:
                data = json.loads(txt)
            except Exception:
                start, end = txt.find("["), txt.rfind("]")
                if start != -1 and end > start:
                    data = json.loads(txt[start:end + 1])
                else:
                    obj = extract_json(txt)
                    data = obj.get("items") or obj.get("results") or [] if isinstance(obj, dict) else []
            if isinstance(data, dict):
                data = data.get("items") or data.get("results") or [data]
        except Exception as e:
            print(f"  {domain}: batch {i//BATCH} parse error: {str(e)[:80]}")
            continue
        by_id = {str(d.get("id")): d for d in data if isinstance(d, dict)}
        with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
            for kid, kw in batch:
                d = by_id.get(str(kid))
                if not d:
                    continue
                try:
                    comp = max(0.0, min(1.0, float(d.get("competition"))))
                    vol = int(d.get("volume") or 2)
                except (TypeError, ValueError):
                    continue
                if dry:
                    print(f"    [DRY] comp={comp:.2f} vol={vol}  {kw[:50]}")
                    continue
                cur.execute(
                    "update keywords set competition=%s, search_volume=%s, "
                    "updated_at=now() where id=%s",
                    (comp, vol, kid),
                )
                scored += 1
    print(f"  {domain}: scored {scored} keyword(s)")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--site")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    with get_db_connection() as conn, conn.cursor() as cur:
        if args.site:
            cur.execute("select domain from sites where domain=%s", (args.site,))
        else:
            cur.execute("select domain from sites order by domain")
        sites = [r[0] for r in cur.fetchall()]
    print(f"=== keyword_difficulty sites={len(sites)} ===")
    for d in sites:
        score_site(d, args.limit, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
