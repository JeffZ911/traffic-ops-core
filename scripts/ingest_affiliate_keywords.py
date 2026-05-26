"""One-shot ingestion: affiliate_keywords.csv → keywords table.

Reads `data/affiliate_keywords.csv` (250 "best X for Y" affiliate phrases,
5 product categories) and inserts them into the keywords table for the
specified site as `status='planned'`, ready for KeywordSelector to pick.

Priority mapping (drives selection frequency):
  high  volume_band → priority 85   (frequent picks)
  mid   volume_band → priority 70   (regular)
  low   volume_band → priority 50   (occasional)

The `notes` column encodes structured metadata that downstream agents
need (game, category, audience, comp_band, vol_band, type hint):

    game=multi|article_type=comparison|category=gaming_chairs|
    audience=tall_users|comp=mid|vol=low

  - `game=multi` because affiliate roundups aren't game-specific (they
    cross gacha/MMO audiences), so we don't tag a single game.
  - `article_type=comparison` is the explicit hint that KeywordSelector's
    `_guess_type` reads to route these into the new COMPARISON_PROMPT
    (which enforces the 4 hard rules).

Idempotent: existing keyword rows (matched case-insensitively) are
SKIPPED, not updated. Safe to re-run after editing the CSV.

Usage:
    python -m scripts.ingest_affiliate_keywords \\
        --csv data/affiliate_keywords.csv \\
        --site-domain ntecodex.com \\
        [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


PRIORITY_BY_VOL = {"low": 50, "mid": 70, "high": 85}


def site_id_for_domain(cur, domain: str) -> str:
    cur.execute("select id from sites where domain = %s", (domain,))
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"site not found: {domain}")
    return str(row[0])


def existing_keyword_set(cur, site_id: str) -> set[str]:
    """Lowercased existing keyword set for dedup. Includes every status
    (not just planned) so we never re-insert one that's already in flight
    or used."""
    cur.execute("select lower(keyword) from keywords where site_id = %s", (site_id,))
    return {r[0] for r in cur.fetchall()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", default="data/affiliate_keywords.csv")
    ap.add_argument("--site-domain", default="ntecodex.com")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report counts without writing")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        return 2

    # Load + validate the CSV up-front so we fail fast on schema drift.
    rows: list[dict[str, str]] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"keyword", "category", "intent", "audience_tag",
                    "est_volume_band", "competition_band"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            print(f"ERROR: CSV missing required columns: {missing}", file=sys.stderr)
            return 2
        for row in reader:
            kw = (row.get("keyword") or "").strip()
            if not kw:
                continue
            rows.append({k: (v or "").strip() for k, v in row.items()})

    print(f"Loaded {len(rows)} keyword rows from {csv_path}")

    with get_db_connection() as conn, conn.cursor() as cur:
        site_id = site_id_for_domain(cur, args.site_domain)
        existing = existing_keyword_set(cur, site_id)
        print(f"Site {args.site_domain} → {site_id} (existing keywords: {len(existing)})")

        to_insert: list[tuple] = []
        skipped_dup = 0
        for row in rows:
            kw = row["keyword"]
            if kw.lower() in existing:
                skipped_dup += 1
                continue

            vol = (row.get("est_volume_band") or "low").lower()
            priority = PRIORITY_BY_VOL.get(vol, 50)

            notes_parts = [
                "game=multi",
                "article_type=comparison",
                f"category={row.get('category', 'unknown')}",
                f"audience={row.get('audience_tag', 'unknown')}",
                f"comp={row.get('competition_band', 'unknown')}",
                f"vol={vol}",
            ]
            notes = "|".join(notes_parts)
            intent = row.get("intent") or "buy"
            source = "affiliate_seed_2026-05-26"

            to_insert.append((site_id, kw, intent, priority, source, notes))

        print(f"  → {len(to_insert)} new keywords to insert "
              f"({skipped_dup} duplicates skipped)")

        if args.dry_run:
            print("DRY RUN — no DB writes performed.")
            return 0

        if not to_insert:
            print("Nothing to insert. Done.")
            return 0

        cur.executemany(
            """
            insert into keywords (site_id, keyword, intent, priority_score,
                                  source, notes, status)
            values (%s, %s, %s, %s, %s, %s, 'planned')
            """,
            to_insert,
        )
        print(f"Inserted {len(to_insert)} keywords with status='planned'.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
