"""Pick ONE rewrite candidate and (maybe) replace its published markdown.

This is the orchestration tier above `RewriterAgent`. It runs:

  1. Pick a candidate (from the latest seo_intelligence weekly report,
     OR a CLI-supplied --article-id override).
  2. Run RewriterAgent → new markdown + analysis output.
  3. Run QAAgent on the new markdown.
  4. If new_qa_score > old_qa_score + 0.5 AND QA passed, swap:
       articles.content_md = new
       articles.word_count = new
       articles.qa_score   = new
       articles.qa_feedback merged + rewrite_history appended
     Then overwrite the markdown file under <SITE_REPO_PATH>/src/content/...
  5. If the rewrite doesn't beat the threshold, increment
     `qa_feedback.rewrite_attempts`. Three failures in a row → set
     `qa_feedback.rewrite_skipped = true` so future cron runs skip this
     article entirely.

Designed to be invoked by the daily cron as the last cost-incurring step,
budget-gated to `action in (normal, warn)`. Single-article-per-day cap
keeps cost bounded.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv

from src.agents.rewriter import RewriterAgent
from src.agents.qa import QAAgent
from src.db.client import get_db_connection
from src.utils.llm import get_llm_provider


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# Mirrors PublishAgent.PATH_BY_TYPE — kept inline so this script can run
# without importing the agent (avoids a circular-ish dependency).
PATH_BY_TYPE: dict[str, str] = {
    "build":        "guides/{slug}.md",
    "comparison":   "guides/{slug}.md",
    "boss_guide":   "boss/{slug}.md",
    "reroll":       "guides/reroll/{slug}.md",
    "character_db": "characters/{slug}.md",
    "weapon_db":    "weapons/{slug}.md",
    "news":         "news/{slug}.md",
    "tier_list":    "tier-list-source/{slug}.md",
    "faq":          "faq-source/{slug}.md",
}

MAX_REWRITE_ATTEMPTS = 3            # safety: after 3 failures, skip forever
SCORE_IMPROVEMENT_MIN = 0.5         # new_qa - old_qa must clear this


# --------------------------------------------------------- candidate select


def _latest_seo_intel_rewrite_candidates(
    cur, site_id: UUID
) -> list[dict[str, Any]]:
    """Read the most recent seo_intelligence_weekly payload from daily_reports.
    Returns the embedded rewrite_candidates list, or [] if none."""
    cur.execute(
        """
        select data_snapshot
          from daily_reports
         where site_id = %s
           and data_snapshot ? 'seo_intelligence'
         order by report_date desc
         limit 1
        """,
        (str(site_id),),
    )
    row = cur.fetchone()
    if not row:
        return []
    snap = row[0] or {}
    intel = snap.get("seo_intelligence") or {}
    return intel.get("rewrite_candidates") or []


def _pick_next_article(
    cur, site_id: UUID
) -> tuple[UUID, dict[str, Any]] | None:
    """Find one (article_id, gsc_stats) tuple suitable for rewrite.

    Skips articles whose qa_feedback flags rewrite_skipped=true or whose
    rewrite_attempts is already >= MAX_REWRITE_ATTEMPTS.
    """
    candidates = _latest_seo_intel_rewrite_candidates(cur, site_id)
    if not candidates:
        return None
    for c in candidates:
        aid_str = c.get("article_id")
        if not aid_str:
            continue
        cur.execute(
            "select qa_feedback from articles where id = %s and status = 'published'",
            (aid_str,),
        )
        row = cur.fetchone()
        if not row:
            continue
        fb = row[0] or {}
        if fb.get("rewrite_skipped"):
            continue
        if int(fb.get("rewrite_attempts") or 0) >= MAX_REWRITE_ATTEMPTS:
            # promote skipped flag now so we don't re-evaluate forever
            cur.execute(
                """
                update articles
                   set qa_feedback = coalesce(qa_feedback, '{}'::jsonb)
                                  || jsonb_build_object('rewrite_skipped', true)
                 where id = %s
                """,
                (aid_str,),
            )
            continue
        gsc_stats = {
            "impressions": c.get("impressions"),
            "clicks": c.get("clicks"),
            "ctr": c.get("ctr"),
            "position": c.get("position"),
            "url": c.get("url"),
        }
        return UUID(aid_str), gsc_stats
    return None


# --------------------------------------------------------- post-rewrite ops


def _write_markdown(
    site_repo: Path, article: dict[str, Any], new_content_md: str
) -> Path | None:
    rel = PATH_BY_TYPE.get(article["article_type"], "").format(slug=article["slug"])
    if not rel:
        return None
    md_path = site_repo / "src" / "content" / rel
    if not md_path.exists():
        print(f"⚠️  markdown not on disk: {md_path}")
        return None
    text = md_path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        print(f"⚠️  no frontmatter in {md_path}; skipping body swap")
        return None
    fm_block = m.group(0)        # includes both --- delimiters and trailing \n
    md_path.write_text(fm_block + new_content_md + "\n", encoding="utf-8")
    return md_path


def _notify(subject: str, body: str, severity: str = "info") -> None:
    try:
        from src.utils.send_alert import send_alert
        send_alert(subject=subject, body=body, severity=severity)
    except Exception as e:
        print(f"⚠️  alert send failed: {e}")


# --------------------------------------------------------------- main


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--article-id", default=None,
                   help="Override candidate selection; rewrite this article")
    p.add_argument("--budget-usd", type=float, default=1.0,
                   help="Hard cap; abort the script before LLM calls if "
                        "we estimate we'd exceed it (currently informational)")
    p.add_argument("--dry-run", action="store_true",
                   help="Run analysis + rewrite + QA, but do NOT persist")
    args = p.parse_args()

    site_repo = Path(os.getenv("SITE_REPO_PATH", "")).resolve() if os.getenv("SITE_REPO_PATH") else None

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id, config from sites where domain = 'ntecodex.com' limit 1")
        row = cur.fetchone()
        if not row:
            print("❌ ntecodex.com not in sites")
            return 2
        site_id, site_config = row

        if args.article_id:
            article_uuid = UUID(args.article_id)
            cur.execute(
                "select id, qa_feedback from articles where id = %s",
                (str(article_uuid),),
            )
            r = cur.fetchone()
            if not r:
                print(f"❌ article {args.article_id} not found")
                return 2
            gsc_stats = {"position": "?", "impressions": "?", "ctr": 0}
        else:
            pick = _pick_next_article(cur, site_id)
            if not pick:
                print("ℹ️  no rewrite candidates available — skip")
                return 0
            article_uuid, gsc_stats = pick

        cur.execute(
            "select slug, title, article_type, qa_score, qa_feedback "
            "from articles where id = %s",
            (str(article_uuid),),
        )
        a_row = cur.fetchone()
        article = dict(zip(
            ["slug", "title", "article_type", "qa_score", "qa_feedback"], a_row
        ))
        old_qa_score = float(article["qa_score"] or 0)
        existing_fb = article["qa_feedback"] or {}

    print(f"📝 Rewrite candidate: {article['slug']}")
    print(f"   type={article['article_type']}  old_qa={old_qa_score}  "
          f"prior_attempts={existing_fb.get('rewrite_attempts', 0)}")
    print(f"   gsc: pos={gsc_stats.get('position')}  "
          f"imp={gsc_stats.get('impressions')}  "
          f"ctr={gsc_stats.get('ctr')}")

    # ----- 1. Run RewriterAgent
    llm = get_llm_provider("gemini")
    rewriter = RewriterAgent(llm=llm, site_config=site_config)
    try:
        rewrite_out = rewriter.run(
            site_id=site_id,
            article_id=article_uuid,
            input_data={
                "article_id": str(article_uuid),
                "gsc_stats": gsc_stats,
                "old_qa_score": old_qa_score,
            },
        )
    except Exception as e:
        print(f"❌ Rewriter crashed: {type(e).__name__}: {e}")
        # don't bump attempts on crash — Rewriter's BaseAgent already
        # logged the failure into agent_runs
        return 1

    print(f"   ✓ rewrite produced {rewrite_out['new_word_count']} words "
          f"({rewrite_out['old_word_count']} → {rewrite_out['new_word_count']}), "
          f"H2 {rewrite_out['old_h2_count']} → {rewrite_out['new_h2_count']}")

    # ----- 2. QAAgent on the new content
    qa = QAAgent(llm=llm, site_config=site_config)
    try:
        qa_out = qa.run(
            site_id=site_id,
            article_id=article_uuid,
            input_data={
                "keyword": rewrite_out["primary_query"],
                "article_type": article["article_type"],
                "content_md": rewrite_out["new_content_md"],
                "outline": existing_fb.get("outline") or {},   # may be empty
                "word_count": rewrite_out["new_word_count"],
                "min_word_count": site_config["content_plan"]["min_word_count"],
                "max_word_count": site_config["content_plan"]["max_word_count"],
            },
        )
    except Exception as e:
        print(f"❌ QA on rewrite crashed: {type(e).__name__}: {e}")
        return 1

    new_qa_score = float(qa_out.get("score") or 0)
    qa_passed = bool(qa_out.get("passed"))
    print(f"   QA new_score={new_qa_score}  passed={qa_passed}  "
          f"(was {old_qa_score}, delta {new_qa_score - old_qa_score:+.2f})")

    delta_ok = new_qa_score - old_qa_score > SCORE_IMPROVEMENT_MIN
    accept = qa_passed and delta_ok

    # ----- 3. Persist (or fail-bookkeeping)
    if args.dry_run:
        print("   --dry-run set; not persisting")
        return 0

    prior_attempts = int(existing_fb.get("rewrite_attempts") or 0)
    new_attempts = prior_attempts + 1
    rewrite_skipped = False
    if not accept and new_attempts >= MAX_REWRITE_ATTEMPTS:
        rewrite_skipped = True

    rewrite_history_entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "old_qa": old_qa_score,
        "new_qa": new_qa_score,
        "accepted": accept,
        "old_words": rewrite_out["old_word_count"],
        "new_words": rewrite_out["new_word_count"],
    }

    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        if accept:
            cur.execute(
                """
                update articles
                   set content_md = %s,
                       word_count = %s,
                       qa_score   = %s,
                       qa_feedback = (
                         coalesce(qa_feedback, '{}'::jsonb)
                         || %s::jsonb
                       )
                 where id = %s
                """,
                (
                    rewrite_out["new_content_md"],
                    rewrite_out["new_word_count"],
                    new_qa_score,
                    json.dumps({
                        "rewrite_attempts": new_attempts,
                        "rewrite_skipped": False,
                        "rewrite_history": [rewrite_history_entry],   # caller may append
                    }),
                    str(article_uuid),
                ),
            )
        else:
            cur.execute(
                """
                update articles
                   set qa_feedback = (
                         coalesce(qa_feedback, '{}'::jsonb)
                         || %s::jsonb
                       )
                 where id = %s
                """,
                (
                    json.dumps({
                        "rewrite_attempts": new_attempts,
                        "rewrite_skipped": rewrite_skipped,
                        "last_rewrite_attempt": rewrite_history_entry,
                    }),
                    str(article_uuid),
                ),
            )

    # ----- 4. Re-write the markdown file on disk (only on accept)
    md_path = None
    if accept and site_repo:
        md_path = _write_markdown(site_repo, article, rewrite_out["new_content_md"])
        if md_path:
            print(f"   ✓ wrote new markdown: {md_path.relative_to(site_repo)}")

    # ----- 5. Email
    if accept:
        _notify(
            subject=f"[ntecodex] article rewritten: {article['slug']} "
                    f"qa {old_qa_score}→{new_qa_score}",
            body=(
                f"Article: {article['slug']}\n"
                f"Type: {article['article_type']}\n"
                f"Primary query: {rewrite_out['primary_query']}\n"
                f"QA score: {old_qa_score} → {new_qa_score} (Δ {new_qa_score-old_qa_score:+.2f})\n"
                f"Word count: {rewrite_out['old_word_count']} → {rewrite_out['new_word_count']}\n"
                f"H2 count: {rewrite_out['old_h2_count']} → {rewrite_out['new_h2_count']}\n"
                f"GSC pre-rewrite: pos={gsc_stats.get('position')} "
                f"imp={gsc_stats.get('impressions')}\n"
            ),
            severity="info",
        )
    elif rewrite_skipped:
        _notify(
            subject=f"[ntecodex] rewrite_skipped after {new_attempts} attempts: {article['slug']}",
            body=(
                f"Article {article['slug']} hit MAX_REWRITE_ATTEMPTS={MAX_REWRITE_ATTEMPTS}. "
                f"Marked rewrite_skipped=true. Manual review encouraged."
            ),
            severity="warning",
        )

    return 0 if accept else 0      # exit 0 either way; cron logs detail


if __name__ == "__main__":
    sys.exit(main())
