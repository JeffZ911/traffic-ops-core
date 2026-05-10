"""
Pipeline orchestrator: end-to-end "write one article" flow.

Composes KeywordSelector → Outline → Writing → QA, with status transitions
on `articles` and `keywords`. Each Agent does its own retry; this layer
handles the QA-rewrite loop and final status bookkeeping.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from uuid import UUID, uuid4

from src.agents.keyword_selector import KeywordSelectorAgent
from src.agents.outline import OutlineAgent
from src.agents.qa import QAAgent
from src.agents.writing import WritingAgent
from src.db.client import get_db_connection
from src.utils.llm import get_llm_provider


# ---------------------------------------------------------------- helpers


def _slugify(text: str, max_len: int = 60) -> str:
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:max_len] or "article"


def _load_site(site_id: UUID) -> dict[str, Any]:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select config from sites where id = %s", (str(site_id),))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"site_id {site_id} not found")
        return row[0]


def _create_article(
    site_id: UUID, slug: str, title: str, article_type: str
) -> UUID:
    article_id = uuid4()
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into articles (id, site_id, slug, title, article_type, status)
            values (%s, %s, %s, %s, %s, 'draft')
            """,
            (str(article_id), str(site_id), slug, title, article_type),
        )
    return article_id


def _set_article(article_id: UUID, **fields: Any) -> None:
    if not fields:
        return
    set_clauses = []
    params: list[Any] = []
    for k, v in fields.items():
        if k in ("outline", "qa_feedback") and v is not None:
            set_clauses.append(f"{k} = %s::jsonb")
            params.append(json.dumps(v))
        else:
            set_clauses.append(f"{k} = %s")
            params.append(v)
    params.append(str(article_id))
    sql = f"update articles set {', '.join(set_clauses)} where id = %s"
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)


def _set_keyword_status(keyword_id: UUID, status: str) -> None:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "update keywords set status = %s, last_used_at = now() where id = %s",
            (status, str(keyword_id)),
        )


def _link_article_keyword(article_id: UUID, keyword_id: UUID) -> None:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into article_keywords (article_id, keyword_id, is_primary)
            values (%s, %s, true)
            on conflict (article_id, keyword_id) do nothing
            """,
            (str(article_id), str(keyword_id)),
        )


# ----------------------------------------------------------------- run


def run_one_article(
    site_id: UUID,
    *,
    log=print,
    max_retry_rounds_override: Optional[int] = None,
) -> dict[str, Any]:
    """
    Execute the full pipeline for ONE article. Returns a summary dict.

    Status flow on `articles`:
      draft → writing → qa_pending → qa_passed | qa_failed | failed
    Status flow on `keywords`:
      planned → in_progress → completed (success) | planned (qa_failed)
    """
    site_config = _load_site(site_id)
    llm = get_llm_provider("gemini")

    summary: dict[str, Any] = {
        "site_id": str(site_id),
        "stages": [],
    }

    # ------------------------------------------------------ Step 1: select
    log("\n=== Step 1: KeywordSelectorAgent ===")
    selector = KeywordSelectorAgent(llm=llm, site_config=site_config)
    sel = selector.run(
        site_id=site_id, article_id=None,
        input_data={"site_id": str(site_id)},
    )
    keyword_id = UUID(sel["keyword_id"])
    keyword = sel["keyword_text"]
    article_type = sel["article_type"]
    log(f"  Selected: {keyword!r} → {article_type}  (reason: {sel.get('reason')})")
    summary["stages"].append({"agent": "keyword_selector", **sel})
    summary["keyword_id"] = str(keyword_id)
    summary["keyword"] = keyword
    summary["article_type"] = article_type

    _set_keyword_status(keyword_id, "in_progress")

    # ----------------------------------------------------- Step 2: article row
    target_words = (
        site_config["content_plan"]["min_word_count"]
        + site_config["content_plan"]["max_word_count"]
    ) // 2
    initial_slug = _slugify(keyword)
    article_id = _create_article(
        site_id=site_id, slug=initial_slug, title=keyword, article_type=article_type,
    )
    _link_article_keyword(article_id, keyword_id)
    log(f"  Created articles row id={article_id}  slug={initial_slug}")
    summary["article_id"] = str(article_id)

    try:
        # -------------------------------------------------- Step 3: outline
        log("\n=== Step 2: OutlineAgent ===")
        outline_agent = OutlineAgent(llm=llm, site_config=site_config)
        outline = outline_agent.run(
            site_id=site_id, article_id=article_id,
            input_data={
                "keyword": keyword, "article_type": article_type,
                "target_word_count": target_words,
            },
        )
        log(f"  Title: {outline.get('title')}")
        log(f"  Slug:  {outline.get('slug')}")
        log(f"  Sections: {[s.get('h2') for s in outline.get('sections', [])]}")
        summary["stages"].append({"agent": "outline", "title": outline.get("title")})
        _set_article(
            article_id,
            title=outline.get("title") or keyword,
            slug=outline.get("slug") or initial_slug,
            outline=outline,
            status="writing",
        )

        # -------------------------------------------------- Step 4: write + QA loop
        max_retry_rounds = int(site_config["qa_thresholds"].get("max_retry_rounds", 3))
        if max_retry_rounds_override is not None:
            max_retry_rounds = max_retry_rounds_override
        min_words = site_config["content_plan"]["min_word_count"]
        max_words = site_config["content_plan"]["max_word_count"]

        feedback: Optional[dict[str, Any]] = None
        qa_attempts = 0
        final_qa: Optional[dict[str, Any]] = None
        write_output: Optional[dict[str, Any]] = None

        while True:
            log(f"\n=== Step 3: WritingAgent (attempt {qa_attempts + 1}) ===")
            writer = WritingAgent(llm=llm, site_config=site_config)
            write_output = writer.run(
                site_id=site_id, article_id=article_id,
                input_data={
                    "keyword": keyword, "article_type": article_type,
                    "outline": outline,
                    "min_word_count": min_words, "max_word_count": max_words,
                    "qa_feedback": feedback,
                },
            )
            wc = write_output["word_count"]
            log(f"  Wrote {wc} words")
            summary["stages"].append({"agent": "writing", "word_count": wc})
            _set_article(
                article_id,
                content_md=write_output["content_md"],
                word_count=wc,
                status="qa_pending",
                qa_attempts=qa_attempts + 1,
            )

            log(f"\n=== Step 4: QAAgent (attempt {qa_attempts + 1}) ===")
            qa_agent = QAAgent(llm=llm, site_config=site_config)
            qa_result = qa_agent.run(
                site_id=site_id, article_id=article_id,
                input_data={
                    "keyword": keyword, "article_type": article_type,
                    "content_md": write_output["content_md"],
                    "outline": outline, "word_count": wc,
                    "min_word_count": min_words, "max_word_count": max_words,
                },
            )
            score = float(qa_result["score"])
            passed = bool(qa_result["passed"])
            log(f"  QA score={score}  passed={passed}")
            summary["stages"].append({"agent": "qa", "score": score, "passed": passed})
            final_qa = qa_result

            _set_article(
                article_id,
                qa_score=score,
                qa_feedback=qa_result.get("feedback"),
            )

            if passed:
                _set_article(article_id, status="qa_passed")
                _set_keyword_status(keyword_id, "completed")
                summary["final_status"] = "qa_passed"
                summary["qa"] = qa_result
                summary["content_md"] = write_output["content_md"]
                summary["word_count"] = wc
                return summary

            qa_attempts += 1
            feedback = qa_result.get("feedback")
            if qa_attempts >= max_retry_rounds:
                log(f"  QA failed and exhausted {max_retry_rounds} retries → qa_failed")
                _set_article(article_id, status="qa_failed")
                _set_keyword_status(keyword_id, "planned")  # back to pool
                summary["final_status"] = "qa_failed"
                summary["qa"] = qa_result
                summary["content_md"] = write_output["content_md"]
                summary["word_count"] = wc
                return summary

    except Exception as e:
        _set_article(article_id, status="failed", failure_reason=str(e)[:1000])
        _set_keyword_status(keyword_id, "planned")
        summary["final_status"] = "failed"
        summary["error"] = f"{type(e).__name__}: {e}"
        raise
