"""PublishAgent — write a qa_passed article to the site repo as a Markdown file.

Per SITE-STRUCTURE.md §2 the destination directory depends on article_type.
Phase 1.A scope: write the file only. git push / GSC / deploy polling are
deferred until the Astro template is ready.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from src.agents.base import BaseAgent
from src.db.client import get_db_connection


# article_type → relative path under <site_repo>/src/content/
# tier_list and faq aggregate into a single page per SITE-STRUCTURE §2; we
# stash their source in a separate folder so the aggregator can scan them.
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

# Public URL pattern (for articles.published_url). Aggregated types map to
# the aggregation page rather than a per-slug URL.
URL_BY_TYPE: dict[str, str] = {
    "build":        "/guides/{slug}",
    "comparison":   "/guides/{slug}",
    "boss_guide":   "/boss/{slug}",
    "reroll":       "/guides/reroll/{slug}",
    "character_db": "/characters/{slug}",
    "weapon_db":    "/weapons/{slug}",
    "news":         "/news/{slug}",
    "tier_list":    "/tier-list",   # aggregated page
    "faq":          "/faq",         # aggregated page
}


def _yaml_escape(s: str) -> str:
    """Quote a YAML scalar that may contain unsafe chars."""
    if any(c in s for c in (':', '#', '\n', '"', '\'')) or s.strip() != s:
        return json.dumps(s, ensure_ascii=False)
    return s


def _emit_yaml(d: dict) -> str:
    """Tiny YAML emitter for our flat-ish frontmatter shape."""
    out: list[str] = []
    for k, v in d.items():
        if v is None:
            out.append(f"{k}: null")
        elif isinstance(v, bool):
            out.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            out.append(f"{k}: {v}")
        elif isinstance(v, str):
            out.append(f"{k}: {_yaml_escape(v)}")
        elif isinstance(v, list):
            if not v:
                out.append(f"{k}: []")
            elif all(isinstance(x, str) for x in v):
                out.append(f"{k}:")
                for x in v:
                    out.append(f"  - {_yaml_escape(x)}")
            else:
                out.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        elif isinstance(v, dict):
            out.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        else:
            out.append(f"{k}: {json.dumps(str(v), ensure_ascii=False)}")
    return "\n".join(out)


class PublishAgent(BaseAgent):
    name = "publish"
    task_type = "publish"     # not in site_config; this Agent does no LLM calls
    max_retries = 0           # filesystem ops; retries here would mask bugs

    def __init__(self, llm, site_config, *, site_repo_path: Path):
        super().__init__(llm=llm, site_config=site_config)
        self.site_repo_path = Path(site_repo_path)

    def _execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        article_id = UUID(input_data["article_id"])

        # Load the article + its writing-agent sources from agent_runs
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "select id, slug, title, article_type, status, content_md, "
                "       qa_score, word_count, outline "
                "from articles where id = %s",
                (str(article_id),),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError(f"article not found: {article_id}")
            cols = [d.name for d in cur.description]
            article = dict(zip(cols, row))

            cur.execute(
                """
                select output->'_sources' from agent_runs
                 where article_id = %s and agent_name = 'writing'
                       and status = 'success'
                 order by created_at desc limit 1
                """,
                (str(article_id),),
            )
            src_row = cur.fetchone()
            sources = src_row[0] if src_row and src_row[0] else []

        if article["status"] != "qa_passed":
            raise RuntimeError(
                f"refusing to publish article in status={article['status']!r}; "
                f"expected qa_passed"
            )

        article_type = article["article_type"]
        slug = article["slug"]
        if article_type not in PATH_BY_TYPE:
            raise ValueError(f"unknown article_type: {article_type}")

        rel = PATH_BY_TYPE[article_type].format(slug=slug)
        out_path = self.site_repo_path / "src" / "content" / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)

        url_pattern = URL_BY_TYPE[article_type]
        published_url = url_pattern.format(slug=slug)
        published_at = datetime.now(timezone.utc)

        # Build frontmatter
        fm: dict[str, Any] = {
            "title": article["title"] or slug,
            "slug": slug,
            "article_type": article_type,
            "qa_score": float(article["qa_score"] or 0),
            "word_count": int(article["word_count"] or 0),
            "published_at": published_at.isoformat(),
            "published_url": published_url,
            "sources": [s.get("uri") for s in sources if s.get("uri")],
        }
        # character_db: surface the structured outline so Astro template can render cards
        if article_type == "character_db" and isinstance(article["outline"], dict):
            fm["character_data"] = article["outline"]

        body = (
            "---\n"
            + _emit_yaml(fm)
            + "\n---\n\n"
            + (article["content_md"] or "")
            + "\n"
        )
        out_path.write_text(body, encoding="utf-8")

        # Update articles row
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                update articles
                   set status = 'published',
                       published_url = %s,
                       published_at = %s
                 where id = %s
                """,
                (published_url, published_at, str(article_id)),
            )

        return {
            "article_id": str(article_id),
            "file_path": str(out_path.relative_to(self.site_repo_path)),
            "absolute_path": str(out_path),
            "published_url": published_url,
            "bytes_written": len(body.encode("utf-8")),
            "source_count": len(fm["sources"]),
        }
