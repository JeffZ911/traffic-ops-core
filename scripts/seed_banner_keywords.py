"""Seed a small batch of banner-topic keywords so the cron has fuel.

GSC long-tail discovery (see seo_intelligence) is the long-term source of
banner queries. Until that has data, this script seeds 8 hand-curated NTE
banner queries with `status='planned'`, `source='manual'`, and
`notes='banner-seed'` so the operator can spot them in the dashboard.

Idempotent: re-running won't create duplicates (keywords table has a
unique(site_id, keyword) constraint, and we use ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# Hand-curated banner / news keywords for Neverness to Everness. Mix of
# evergreen banner-mechanic queries and time-bound character-debut
# variants. priority_score is set high (80) so KeywordSelector with the
# diversity bonus picks them up quickly.
BANNER_SEEDS: list[dict] = [
    {
        "keyword": "NTE current banner schedule",
        "intent": "informational",
        "notes": "banner-seed | evergreen: who's featured this rotation",
    },
    {
        "keyword": "Neverness to Everness next banner predictions",
        "intent": "informational",
        "notes": "banner-seed | forward-looking, refreshes monthly",
    },
    {
        "keyword": "NTE Hotori banner release date",
        "intent": "informational",
        "notes": "banner-seed | character-specific debut tracker",
    },
    {
        "keyword": "Neverness to Everness banner pull strategy",
        "intent": "how-to",
        "notes": "banner-seed | savings + 50/50 mechanic explainer",
    },
    {
        "keyword": "NTE Strange Encounters standard banner guide",
        "intent": "how-to",
        "notes": "banner-seed | the discounted selector banner",
    },
    {
        "keyword": "NTE banner pity transfer between phases",
        "intent": "informational",
        "notes": "banner-seed | gacha mechanic FAQ-style",
    },
    {
        "keyword": "Neverness to Everness Lacrimosa banner reveal",
        "intent": "informational",
        "notes": "banner-seed | upcoming character speculation",
    },
    {
        "keyword": "NTE banner roadmap version 1.1",
        "intent": "informational",
        "notes": "banner-seed | patch-version forward planning",
    },
]


def main() -> int:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain = 'ntecodex.com' limit 1")
        row = cur.fetchone()
        if not row:
            print("❌ ntecodex.com site row not found; run bootstrap first.")
            return 2
        site_id = row[0]

        inserted = 0
        skipped = 0
        for s in BANNER_SEEDS:
            cur.execute(
                """
                insert into keywords
                  (site_id, keyword, intent, priority_score, status, source, notes)
                values (%s, %s, %s, 80, 'planned', 'manual', %s)
                on conflict (site_id, keyword) do nothing
                returning id
                """,
                (str(site_id), s["keyword"], s["intent"], s["notes"]),
            )
            if cur.fetchone():
                inserted += 1
            else:
                skipped += 1
        conn.commit()

    print(f"✓ Banner seeds: {inserted} inserted, {skipped} already present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
