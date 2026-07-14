"""Ingest an EXTERNAL keyword-strategy library (keywords.json) → Supabase
`keywords` table for imade4u. Replaces the internal self-generated selection as
the PRIMARY topic source (content_imade4u's picker biases source=
'external_strategy' first; see HANDOFF.md from the keyword-strategy session).

Field map (per the traffic-ops-core Q3 answer):
  keyword          → keyword
  intent           → intent
  priority_score   → priority_score
  search_volume    → search_volume   (nullable — column exists)
  competition      → competition     (nullable)
  scene, target_product_handle, tier → notes (pipe-encoded), so the picker +
    build_article can lock the exact product:
      type=external|tier=<n>|scene=<scene>|handle=<h>|match=<derived nouns>
  handle_match_confidence >= 2 → write handle= (trusted); else omit handle=
    and let the existing fuzzy match= logic pick products.

source='external_strategy' (distinct → easy to bias/roll back). Idempotent:
re-running updates priority/notes but never resurrects a published keyword.

Usage:
  python -m scripts.ingest_external_keywords --file /path/keywords.json
  python -m scripts.ingest_external_keywords --file ... --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SITE = "imade4u.com"
SOURCE = "external_strategy"
# imade4u catalog product nouns — for the match= fuzzy fallback when a keyword
# has no trusted handle (the catalog-reality gate + _products need match nouns).
_PRODUCT_NOUNS = ("necklace", "bracelet", "ring", "mug", "pillow", "blanket",
                  "ornament", "portrait", "keychain", "sign", "box", "tumbler",
                  "frame", "cushion", "pendant", "bangle", "charm")


def _derive_match(keyword: str, scene: str) -> str:
    """Product nouns present in the keyword (→ match= fallback). Falls back to a
    broad jewelry set so the catalog-reality gate still recalls real products."""
    kl = keyword.lower()
    found = [n for n in _PRODUCT_NOUNS if n in kl]
    if not found:
        found = ["necklace", "bracelet"]     # catalog is necklace/bracelet-heavy
    return ", ".join(list(dict.fromkeys(found))[:3])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="path to keywords.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else (
        data.get("keywords") or next((v for v in data.values() if isinstance(v, list)), []))
    if not items:
        print("❌ no keyword items in file"); return 2

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain=%s", (SITE,))
        row = cur.fetchone()
        if not row:
            print(f"❌ {SITE} missing"); return 2
        site_id = str(row[0])

        rows = []
        for k in items:
            kw = (k.get("keyword") or "").strip()
            if not kw:
                continue
            scene = (k.get("scene") or "general").strip()
            handle = (k.get("target_product_handle") or "").strip()
            conf = k.get("handle_match_confidence")
            notes = [f"type=external", f"tier={k.get('tier','')}", f"scene={scene}"]
            if handle and isinstance(conf, (int, float)) and conf >= 2:
                notes.append(f"handle={handle}")
            notes.append(f"match={_derive_match(kw, scene)}")   # always: fuzzy fallback + gate
            rows.append((
                site_id, kw, (k.get("intent") or "informational").strip(),
                int(k.get("priority_score") or 60), SOURCE, "|".join(notes),
                k.get("search_volume"), k.get("competition"),
            ))

        tiers = {}
        for k in items:
            tiers[k.get("tier")] = tiers.get(k.get("tier"), 0) + 1
        print(f"  {len(rows)} keyword(s) to ingest for {SITE} (tiers {tiers}); "
              f"{sum(1 for r in rows if 'handle=' in r[5])} with a trusted handle=")
        if args.dry_run:
            for r in rows[:5]:
                print(f"    {r[1][:44]}  [{r[5][:70]}]")
            print("  (dry-run — nothing written)"); return 0

        cur.executemany(
            """insert into keywords
                 (site_id, keyword, intent, priority_score, source, notes,
                  search_volume, competition, status)
               values (%s,%s,%s,%s,%s,%s,%s,%s,'planned')
               on conflict (site_id, keyword) do update set
                 priority_score = excluded.priority_score,
                 source = excluded.source, notes = excluded.notes,
                 intent = excluded.intent, updated_at = now(),
                 -- never resurrect an already-published keyword into the queue
                 status = case when keywords.status = 'published'
                               then keywords.status else 'planned' end""",
            rows,
        )
        conn.commit()
        print(f"  ✓ ingested {len(rows)} external_strategy keyword(s) (status='planned')")
    return 0


if __name__ == "__main__":
    sys.exit(main())
