"""One-shot: rewrite stale Gemini model IDs in sites.config to current ones.

Background: Google retired `gemini-3.1-flash-lite-preview` (404 NOT_FOUND)
which keyword_gardener was configured to use. The whole content_daily cron
fails at "Garden the keyword pool" step until this is fixed.

This script updates the `text_provider` block in sites.config for every site,
replacing any retired model IDs with their currently-available equivalents.
Idempotent: re-runs cleanly when the model IDs are already current.

Migration table:
  gemini-3.1-flash-lite-preview  →  gemini-3-flash-preview  (lite tier retired)
  (other -preview variants kept as-is for now; flip them here if more retire)

Usage:
    python -m scripts.migrate_model_names              # apply
    python -m scripts.migrate_model_names --dry-run    # report only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# Add new mappings here as Google retires more model IDs.
RETIRED_MODELS: dict[str, str] = {
    "gemini-3.1-flash-lite-preview": "gemini-3-flash-preview",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    fixed = 0
    skipped = 0
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id, domain, config from sites")
        rows = cur.fetchall()
        for site_id, domain, config in rows:
            if not isinstance(config, dict):
                config = json.loads(config) if isinstance(config, str) else {}
            tp = config.get("text_provider") or {}
            changes: list[tuple[str, str, str]] = []
            for k, old in list(tp.items()):
                if isinstance(old, str) and old in RETIRED_MODELS:
                    new = RETIRED_MODELS[old]
                    tp[k] = new
                    changes.append((k, old, new))

            if not changes:
                print(f"  ✓ {domain}: no retired models, skip")
                skipped += 1
                continue

            print(f"  ⚙ {domain}:")
            for k, old, new in changes:
                print(f"      {k}: {old}  →  {new}")

            if args.dry_run:
                continue

            config["text_provider"] = tp
            cur.execute(
                "update sites set config = %s, updated_at = now() where id = %s",
                (json.dumps(config), str(site_id)),
            )
            fixed += 1

    print()
    print(f"Result: {fixed} site(s) updated, {skipped} already current.")
    if args.dry_run:
        print("DRY RUN — no DB writes performed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
