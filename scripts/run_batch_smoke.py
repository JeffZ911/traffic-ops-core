"""Run the orchestrator N times in a single cron invocation.

Replaces run_one_article_smoke for the velocity-accelerated cron (Phase
2.4 / 2026-05-12). Each iteration:

  - Re-reads budget_guard. If `action == 'pause_all'`, stop the batch
    immediately so we don't burn dollars after the kill switch fires.
  - Wraps run_one_article in its own try/except so a single crash
    doesn't sink the rest of the batch.
  - Tallies cost per iteration + cumulative.
  - Prints a per-article scorecard at the end (same shape as
    run_batch_articles.py so existing dashboards keep parsing).

Designed for the multi-iteration GH Actions step where we want 4 attempts
per cron × 6 cron/day = 24 attempts/day. The user's 65% pass-rate target
puts us at ~15-17 published / day.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection
from src.pipeline.orchestrator import run_one_article
from src.utils.budget_guard import check_monthly_budget


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, default=4,
                   help="How many articles to attempt this batch")
    p.add_argument("--max-retries", type=int, default=1,
                   help="QA-rewrite retry rounds inside each article")
    args = p.parse_args()

    import os
    site_domain = os.getenv("SITE_DOMAIN", "ntecodex.com")
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain=%s limit 1", (site_domain,))
        row = cur.fetchone()
        if not row:
            print(f"❌ site {site_domain!r} not found in sites table")
            return 2
        site_id = row[0]

    print("=" * 78)
    print(f"=== Batch smoke: {args.count} articles ===")
    print("=" * 78)

    results: list[dict] = []
    t0 = time.perf_counter()

    for i in range(1, args.count + 1):
        # Per-iteration budget check — bail mid-batch if we cross 95%.
        bg = check_monthly_budget(site_id)
        if bg.action == "pause_all":
            print(f"\n🛑 budget_guard action='pause_all' "
                  f"(${bg.spent_usd:.2f} / ${bg.budget_usd:.2f}); "
                  f"stopping batch after {i - 1} of {args.count}")
            break

        print(f"\n{'#' * 78}\n# Article {i}/{args.count}  "
              f"(month spent ${bg.spent_usd:.2f}/${bg.budget_usd:.2f} = "
              f"{bg.percent*100:.0f}%)\n{'#' * 78}")
        try:
            summary = run_one_article(
                site_id, max_retry_rounds_override=args.max_retries,
            )
        except Exception as e:
            print(f"❌ Article {i} crashed: {type(e).__name__}: {e}")
            results.append({"index": i, "status": "crashed", "error": str(e)[:200]})
            continue

        article_id = summary.get("article_id")
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "select coalesce(sum(cost_usd),0)::float from agent_runs "
                "where article_id = %s",
                (article_id,),
            )
            article_cost = float(cur.fetchone()[0])
            # Plus the selector run (no article_id link)
            cur.execute(
                "select coalesce(cost_usd, 0)::float from agent_runs "
                "where agent_name='keyword_selector' and article_id is null "
                "order by created_at desc limit 1"
            )
            sel_row = cur.fetchone()
            sel_cost = float(sel_row[0]) if sel_row else 0.0
        total_cost = article_cost + sel_cost

        qa = summary.get("qa") or {}
        fb = qa.get("feedback") or {}
        results.append({
            "index": i,
            "article_id": article_id,
            "keyword": summary.get("keyword"),
            "game": summary.get("game"),
            "article_type": summary.get("article_type"),
            "final_status": summary.get("final_status"),
            "qa_score": qa.get("score"),
            "factual_accuracy": fb.get("factual_accuracy"),
            "fabricated_terms": fb.get("fabricated_terms") or [],
            "word_count": summary.get("word_count"),
            "cost_usd": total_cost,
        })

        print(f"\n   ↪︎ result: {summary.get('final_status')}  "
              f"qa={qa.get('score')}  cost=${total_cost:.4f}")

    elapsed = time.perf_counter() - t0
    print()
    print("=" * 78)
    print("=== Batch Smoke Scorecard ===")
    print("=" * 78)
    passed = sum(1 for r in results if r.get("final_status") == "qa_passed")
    failed = sum(1 for r in results if r.get("final_status") == "qa_failed")
    crashed = sum(1 for r in results if r.get("status") == "crashed")
    qa_scores = [r["qa_score"] for r in results if r.get("qa_score") is not None]
    avg_qa = sum(qa_scores) / len(qa_scores) if qa_scores else 0
    cumulative_cost = sum(r.get("cost_usd", 0) for r in results)
    games = {}
    for r in results:
        g = r.get("game") or "n/a"
        games[g] = games.get(g, 0) + 1

    print(f"  Articles attempted   : {len(results)}")
    print(f"  ✅ qa_passed         : {passed}")
    print(f"  ❌ qa_failed         : {failed}")
    print(f"  💥 crashed           : {crashed}")
    print(f"  📈 avg qa_score      : {avg_qa:.2f}")
    print(f"  🎮 game distribution : {games}")
    print(f"  💰 total cost        : ${cumulative_cost:.4f}")
    print(f"  ⏱️  wall clock        : {elapsed:.1f}s")

    print()
    print("Per-article:")
    for r in results:
        st = r.get("final_status") or r.get("status")
        kw = r.get("keyword", "?")
        atype = r.get("article_type", "?")
        game = r.get("game", "?")
        score = r.get("qa_score")
        cost = r.get("cost_usd", 0)
        flag = "✅" if st == "qa_passed" else "❌"
        print(f"  {flag} #{r['index']}  [{st}]  {kw!r} ({atype}, {game})  "
              f"score={score} cost=${cost:.4f}")

    # Exit 0 always — cron continues to publish/image/deploy steps for
    # any qa_passed articles even if some attempts failed.
    return 0


if __name__ == "__main__":
    sys.exit(main())
