"""End-to-end smoke for RewriterAgent — real LLM calls, ~$0.30-0.60.

Picks ONE published article (preferring lowest qa_score, but takes
--article-id override), runs the full rewrite + QA loop in --dry-run
mode (does NOT touch the article in the DB or on disk), and prints a
report so the operator can eyeball quality before enabling the cron
step in production.

Use this exactly once after a major prompt change. Otherwise the daily
cron is the production exerciser.

Usage:
    python -m scripts.test_rewriter_e2e               # auto-pick worst article
    python -m scripts.test_rewriter_e2e --article-id <uuid>
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv

from src.agents.qa import QAAgent
from src.agents.rewriter import RewriterAgent
from src.db.client import get_db_connection
from src.utils.llm import get_llm_provider


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--article-id", default=None)
    args = p.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id, config from sites where domain='ntecodex.com' limit 1")
        site_id, site_config = cur.fetchone()

        if args.article_id:
            cur.execute(
                "select id, slug, qa_score, article_type from articles "
                "where id = %s and status='published'",
                (args.article_id,),
            )
        else:
            cur.execute(
                """
                select id, slug, qa_score, article_type
                  from articles
                 where site_id = %s and status = 'published'
                   and (qa_feedback is null or
                        (qa_feedback->>'rewrite_skipped') is null or
                        (qa_feedback->>'rewrite_skipped')::boolean = false)
                 order by qa_score nulls last, published_at asc
                 limit 1
                """,
                (str(site_id),),
            )
        row = cur.fetchone()
        if not row:
            print("❌ no eligible article")
            return 2
        article_id, slug, old_qa, atype = row

    print(f"🧪 E2E smoke for RewriterAgent")
    print(f"   article: {slug}  type={atype}  old_qa={old_qa}")
    print(f"   article_id: {article_id}")
    print()

    llm = get_llm_provider("gemini")
    rewriter = RewriterAgent(llm=llm, site_config=site_config)

    t0 = time.perf_counter()
    rewrite_out = rewriter.run(
        site_id=site_id, article_id=article_id,
        input_data={
            "article_id": str(article_id),
            "gsc_stats": {"position": "?", "impressions": "?", "ctr": 0},
            "old_qa_score": float(old_qa or 0),
        },
    )
    rewrite_secs = time.perf_counter() - t0
    print(f"✓ rewrite step done in {rewrite_secs:.1f}s")
    print(f"  words: {rewrite_out['old_word_count']} → {rewrite_out['new_word_count']}")
    print(f"  H2:    {rewrite_out['old_h2_count']} → {rewrite_out['new_h2_count']}")
    print(f"  target_min_words={rewrite_out['targets']['min_words']}")
    print(f"  target_h2_count ={rewrite_out['targets']['h2_count']}")
    print()
    print(f"  analysis: {len(rewrite_out['analysis'].get('missing_sections') or [])} missing, "
          f"{len(rewrite_out['analysis'].get('shallow_sections') or [])} shallow, "
          f"{len(rewrite_out['analysis'].get('stale_info') or [])} stale")
    print()

    # QA pass
    qa = QAAgent(llm=llm, site_config=site_config)
    t1 = time.perf_counter()
    qa_out = qa.run(
        site_id=site_id, article_id=article_id,
        input_data={
            "keyword": rewrite_out["primary_query"],
            "article_type": atype,
            "content_md": rewrite_out["new_content_md"],
            "outline": {},
            "word_count": rewrite_out["new_word_count"],
            "min_word_count": site_config["content_plan"]["min_word_count"],
            "max_word_count": site_config["content_plan"]["max_word_count"],
        },
    )
    qa_secs = time.perf_counter() - t1
    print(f"✓ QA step done in {qa_secs:.1f}s")
    print(f"  new qa_score: {qa_out['score']}  passed={qa_out['passed']}")
    print(f"  delta: {qa_out['score'] - float(old_qa or 0):+.2f}")

    # Sum cost
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select sum(cost_usd) from agent_runs where article_id = %s "
            "and agent_name in ('rewriter','qa') and created_at > now() - interval '5 minutes'",
            (str(article_id),),
        )
        cost = float(cur.fetchone()[0] or 0)
    print()
    print(f"💰 e2e cost ≈ ${cost:.4f}  (rewrite {rewrite_secs:.1f}s + qa {qa_secs:.1f}s)")
    print()
    print("--- first 600 chars of new content ---")
    print(rewrite_out["new_content_md"][:600])
    print("---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
