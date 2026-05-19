"""P0 backfill (2026-05-13): re-emit MD files for every `status='published'`
article whose target file is missing from ntecodex-site.

Why this exists
---------------
The previous content_daily.yml workflow had no `git commit + push` step.
For every cron, the workflow:
  1. Checked out a CLEAN copy of ntecodex-site.
  2. Generated .md files into src/content/.
  3. Deployed dist/ to Cloudflare Pages via wrangler.
  4. The workspace was destroyed.

So each cron's articles shipped exactly once (during that deploy), then
the next cron checked out the OLD repo state (without those files) and
clobbered the deploy. End state: DB shows ~111 published, but the site
repo only contains ~57 .md files, and the live sitemap only ~45 URLs.

This script closes the gap WITHOUT spending an LLM dollar:
  - Reads every article in articles where status='published'.
  - For each one, computes the expected MD path under src/content/.
  - If the file is missing OR --force: render the .md using exactly the
    same logic PublishAgent uses (frontmatter + banner + internal-links
    injection), then write it.
  - Does NOT touch the articles table (status is already 'published').

After this script runs, the operator commits + pushes the resulting tree
in one shot. The companion workflow fix (commit/push step) keeps this
problem from re-occurring on future crons.

Cost: $0. Pure SQL read + filesystem write.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.agents._internal_links import (
    build_keyword_lookup_from_articles,
    inject_internal_links,
)
from src.agents.publish import (
    PATH_BY_TYPE,
    URL_BY_TYPE,
    _emit_yaml,
    _inject_editorial_banner,
)
from src.db.client import get_db_connection


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _site_repo_path() -> Path:
    env = os.getenv("SITE_REPO_PATH")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    nested = here.parent.parent / "ntecodex-site"
    parent_sibling = here.parent.parent.parent / "ntecodex-site"
    return (nested if nested.exists() else parent_sibling).resolve()


SITE_REPO = _site_repo_path()


def _render(
    article: dict[str, Any],
    sources: list[dict],
    other_published: list[dict],
) -> tuple[Path, str] | None:
    """Mirror PublishAgent._execute's render logic. Returns
    (out_path, file_body) or None when this article has no template
    (unknown article_type)."""

    article_type = article["article_type"]
    slug = article["slug"]
    if article_type not in PATH_BY_TYPE:
        return None

    rel = PATH_BY_TYPE[article_type].format(slug=slug)
    out_path = SITE_REPO / "src" / "content" / rel

    # Reuse the URL already stored in the article row if present; this
    # is critical so the backfilled file's `published_url` matches what
    # Google has been crawling against and what _redirects already
    # encodes for legacy paths.
    url_pattern = URL_BY_TYPE[article_type]
    published_url = article.get("published_url") or url_pattern.format(slug=slug)

    # When the article was originally published we wrote `published_at`
    # into the row — preserve it. Falling back to now() would lie about
    # freshness to Google. Only synthesize when truly missing.
    pub_at = article.get("published_at") or datetime.now(timezone.utc)

    rel_without_collection = rel.split("/", 1)[1] if "/" in rel else rel
    entry_slug = (
        rel_without_collection[:-len(".md")]
        if rel_without_collection.endswith(".md") else rel_without_collection
    )

    outline_blob = article.get("outline") or {}
    game_slug = (
        (outline_blob.get("game") if isinstance(outline_blob, dict) else None)
        or "nte"
    )

    fm: dict[str, Any] = {
        "title": article["title"] or slug,
        "slug": entry_slug,
        "game": game_slug,
        "article_type": article_type,
        "qa_score": float(article["qa_score"] or 0),
        "word_count": int(article["word_count"] or 0),
        "published_at": pub_at.isoformat() if hasattr(pub_at, "isoformat") else str(pub_at),
        "published_url": published_url,
        "sources": [s.get("uri") for s in sources if isinstance(s, dict) and s.get("uri")],
    }
    if article_type == "character_db" and isinstance(article["outline"], dict):
        fm["character_data"] = article["outline"]

    content_md = article["content_md"] or ""

    # Internal links — feed every OTHER published article into the lookup.
    keyword_lookup = build_keyword_lookup_from_articles(
        [r for r in other_published if r.get("id") != article["id"]]
    )
    if keyword_lookup:
        content_md, _linked = inject_internal_links(
            content_md, keyword_lookup, self_url=published_url
        )

    # Editorial-tier banner (Phase 2.6).
    qa_fb = article.get("qa_feedback") or {}
    tier = (qa_fb.get("editorial_tier") if isinstance(qa_fb, dict) else None) or "clean"
    if tier in ("note", "strong"):
        date_iso = (pub_at.date().isoformat() if hasattr(pub_at, "date")
                    else datetime.now(timezone.utc).date().isoformat())
        content_md = _inject_editorial_banner(content_md, tier=tier, date_iso=date_iso)

    body = "---\n" + _emit_yaml(fm) + "\n---\n\n" + content_md + "\n"
    return out_path, body


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="Re-emit even if the target .md already exists")
    p.add_argument("--limit", type=int, default=500)
    args = p.parse_args()

    if not SITE_REPO.exists():
        print(f"❌ site repo not found at {SITE_REPO}")
        return 2

    site_domain = os.getenv("SITE_DOMAIN", "ntecodex.com")
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select id from sites where domain = %s limit 1", (site_domain,)
        )
        site_row = cur.fetchone()
        if not site_row:
            print(f"❌ site {site_domain!r} not found in sites table")
            return 2
        site_id = site_row[0]

        # All published articles. We don't gate on article_type here —
        # _render returns None for unknown types and we count them.
        cur.execute(
            """
            select id::text, slug, title, article_type, qa_score, word_count,
                   outline, qa_feedback, content_md, published_url, published_at
              from articles
             where site_id = %s and status = 'published'
             order by published_at nulls last
             limit %s
            """,
            (str(site_id), args.limit),
        )
        cols = [d.name for d in cur.description]
        articles = [dict(zip(cols, r)) for r in cur.fetchall()]

        # All published article metadata for internal-link lookup.
        cur.execute(
            """
            select id::text, title, published_url, article_type, outline
              from articles
             where site_id = %s and status = 'published'
            """,
            (str(site_id),),
        )
        link_cols = [d.name for d in cur.description]
        other_published = [dict(zip(link_cols, r)) for r in cur.fetchall()]

        # Sources per article from agent_runs (single query, group by id).
        ids = [a["id"] for a in articles]
        sources_by_id: dict[str, list] = {}
        if ids:
            cur.execute(
                """
                select distinct on (article_id)
                       article_id::text, output->'_sources'
                  from agent_runs
                 where article_id = any(%s::uuid[])
                   and agent_name = 'writing'
                   and status = 'success'
                 order by article_id, created_at desc
                """,
                (ids,),
            )
            for art_id, src in cur.fetchall():
                sources_by_id[art_id] = src or []

    print(f"📚 {len(articles)} published articles in DB; site repo at {SITE_REPO}")

    written = 0
    skipped_exists = 0
    skipped_no_content = 0
    skipped_unknown_type = 0
    paths_by_dir: dict[str, int] = {}

    for art in articles:
        if not art["content_md"]:
            skipped_no_content += 1
            continue
        rendered = _render(art, sources_by_id.get(art["id"], []), other_published)
        if rendered is None:
            skipped_unknown_type += 1
            continue
        out_path, body = rendered

        if out_path.exists() and not args.force:
            skipped_exists += 1
            continue

        if args.dry_run:
            print(f"  [dry] would write {out_path.relative_to(SITE_REPO)}")
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(body, encoding="utf-8")
            print(f"  ✓ {out_path.relative_to(SITE_REPO)}  "
                  f"({len(body)} bytes, tier={(art.get('qa_feedback') or {}).get('editorial_tier', 'clean')})")
        top = out_path.relative_to(SITE_REPO / "src" / "content").parent.as_posix()
        paths_by_dir[top] = paths_by_dir.get(top, 0) + 1
        written += 1

    print()
    print("=== Sync summary ===")
    print(f"  written (or would-write):  {written}")
    print(f"  skipped — file exists:     {skipped_exists}")
    print(f"  skipped — no content_md:   {skipped_no_content}")
    print(f"  skipped — unknown type:    {skipped_unknown_type}")
    print()
    print("=== Path distribution ===")
    for d, n in sorted(paths_by_dir.items()):
        print(f"  src/content/{d}/  → {n}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
