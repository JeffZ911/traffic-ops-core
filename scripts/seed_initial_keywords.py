"""
Seed 50 manually-curated NTE keywords into the keywords table.

Idempotent: skips if keywords already exist for ntecodex.com.

Usage:
    python -m scripts.seed_initial_keywords
"""

from __future__ import annotations

import sys

from src.db.client import get_db_connection


# (keyword, intent, priority, notes)
SEEDS: list[tuple[str, str, int, str]] = [
    # --- Reroll (5) — high priority, day-1 traffic magnet ---
    ("nte reroll guide",                "how-to",        95, "Top reroll funnel keyword"),
    ("neverness to everness reroll",    "how-to",        92, "Long-form game name reroll"),
    ("best starter characters nte",     "list",          90, "Reroll target listicle"),
    ("nte reroll tier list",            "list",          88, "Combines reroll + tier intent"),
    ("how to reroll neverness everness android", "how-to", 80, "Platform-specific reroll"),

    # --- Tier List (5) — evergreen high-intent ---
    ("nte tier list",                   "list",          98, "Highest-intent overarching list"),
    ("nte dps tier list",               "list",          90, "Role-specific tier"),
    ("nte support tier list",           "list",          85, "Support tier list"),
    ("nte healer tier list",            "list",          80, "Healer tier list"),
    ("neverness to everness tier list 2026", "list",     93, "Year-suffixed evergreen"),

    # --- Character build (15) — 8 chars × 1-2 keywords each ---
    ("nanally build nte",               "how-to",        88, "Top DPS build query"),
    ("nanally guide nte",               "informational", 84, "Character db page seed"),
    ("hotori build nte",                "how-to",        85, "Support DPS build"),
    ("hotori best team nte",            "informational", 78, "Team-comp angle"),
    ("kira build nte",                  "how-to",        80, "Sub-DPS build"),
    ("alice build nte",                 "how-to",        78, "Healer build"),
    ("alice teams nte",                 "informational", 72, "Healer team comps"),
    ("ami build nte",                   "how-to",        72, "Tank build"),
    ("evelyn build nte",                "how-to",        82, "Mid-tier DPS build"),
    ("evelyn skill priority nte",       "informational", 65, "Niche skill upgrade query"),
    ("zerda build nte",                 "how-to",        70, "Off-meta build"),
    ("scarlett build nte",              "how-to",        76, "Burst DPS build"),
    ("scarlett vs nanally nte",         "comparison",    74, "Char-vs-char comparison"),
    ("kira teams nte",                  "informational", 68, "Sub-DPS team angle"),
    ("nanally vs hotori nte",           "comparison",    77, "Top-tier head-to-head"),

    # --- Best build / weapon (10) — buyer-intent build queries ---
    ("best dps build nte",              "list",          90, "Generic DPS roundup"),
    ("best f2p build nte",              "list",          92, "F2P-friendly comp build"),
    ("best weapons nte",                "list",          85, "Weapon ranking listicle"),
    ("best disks nte",                  "list",          82, "Disk (artifact) set ranking"),
    ("best support build nte",          "list",          78, "Support build roundup"),
    ("best healer build nte",           "list",          75, "Healer build roundup"),
    ("nte free 5 star characters",      "list",          88, "Free-character question"),
    ("eclipse blade nte best on",       "informational", 70, "Weapon → user mapping"),
    ("moonshade weapon nte guide",      "informational", 65, "Single weapon deep-dive"),
    ("stormrider disk set nte",         "informational", 68, "Single disk set guide"),

    # --- Beginner / new player (5) ---
    ("nte beginner guide",              "how-to",        85, "Onboarding traffic"),
    ("nte for beginners",               "how-to",        82, "Variant beginner query"),
    ("how to play neverness to everness", "how-to",      78, "Long-tail beginner"),
    ("nte tips and tricks",             "list",          70, "Tips listicle"),
    ("things to do daily nte",          "list",          74, "Daily routine guide"),

    # --- Boss / endgame (5) ---
    ("nte abyss guide",                 "how-to",        82, "Endgame mode guide"),
    ("nte spiral chamber guide",        "how-to",        72, "Mid-tier boss/mode"),
    ("nte dragon king guide",           "how-to",        70, "Specific boss"),
    ("nte how to beat shadow lord",     "how-to",        68, "Specific boss long-tail"),
    ("nte endgame content guide",       "informational", 65, "Endgame overview"),

    # --- Banner / gacha system (5) ---
    ("nte current banner",              "informational", 95, "High-frequency repeat traffic"),
    ("nte upcoming banners",            "informational", 88, "Forward-looking banner"),
    ("nte pity system explained",       "how-to",        82, "Gacha mechanic"),
    ("nte should i pull",               "comparison",    78, "Decision-help query"),
    ("nte banner schedule 2026",        "informational", 80, "Year-tagged banner cal"),
]


def main() -> int:
    assert len(SEEDS) == 50, f"Expected 50 seeds, got {len(SEEDS)}"

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select id from sites where domain = 'ntecodex.com' limit 1"
        )
        site_row = cur.fetchone()
        if not site_row:
            print("❌ sites table missing ntecodex.com — run bootstrap_first_site.py first.")
            return 2
        site_id = site_row[0]

        cur.execute(
            "select count(*) from keywords where site_id = %s", (str(site_id),)
        )
        existing = cur.fetchone()[0]
        if existing > 0:
            print(f"↪︎  keywords already seeded for ntecodex.com ({existing} rows). Skip.")
            return 0

        rows = [
            (str(site_id), kw, intent, prio, "manual_seed", note)
            for (kw, intent, prio, note) in SEEDS
        ]
        cur.executemany(
            """
            insert into keywords
              (site_id, keyword, intent, priority_score, source, notes, status)
            values (%s, %s, %s, %s, %s, %s, 'planned')
            """,
            rows,
        )
        print(f"✅ Inserted {len(rows)} keywords")

    # Summary
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select intent, count(*) from keywords
             where site_id = %s group by intent order by count(*) desc
            """,
            (str(site_id),),
        )
        print("\n📊 Distribution by intent:")
        for intent, n in cur.fetchall():
            print(f"   {intent or '(NULL)':14s}  {n}")

        cur.execute(
            """
            select min(priority_score), avg(priority_score)::numeric(5,1),
                   max(priority_score), count(*)
              from keywords where site_id = %s
            """,
            (str(site_id),),
        )
        mn, av, mx, total = cur.fetchone()
        print(f"\n📊 priority_score range: min={mn}  avg={av}  max={mx}  total={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
