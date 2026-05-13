"""Apply Phase 2.6 tier classification to articles processed under the
old binary qa_passed/qa_failed gate.

Reasoning: the deep-fix and tier-redesign commits landed Wednesday
evening, but the pipeline had already produced ~70 qa_failed articles
under the old hard-fail-on-any-fab rule. Many of those scored 7.1-8.3
on the rubric and would ship as `tier='clean'` under the new logic,
or as `tier='note'/'strong'` with a calibrated banner.

For each qa_failed article that has NO editorial_tier already set:
  1. Apply the soft fab penalty to the existing qa_score
       0 fab          → no penalty
       1-2 fab + fa≥1 → -0.3
       3+ OR fa<1     → -2.0
  2. Classify into tier:
       ≥7.5 → 'clean'
       ≥6.0 → 'note'
       ≥4.5 → 'strong'
       <4.5 → 'reject'
  3. If tier != 'reject':
       - UPDATE qa_feedback to add editorial_tier + _fab_penalty
       - UPDATE qa_score to the adjusted value
       - UPDATE status to 'qa_passed' so the next publish cron picks
         it up and PublishAgent injects the correct banner
  4. If tier == 'reject': leave qa_failed unchanged.

Idempotent: re-runs find no qa_failed articles missing
editorial_tier — they've already been classified.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def classify(qa_score: float, fab_count: int, fa: float) -> tuple[float, str, str]:
    """Mirror QAAgent._execute tier logic. Returns (adjusted_score, tier, penalty_label)."""
    if fab_count >= 3 or (fa < 1.0 and fab_count >= 1):
        adjust, label = -2.0, f"-2.0 (fab={fab_count}, fa={fa})"
    elif fab_count >= 1:
        adjust, label = -0.3, f"-0.3 (fab={fab_count}, fa={fa})"
    else:
        adjust, label = 0.0, ""
    score = max(round(qa_score + adjust, 2), 0.0)
    if score >= 7.5:
        tier = "clean"
    elif score >= 6.0:
        tier = "note"
    elif score >= 4.5:
        tier = "strong"
    else:
        tier = "reject"
    return score, tier, label


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=200)
    args = p.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id::text, slug, qa_score, qa_feedback
              from articles
             where status = 'qa_failed'
               and qa_score is not null
               and qa_score >= 4.0
               and (qa_feedback is null
                    or qa_feedback->>'editorial_tier' is null)
             order by created_at desc
             limit %s
            """,
            (args.limit,),
        )
        rows = cur.fetchall()

    print(f"=== Found {len(rows)} qa_failed candidates with qa_score ≥ 4.0 and no editorial_tier ===\n")

    promote: list[tuple] = []   # (id, slug, old_score, new_score, tier, fab_count, fa, penalty_label)
    leave_alone: list[tuple] = []
    for art_id, slug, qa_score, fb in rows:
        fb = fb or {}
        fab = fb.get("fabricated_terms") or []
        fab_count = len(fab) if isinstance(fab, list) else 0
        try:
            fa = float(fb.get("factual_accuracy") or 0)
        except (TypeError, ValueError):
            fa = 0.0
        new_score, tier, penalty_label = classify(float(qa_score), fab_count, fa)
        if tier == "reject":
            leave_alone.append((slug, qa_score, new_score, tier))
            continue
        promote.append((art_id, slug, qa_score, new_score, tier, fab_count, fa, penalty_label))

    print(f"  → promote to qa_passed: {len(promote)}")
    for row in promote:
        print(f"    {row[4]:7s} {row[2]:5.2f} → {row[3]:5.2f}  fab={row[5]} fa={row[6]:.1f}  {row[1][:60]}")
    print(f"\n  → leave as qa_failed (post-penalty < 4.5): {len(leave_alone)}")
    for slug, old, new, tier in leave_alone[:5]:
        print(f"    {tier:7s} {old:5.2f} → {new:5.2f}  {slug[:60]}")

    if args.dry_run:
        print("\n  --dry-run; no DB writes")
        return 0
    if not promote:
        print("\n  nothing to backfill")
        return 0

    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        for art_id, slug, _old, new_score, tier, fab_count, fa, penalty_label in promote:
            # Merge into existing qa_feedback (preserve all other keys)
            cur.execute(
                """
                update articles
                   set qa_score = %s,
                       qa_feedback = coalesce(qa_feedback, '{}'::jsonb)
                                  || %s::jsonb,
                       status = 'qa_passed'
                 where id = %s
                """,
                (
                    new_score,
                    json.dumps({
                        "editorial_tier": tier,
                        "_fab_penalty": penalty_label or "0 (no fab)",
                        "_backfilled_from_qa_failed_at": "2026-05-13",
                    }),
                    art_id,
                ),
            )

    print(f"\n  ✓ promoted {len(promote)} articles → status='qa_passed' (next cron will publish)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
