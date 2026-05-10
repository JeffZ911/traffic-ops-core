"""Publish all qa_passed articles to ntecodex-site/src/content/.

No git push (Phase 1.A scope: write files only, leave staging to operator).
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from src.agents.publish import PublishAgent
from src.db.client import get_db_connection
from src.utils.llm import get_llm_provider


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ntecodex-site lives next to traffic-ops-core under traffic-ops/
SITE_REPO = (
    Path(__file__).resolve().parent.parent.parent / "ntecodex-site"
).resolve()


def main() -> int:
    if not SITE_REPO.exists():
        print(f"❌ site repo not found at {SITE_REPO}")
        return 2

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select s.id, s.config from sites s where s.domain = 'ntecodex.com' limit 1"
        )
        site_row = cur.fetchone()
        if not site_row:
            print("❌ ntecodex.com site not found")
            return 2
        site_id, config = site_row

        cur.execute(
            "select id, slug, article_type, qa_score from articles "
            "where site_id = %s and status = 'qa_passed' "
            "order by created_at",
            (str(site_id),),
        )
        rows = cur.fetchall()

    if not rows:
        print("ℹ️  No qa_passed articles to publish.")
        return 0

    print(f"📚 {len(rows)} qa_passed articles → publishing to {SITE_REPO}")
    print()

    llm = get_llm_provider("gemini")  # PublishAgent doesn't use it but BaseAgent expects it
    agent = PublishAgent(llm=llm, site_config=config, site_repo_path=SITE_REPO)

    paths_by_dir: dict[str, list[str]] = {}
    for article_id, slug, atype, qa in rows:
        try:
            result = agent.run(
                site_id=site_id, article_id=article_id,
                input_data={"article_id": str(article_id)},
            )
            rel = result["file_path"]
            top_dir = rel.split("/", 2)[2] if rel.startswith("src/content/") else rel
            top_dir = top_dir.rsplit("/", 1)[0] if "/" in top_dir else top_dir
            paths_by_dir.setdefault(top_dir, []).append(rel)
            print(f"  ✅ {atype:14s}  {slug:40s} → {rel}  "
                  f"({result['source_count']} sources)")
        except Exception as e:
            print(f"  ❌ {atype:14s}  {slug:40s} → FAILED: {e}")

    print()
    print("=== Path distribution (vs SITE-STRUCTURE §2) ===")
    for d, lst in sorted(paths_by_dir.items()):
        print(f"  src/content/{d}/  → {len(lst)} file(s)")
        for f in lst:
            print(f"     - {f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
