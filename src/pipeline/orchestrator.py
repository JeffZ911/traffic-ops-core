"""
Pipeline orchestrator: end-to-end "write one article" flow.

Composes KeywordSelector → Outline → Writing → QA, with status transitions
on `articles` and `keywords`. Each Agent does its own retry; this layer
handles the QA-rewrite loop and final status bookkeeping.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import psycopg

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


# Maximum collision-retry rounds. Attempt 0 uses the raw slug;
# attempt 1 appends a date suffix; attempts 2-3 append date + random.
SLUG_COLLISION_MAX_RETRIES = 3


def _date_suffix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _random_suffix(n: int = 4) -> str:
    # 4 chars of base32-ish — short, URL-safe, plenty of collision room
    return secrets.token_hex(n // 2 + 1)[:n]


def _candidate_slug(base: str, attempt: int) -> str:
    """Return the slug to try on a given retry round.

    Attempt 0 → base
    Attempt 1 → base-YYYYMMDD
    Attempt 2 → base-YYYYMMDD-aaaa
    Attempt 3 → base-YYYYMMDD-aaaa-bbbb   (extra random chunk)
    """
    if attempt == 0:
        return base
    suffix = "-" + _date_suffix()
    if attempt >= 2:
        suffix += "-" + _random_suffix()
    if attempt >= 3:
        suffix += "-" + _random_suffix()
    # Re-clamp length: total can't exceed _slugify's max_len + suffix budget
    return (base + suffix)[:80]


def _record_slug_rename(
    site_id: UUID,
    article_id: Optional[UUID],
    intended: str,
    final: str,
    attempts: int,
) -> None:
    """Log a slug auto-rename to `alerts` so the operator can audit."""
    try:
        with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into alerts
                  (site_id, level, source, message, payload)
                values (%s, 'info', 'orchestrator',
                        %s, %s::jsonb)
                """,
                (
                    str(site_id),
                    f"slug auto-renamed from {intended!r} to {final!r} "
                    f"after {attempts} collision(s)",
                    json.dumps({
                        "article_id": str(article_id) if article_id else None,
                        "intended_slug": intended,
                        "final_slug": final,
                        "attempts": attempts,
                    }),
                ),
            )
    except Exception:
        # `alerts` table may have a different shape on older deployments,
        # or RLS may bite. Never block a successful pipeline on a logging
        # failure — the rename itself already succeeded.
        pass


def _load_site(site_id: UUID) -> dict[str, Any]:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select config from sites where id = %s", (str(site_id),))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"site_id {site_id} not found")
        return row[0]


def _create_article(
    site_id: UUID, slug: str, title: str, article_type: str
) -> tuple[UUID, str]:
    """INSERT a new articles row. On UniqueViolation on (site_id, slug),
    retry with progressively-disambiguated slugs (date, random suffixes)
    up to SLUG_COLLISION_MAX_RETRIES times.

    Returns (article_id, final_slug). final_slug may differ from the
    input slug if collisions occurred; the orchestrator's `summary` and
    later `_set_article` calls must use the returned slug.
    """
    intended = slug
    last_exc: Exception | None = None
    for attempt in range(SLUG_COLLISION_MAX_RETRIES + 1):
        article_id = uuid4()
        candidate = _candidate_slug(intended, attempt)
        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    insert into articles (id, site_id, slug, title, article_type, status)
                    values (%s, %s, %s, %s, %s, 'draft')
                    """,
                    (str(article_id), str(site_id), candidate, title, article_type),
                )
            if attempt > 0:
                _record_slug_rename(site_id, article_id, intended, candidate, attempt)
            return article_id, candidate
        except psycopg.errors.UniqueViolation as e:
            last_exc = e
            continue
    raise RuntimeError(
        f"slug collision retries exhausted for {intended!r}: {last_exc}"
    )


def _set_article(article_id: UUID, **fields: Any) -> dict[str, Any]:
    """UPDATE an articles row. When `slug` is among the fields, the
    UNIQUE(site_id, slug) constraint can fire; we retry with date /
    random suffixes the same way _create_article does.

    Returns a small audit dict: {"final_slug": <slug-that-stuck-or-None>}.
    Callers that pass a slug should treat the return value's
    final_slug as authoritative (it may differ from what they passed).
    """
    if not fields:
        return {"final_slug": None}

    intended_slug = fields.get("slug")
    max_attempts = (
        SLUG_COLLISION_MAX_RETRIES + 1 if intended_slug else 1
    )
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        # Compute the slug for this attempt
        attempt_fields = dict(fields)
        if intended_slug:
            attempt_fields["slug"] = _candidate_slug(intended_slug, attempt)

        set_clauses = []
        params: list[Any] = []
        for k, v in attempt_fields.items():
            if k in ("outline", "qa_feedback") and v is not None:
                set_clauses.append(f"{k} = %s::jsonb")
                params.append(json.dumps(v))
            else:
                set_clauses.append(f"{k} = %s")
                params.append(v)
        params.append(str(article_id))
        sql = f"update articles set {', '.join(set_clauses)} where id = %s"
        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(sql, params)
            final_slug = attempt_fields.get("slug")
            if intended_slug and attempt > 0:
                # We don't have a site_id here directly; alerts.site_id is
                # nullable per the schema? If not, we look it up.
                try:
                    with get_db_connection() as conn, conn.cursor() as cur:
                        cur.execute(
                            "select site_id from articles where id = %s",
                            (str(article_id),),
                        )
                        r = cur.fetchone()
                        site_id_val = r[0] if r else None
                except Exception:
                    site_id_val = None
                if site_id_val:
                    _record_slug_rename(
                        site_id_val, article_id, intended_slug, final_slug, attempt
                    )
            return {"final_slug": final_slug}
        except psycopg.errors.UniqueViolation as e:
            last_exc = e
            if not intended_slug:
                # Collision on a non-slug field? Re-raise immediately —
                # not our retry case.
                raise
            continue
    raise RuntimeError(
        f"slug collision retries exhausted on UPDATE for "
        f"intended slug {intended_slug!r}: {last_exc}"
    )


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
    force_article_type: Optional[str] = None,
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
    game = sel.get("game") or "unknown"
    if force_article_type and force_article_type != article_type:
        log(f"  Overriding article_type {article_type!r} → {force_article_type!r} "
            f"(force_article_type set by caller)")
        article_type = force_article_type
    log(f"  Selected: {keyword!r} → {article_type}  game={game}  (reason: {sel.get('reason')})")
    summary["stages"].append({"agent": "keyword_selector", **sel})
    summary["keyword_id"] = str(keyword_id)
    summary["keyword"] = keyword
    summary["article_type"] = article_type
    summary["game"] = game

    _set_keyword_status(keyword_id, "in_progress")

    # ----------------------------------------------------- Step 2: article row
    target_words = (
        site_config["content_plan"]["min_word_count"]
        + site_config["content_plan"]["max_word_count"]
    ) // 2
    initial_slug = _slugify(keyword)
    article_id, used_slug = _create_article(
        site_id=site_id, slug=initial_slug, title=keyword, article_type=article_type,
    )
    if used_slug != initial_slug:
        log(f"  ⚠️  slug collision on {initial_slug!r} — using {used_slug!r}")
    initial_slug = used_slug
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
                "game": game,
            },
        )
        # Tag the outline jsonb with the game so PublishAgent can write
        # `game:` into the markdown frontmatter, and so future audits
        # can look at articles.outline->>'game' without a schema change.
        outline["game"] = game
        log(f"  Title: {outline.get('title')}")
        log(f"  Slug:  {outline.get('slug')}")
        log(f"  Sections: {[s.get('h2') for s in outline.get('sections', [])]}")
        summary["stages"].append({"agent": "outline", "title": outline.get("title")})
        desired_slug = outline.get("slug") or initial_slug
        res = _set_article(
            article_id,
            title=outline.get("title") or keyword,
            slug=desired_slug,
            outline=outline,
            status="writing",
        )
        final_slug = res.get("final_slug")
        if final_slug and final_slug != desired_slug:
            log(f"  ⚠️  slug collision on UPDATE {desired_slug!r} → {final_slug!r}")
        if final_slug:
            outline["slug"] = final_slug
            summary["slug"] = final_slug

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
                    "outline": outline, "game": game,
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
                    "outline": outline, "game": game, "word_count": wc,
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
