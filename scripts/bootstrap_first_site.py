"""
Bootstrap the first site row (ntecodex.com).

Idempotent: checks for an existing row by domain before inserting.
owner_id is left NULL — the user will sign up via Supabase Auth later
and we'll patch the row at that point.

Usage:
    python -m scripts.bootstrap_first_site
"""

from __future__ import annotations

import json
import sys

from src.db.client import get_db_connection
from src.models.site import Site


SITE_DOMAIN = "ntecodex.com"
SITE_NAME = "NTE Codex"

CONFIG: dict = {
    "site_slug": "ntecodex",
    "site_name": "NTE Codex",
    "primary_language": "en",
    "game": {
        "name": "Neverness to Everness",
        "abbreviation": "NTE",
        "release_date": "2026-04-29",
        "developer": "Hotta Studio",
        "publisher": "Perfect World",
        "genre": "Gacha / RPG",
        "platforms": ["PC", "iOS", "Android"],
    },
    "content_plan": {
        "daily_articles": 3,
        "min_word_count": 1200,
        "max_word_count": 2500,
        "diversity": {
            "required_types": [
                "build", "tier_list", "boss_guide", "reroll",
                "character_db", "weapon_db", "news", "faq",
            ],
            "min_types_per_week": 5,
        },
    },
    "qa_thresholds": {
        "min_quality_score": 7.0,
        "max_retry_rounds": 3,
        "consecutive_failure_alert": 5,
        "weekly_pass_rate_min": 0.40,
    },
    "text_provider": {
        "provider": "gemini",
        "writing_model": "gemini-3-flash-preview",
        "qa_model": "gemini-3.1-pro-preview",
        "outline_model": "gemini-3-flash-preview",
        "keyword_research_model": "gemini-3.1-flash-lite-preview",
        "report_summary_model": "gemini-3-flash-preview",
        "fallback_provider": None,
    },
    "image_provider": {
        "provider": "gemini",
        "model": "gemini-2.5-flash-image",
        "default_aspect_ratio": "16:9",
        "fallback_provider": None,
        "extra_params": {},
    },
    # Phase 1.B: skip paid acquisition. Field retained so future config diff is clean.
    "ad_budget": {
        "daily_max_usd": 0,
        "total_test_usd": 0,
        "loss_stop_threshold_usd": 0,
    },
    "tools_enabled": [],
}


def main() -> int:
    print(f"🌱 Bootstrap site: {SITE_DOMAIN}")
    with get_db_connection() as conn, conn.cursor() as cur:
        # 1. Check existing
        cur.execute("select id from sites where domain = %s", (SITE_DOMAIN,))
        existing = cur.fetchone()
        if existing:
            print(f"   ↪︎  Already present (id={existing[0]}). No write performed.")
        else:
            cur.execute(
                """
                insert into sites (domain, site_name, status, config, owner_id)
                values (%s, %s, %s, %s::jsonb, NULL)
                returning id
                """,
                (SITE_DOMAIN, SITE_NAME, "active", json.dumps(CONFIG)),
            )
            new_id = cur.fetchone()[0]
            print(f"   ✅ Inserted (id={new_id})")

        # 2. Read back as dict, then construct Pydantic Site to validate round-trip
        cur.execute(
            "select id, domain, site_name, status, config, owner_id, "
            "       created_at, updated_at from sites where domain = %s",
            (SITE_DOMAIN,),
        )
        cols = [d.name for d in cur.description]
        row = dict(zip(cols, cur.fetchone()))

    site = Site.model_validate(row)
    print()
    print("📦 Site (Pydantic dump):")
    # Use mode='json' so UUID/datetime serialize to strings cleanly
    print(json.dumps(site.model_dump(mode="json"), indent=2, ensure_ascii=False))

    # Sanity assertions
    assert site.domain == SITE_DOMAIN
    assert site.config["site_slug"] == "ntecodex"
    assert site.config["text_provider"]["writing_model"] == "gemini-3-flash-preview"
    assert site.config["ad_budget"]["daily_max_usd"] == 0
    print("\n✅ Round-trip validated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
