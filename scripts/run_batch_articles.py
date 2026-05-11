"""Run the orchestrator N times in a row. Reports a per-article scorecard.

Usage:
    python -m scripts.run_batch_articles --count 5 --max-retries 1
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection
from src.pipeline.orchestrator import run_one_article


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--max-retries", type=int, default=1,
                   help="Override site_config qa_thresholds.max_retry_rounds")
    p.add_argument("--budget-usd", type=float, default=1.0,
                   help="Stop if cumulative cost exceeds this")
    p.add_argument("--force-type", default=None,
                   help="Override KeywordSelector's article_type decision. "
                        "Must be one of: build, tier_list, boss_guide, reroll, "
                        "character_db, weapon_db, news, faq, comparison.")
    args = p.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain = 'ntecodex.com' limit 1")
        site_id = cur.fetchone()[0]

    print("=" * 78)
    print(f"=== Batch run: {args.count} articles, max_retries={args.max_retries}, "
          f"budget=${args.budget_usd:.2f} ===")
    print("=" * 78)

    results: list[dict] = []
    cumulative_cost = 0.0
    t0 = time.perf_counter()

    for i in range(1, args.count + 1):
        print(f"\n{'#' * 78}\n# Article {i}/{args.count}\n{'#' * 78}")
        try:
            summary = run_one_article(
                site_id,
                max_retry_rounds_override=args.max_retries,
                force_article_type=args.force_type,
            )
        except Exception as e:
            print(f"❌ Article {i} crashed: {type(e).__name__}: {e}")
            results.append({"index": i, "status": "crashed", "error": str(e)})
            continue

        # Sum cost from DB for this article
        article_id = summary.get("article_id")
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "select coalesce(sum(cost_usd), 0)::float "
                "from agent_runs where article_id = %s",
                (article_id,),
            )
            article_cost = float(cur.fetchone()[0])
            # Plus the keyword_selector run that has article_id=NULL but matches
            # this article's keyword. Approximate by latest selector run before now.
            cur.execute(
                "select coalesce(cost_usd, 0)::float from agent_runs "
                "where agent_name='keyword_selector' and article_id is null "
                "order by created_at desc limit 1"
            )
            sel_cost_row = cur.fetchone()
            sel_cost = float(sel_cost_row[0]) if sel_cost_row else 0.0
        total_article_cost = article_cost + sel_cost

        cumulative_cost += total_article_cost

        qa = summary.get("qa") or {}
        fb = qa.get("feedback") or {}
        results.append({
            "index": i,
            "article_id": article_id,
            "keyword": summary.get("keyword"),
            "article_type": summary.get("article_type"),
            "final_status": summary.get("final_status"),
            "qa_score": qa.get("score"),
            "factual_accuracy": fb.get("factual_accuracy"),
            "fabricated_terms": fb.get("fabricated_terms") or [],
            "word_count": summary.get("word_count"),
            "cost_usd": total_article_cost,
        })

        print(f"\n   ↪︎ result: {summary.get('final_status')}  "
              f"qa={qa.get('score')}  cost=${total_article_cost:.4f}  "
              f"cumulative=${cumulative_cost:.4f}")

        if cumulative_cost >= args.budget_usd:
            print(f"\n⛔ Budget cap ${args.budget_usd:.2f} reached. Stopping.")
            break

    elapsed = time.perf_counter() - t0

    # Scorecard
    print()
    print("=" * 78)
    print("=== Batch Scorecard ===")
    print("=" * 78)
    passed = sum(1 for r in results if r.get("final_status") == "qa_passed")
    failed = sum(1 for r in results if r.get("final_status") == "qa_failed")
    crashed = sum(1 for r in results if r.get("status") == "crashed")
    qa_scores = [r["qa_score"] for r in results if r.get("qa_score") is not None]
    fa_scores = [r["factual_accuracy"] for r in results if r.get("factual_accuracy") is not None]
    avg_qa = sum(qa_scores) / len(qa_scores) if qa_scores else 0
    avg_fa = sum(float(x) for x in fa_scores) / len(fa_scores) if fa_scores else 0
    word_counts = [r["word_count"] for r in results if r.get("word_count")]
    avg_wc = sum(word_counts) / len(word_counts) if word_counts else 0

    print(f"  Articles attempted   : {len(results)}")
    print(f"  ✅ qa_passed         : {passed}")
    print(f"  ❌ qa_failed         : {failed}")
    print(f"  💥 crashed           : {crashed}")
    print(f"  📈 avg qa_score      : {avg_qa:.2f}")
    print(f"  🔬 avg factual_acc   : {avg_fa:.2f}")
    print(f"  📝 avg word_count    : {avg_wc:.0f}")
    print(f"  💰 total cost        : ${cumulative_cost:.4f}")
    print(f"  ⏱️  wall clock        : {elapsed:.1f}s")

    print()
    print("Per-article:")
    for r in results:
        st = r.get("final_status") or r.get("status")
        kw = r.get("keyword", "?")
        atype = r.get("article_type", "?")
        score = r.get("qa_score")
        fa = r.get("factual_accuracy")
        cost = r.get("cost_usd", 0)
        fab = r.get("fabricated_terms") or []
        flag = "✅" if st == "qa_passed" else "❌"
        print(f"  {flag} #{r['index']}  [{st}]  {kw!r} ({atype})  "
              f"score={score} fa={fa} cost=${cost:.4f}")
        if fab:
            print(f"       fabricated: {fab[:5]}")

    return 0 if passed >= 1 else 1


if __name__ == "__main__":
    sys.exit(main())
