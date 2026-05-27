"""One-shot: raise daily_article_cap + add article_type_floors per site.

After this script runs, sites.config.content_plan gets two new shape:

    daily_article_cap: 6                  (was 3)
    article_type_floors:                  (NEW — guarantees a minimum per type)
      comparison: 3                       (ntecodex affiliate review floor)
    OR for pixelmatch:
      vs_comparison: 3                    (revenue-relevant content floor)

Idempotent — re-runs cleanly.

Usage:
    python -m scripts.migrate_site_caps              # apply
    python -m scripts.migrate_site_caps --dry-run    # report only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from src.db.client import get_db_connection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# Per-site cap policy.
SITE_POLICY: dict[str, dict] = {
    "ntecodex.com": {
        "daily_article_cap": 10,
        "article_type_floors": {
            # Guarantees revenue + SEO breadth every day. Every directory
            # on /sitemap.xml gets at least 1 daily update so Google's
            # freshness crawler always finds movement. Floor sum = 6 of
            # the 10 daily_article_cap slots; the other 4 go to whatever
            # the selector wants (build / faq / boss / etc).
            "comparison":  3,   # /guides/ — affiliate roundups, revenue
            "character_db": 1,  # /characters/ — was 0/day, now floor 1
            "weapon_db":    1,  # /weapons/ — was 0/day
            "news":         1,  # /news/ — banner/patch coverage
        },
    },
    "pixelmatch.art": {
        "daily_article_cap": 8,
        "article_type_floors": {
            # vs_comparison is pixelmatch's revenue-relevant content type
            # (tool-vs-tool reviews channel readers into SaaS signups +
            # Amazon-Associate hardware links).
            "vs_comparison": 3,
            # use_case + policy_guide rotate occasional coverage to keep
            # all four directories fresh in the sitemap.
            "use_case":      1,
            "policy_guide":  1,
        },
    },
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id, domain, config from sites")
        rows = cur.fetchall()
        updated = 0
        for site_id, domain, config in rows:
            policy = SITE_POLICY.get(domain)
            if not policy:
                print(f"  ✓ {domain}: no policy entry, skip")
                continue
            if not isinstance(config, dict):
                config = json.loads(config) if isinstance(config, str) else {}

            cp = dict(config.get("content_plan") or {})
            before_cap = cp.get("daily_article_cap")
            before_floors = cp.get("article_type_floors") or {}

            new_cap = policy["daily_article_cap"]
            new_floors = policy["article_type_floors"]

            if before_cap == new_cap and before_floors == new_floors:
                print(f"  ✓ {domain}: already current ({new_cap}/day, floors={new_floors})")
                continue

            cp["daily_article_cap"] = new_cap
            cp["article_type_floors"] = new_floors
            config["content_plan"] = cp

            print(f"  ⚙ {domain}:")
            print(f"      daily_article_cap: {before_cap}  →  {new_cap}")
            print(f"      article_type_floors: {before_floors}  →  {new_floors}")

            if args.dry_run:
                continue
            cur.execute(
                "update sites set config=%s, updated_at=now() where id=%s",
                (json.dumps(config), str(site_id)),
            )
            updated += 1
        print()
        print(f"Result: {updated} site(s) updated.")
        if args.dry_run:
            print("DRY RUN — no DB writes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
