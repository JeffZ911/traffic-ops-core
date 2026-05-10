"""End-to-end: run the orchestrator on the ntecodex.com site, ONCE."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection
from src.pipeline.orchestrator import run_one_article


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main() -> int:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain = 'ntecodex.com' limit 1")
        row = cur.fetchone()
        if not row:
            print("❌ Run scripts/bootstrap_first_site.py first.")
            return 2
        site_id = row[0]

    print("=" * 78)
    print("=== End-to-End: run_one_article ===")
    print("=" * 78)
    print(f"site_id: {site_id}")

    start = time.perf_counter()
    summary = run_one_article(site_id)
    elapsed = time.perf_counter() - start

    # Tally cost & tokens from agent_runs for this article
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select agent_name, status,
                   coalesce(tokens_in, 0), coalesce(tokens_out, 0),
                   coalesce(cost_usd, 0), coalesce(duration_ms, 0)
              from agent_runs
             where article_id = %s
             order by created_at
            """,
            (summary["article_id"],),
        )
        runs = cur.fetchall()

        cur.execute(
            "select id from agent_runs where site_id = %s and article_id is null "
            "and agent_name = 'keyword_selector' order by created_at desc limit 1",
            (str(site_id),),
        )
        sel_run = cur.fetchone()

        sel_metrics = (0, 0, 0.0, 0)
        if sel_run:
            cur.execute(
                "select coalesce(tokens_in, 0), coalesce(tokens_out, 0), "
                "       coalesce(cost_usd, 0), coalesce(duration_ms, 0) "
                "from agent_runs where id = %s",
                (sel_run[0],),
            )
            sel_metrics = cur.fetchone()

    print()
    print("=== Per-Agent breakdown ===")
    print(f"  keyword_selector  status=success    "
          f"tokens={sel_metrics[0]}/{sel_metrics[1]}  "
          f"cost=${float(sel_metrics[2]):.6f}  duration={sel_metrics[3]}ms")
    total_in = sel_metrics[0]
    total_out = sel_metrics[1]
    total_cost = float(sel_metrics[2])
    total_ms = sel_metrics[3]

    for r in runs:
        agent_name, status, ti, to, c, d = r
        c_f = float(c)
        print(f"  {agent_name:18s} status={status:9s} "
              f"tokens={ti}/{to}  cost=${c_f:.6f}  duration={d}ms")
        total_in += ti
        total_out += to
        total_cost += c_f
        total_ms += d

    print()
    print("=== Totals ===")
    print(f"  Total tokens in/out  : {total_in} / {total_out}")
    print(f"  Total cost (DB sum)  : ${total_cost:.6f}")
    print(f"  Total agent duration : {total_ms}ms")
    print(f"  Wall clock           : {elapsed:.1f}s")
    print(f"  Final status         : {summary['final_status']}")

    qa = summary.get("qa") or {}
    print()
    print("=== QA result ===")
    print(f"  score: {qa.get('score')}")
    print(f"  passed: {qa.get('passed')}")
    fb = qa.get("feedback") or {}
    if fb:
        for dim in ("intent_match", "info_density", "structure", "ai_pattern", "seo"):
            if dim in fb:
                print(f"  {dim:14s}: {fb[dim]}")
        if fb.get("issues"):
            print("  issues:")
            for x in fb["issues"][:5]:
                print(f"     - {x}")
        if fb.get("suggestions"):
            print("  suggestions:")
            for x in fb["suggestions"][:5]:
                print(f"     - {x}")

    print()
    print("=== content_md preview (first 500 chars) ===")
    content = summary.get("content_md") or ""
    print(content[:500])
    print("..." if len(content) > 500 else "(end)")
    print()
    print(f"📝 article_id: {summary['article_id']}  ({summary.get('word_count')} words)")
    return 0 if summary["final_status"] == "qa_passed" else 1


if __name__ == "__main__":
    sys.exit(main())
