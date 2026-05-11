"""SEO Intelligence — weekly GSC-driven feedback loop.

Runs every Monday (via .github/workflows/seo_intelligence_weekly.yml).
Reads the last 14 days of Search Console data for ntecodex.com, then:

  1. **Discovers long-tail keywords.** Queries Google already shows the
     site for but for which we have no matching keyword row OR no
     dedicated article. Inserted into `keywords` with
     `source='gsc_longtail'`, `priority_score=90`, `status='planned'`.
     KeywordSelectorAgent (already updated to add +20 for `gsc_longtail`
     candidates) picks them up on the next daily cron.

  2. **Flags low-rank rewrite candidates.** Published URLs with ≥50
     impressions and an average position in the 11-30 band. These are
     pages Google is already showing in SERP results but on page 2 —
     small content tweaks tend to move them to page 1. Listed in the
     weekly report payload (we can't add an articles.metadata column
     without a schema change, so the report is the single source of
     truth here).

  3. **Extracts high-CTR title patterns.** For pages with CTR > 3%,
     reports the title list so the operator can spot patterns to reuse
     in future articles. (Phase 1.B keeps this advisory — no automatic
     prompt-injection yet.)

  4. **Persists the weekly report.** Writes one row to `daily_reports`
     keyed to last Monday with `data_snapshot` containing all of the
     above. Idempotent via ON CONFLICT — re-running on the same Monday
     refreshes the snapshot.

  5. **Sends an email summary.** Reuses src.utils.send_alert with a
     non-critical severity so it lands in the regular inbox.

No schema migration. Uses only existing columns:
  - keywords.source / .priority_score / .notes
  - daily_reports.data_snapshot (jsonb)
  - metrics_raw.payload (jsonb) — read from previous GSC collector runs

If GSC OAuth credentials aren't configured, the job logs a warning and
returns 0 (so the cron doesn't burn red badges in the dashboard).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv

from src.db.client import get_db_connection
from src.utils.send_alert import send_alert


load_dotenv(Path(__file__).resolve().parents[2] / ".env")


# Long-tail discovery thresholds (tunable). Conservative on purpose:
# we'd rather miss a real opportunity than spam the keyword pool with
# noise that the LLM has to filter every day.
MIN_IMPRESSIONS = 5           # ≥5 imp over the window
MIN_POSITION = 5.0            # not yet top-5 (i.e. not already ranking great)
MAX_POSITION = 50.0           # not utterly hopeless either
LOOKBACK_DAYS = 14

# Page-rewrite candidate band
REWRITE_MIN_IMPRESSIONS = 50
REWRITE_POSITION_LOW = 11.0
REWRITE_POSITION_HIGH = 30.0

# High-CTR pattern threshold
HIGH_CTR_THRESHOLD = 0.03


def _last_monday(today: date | None = None) -> date:
    today = today or date.today()
    return today - timedelta(days=today.weekday())


# --------------------------------------------------------------- GSC fetch

def _fetch_gsc_window(site_id: UUID, days: int) -> dict[str, Any]:
    """Pull a fresh GSC slice covering the last N days, by query AND by
    page. Returns a structured dict — does NOT touch metrics_raw.

    If GSC API isn't reachable (no creds, no property), returns an empty
    structure so the rest of the job degrades gracefully.
    """
    try:
        from googleapiclient.discovery import build
        from src.utils.google_oauth import get_user_credentials
        from src.collectors.gsc import _site_property, _try_query
    except Exception as e:
        print(f"⚠️  GSC libs unavailable: {e}")
        return {"queries": [], "pages": [], "site_url": None}

    try:
        creds = get_user_credentials()
        svc = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        print(f"⚠️  GSC auth failed: {e}")
        return {"queries": [], "pages": [], "site_url": None}

    try:
        site_url = _site_property(site_id)
    except Exception as e:
        print(f"⚠️  GSC site_property lookup failed: {e}")
        return {"queries": [], "pages": [], "site_url": None}

    end = date.today() - timedelta(days=2)   # GSC has ~2d processing lag
    start = end - timedelta(days=days - 1)

    body_q = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": ["query"],
        "rowLimit": 500,
    }
    body_p = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": ["page"],
        "rowLimit": 500,
    }
    body_qp = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": ["page", "query"],
        "rowLimit": 1000,
    }

    out: dict[str, Any] = {
        "site_url": site_url,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "queries": [],
        "pages": [],
        "page_query_pairs": [],
    }
    try:
        out["queries"] = _try_query(svc, site_url, body_q).get("rows", []) or []
    except Exception as e:
        print(f"⚠️  GSC query-dim fetch failed: {e}")
    try:
        out["pages"] = _try_query(svc, site_url, body_p).get("rows", []) or []
    except Exception as e:
        print(f"⚠️  GSC page-dim fetch failed: {e}")
    try:
        out["page_query_pairs"] = _try_query(svc, site_url, body_qp).get("rows", []) or []
    except Exception as e:
        print(f"⚠️  GSC page+query-dim fetch failed: {e}")
    return out


# --------------------------------------------------------------- Section 1

def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _discover_longtail(
    site_id: UUID,
    gsc: dict[str, Any],
) -> list[dict[str, Any]]:
    """Find GSC queries that:
       - have ≥MIN_IMPRESSIONS in the window,
       - average position in (MIN_POSITION, MAX_POSITION],
       - aren't already a keyword row (exact or substring match either way),
       - aren't already an article title token-match.

       Returns a list of dicts ready to INSERT into `keywords`. Caller
       handles the write.
    """
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select keyword from keywords where site_id = %s",
            (str(site_id),),
        )
        existing_kw = [_normalize(r[0]) for r in cur.fetchall()]
        cur.execute(
            "select title from articles where site_id = %s and status = 'published'",
            (str(site_id),),
        )
        existing_titles = [_normalize(r[0]) for r in cur.fetchall()]

    def matches_existing(q_norm: str) -> bool:
        if any(q_norm == k for k in existing_kw):
            return True
        if any(q_norm in k or k in q_norm for k in existing_kw if k):
            return True
        # Match against article titles by stem (drop very common words)
        for t in existing_titles:
            if not t:
                continue
            if q_norm in t:
                return True
        return False

    candidates: list[dict[str, Any]] = []
    for row in gsc.get("queries", []):
        keys = row.get("keys") or []
        if not keys:
            continue
        q_text = keys[0]
        q_norm = _normalize(q_text)
        if len(q_norm) < 6:        # skip 1-2 word fragments
            continue
        impressions = int(row.get("impressions", 0))
        position = float(row.get("position", 999))
        if impressions < MIN_IMPRESSIONS:
            continue
        if not (MIN_POSITION < position <= MAX_POSITION):
            continue
        if matches_existing(q_norm):
            continue
        candidates.append({
            "keyword": q_text,
            "impressions": impressions,
            "clicks": int(row.get("clicks", 0)),
            "ctr": float(row.get("ctr", 0)),
            "position": round(position, 2),
        })
    return candidates


def _insert_longtail_keywords(
    site_id: UUID,
    candidates: list[dict[str, Any]],
) -> int:
    inserted = 0
    for c in candidates:
        note = (f"Discovered from GSC: {c['impressions']} imp, "
                f"position {c['position']}, ctr {c['ctr']*100:.1f}%")
        with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into keywords
                  (site_id, keyword, intent, priority_score, status, source, notes)
                values (%s, %s, 'informational', 90, 'planned', 'gsc_longtail', %s)
                on conflict (site_id, keyword) do nothing
                returning id
                """,
                (str(site_id), c["keyword"], note),
            )
            if cur.fetchone():
                inserted += 1
    return inserted


# --------------------------------------------------------------- Section 2

def _find_rewrite_candidates(
    site_id: UUID,
    gsc: dict[str, Any],
) -> list[dict[str, Any]]:
    """Published URL with ≥REWRITE_MIN_IMPRESSIONS, position in 11-30."""
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id, slug, title, published_url
              from articles
             where site_id = %s and status = 'published'
            """,
            (str(site_id),),
        )
        articles = [
            {"id": r[0], "slug": r[1], "title": r[2], "url": r[3]}
            for r in cur.fetchall()
        ]
    url_to_article = {
        (a["url"] or "").rstrip("/"): a for a in articles
    }

    out: list[dict[str, Any]] = []
    for row in gsc.get("pages", []):
        keys = row.get("keys") or []
        if not keys:
            continue
        page_url = keys[0]
        # Try to match by path (ignore protocol+host)
        try:
            from urllib.parse import urlparse
            path = urlparse(page_url).path.rstrip("/")
        except Exception:
            path = page_url.rstrip("/")
        impressions = int(row.get("impressions", 0))
        position = float(row.get("position", 999))
        if impressions < REWRITE_MIN_IMPRESSIONS:
            continue
        if not (REWRITE_POSITION_LOW <= position <= REWRITE_POSITION_HIGH):
            continue
        art = url_to_article.get(path)
        if not art:
            continue
        out.append({
            "article_id": str(art["id"]),
            "slug": art["slug"],
            "title": art["title"],
            "url": path,
            "impressions": impressions,
            "clicks": int(row.get("clicks", 0)),
            "ctr": round(float(row.get("ctr", 0)), 4),
            "position": round(position, 2),
        })
    return out


# --------------------------------------------------------------- Section 3

def _high_ctr_patterns(
    site_id: UUID,
    gsc: dict[str, Any],
) -> list[dict[str, Any]]:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select coalesce(published_url, ''), title from articles "
            "where site_id = %s and status = 'published'",
            (str(site_id),),
        )
        url_to_title = {
            (r[0] or "").rstrip("/"): r[1] for r in cur.fetchall()
        }

    out: list[dict[str, Any]] = []
    for row in gsc.get("pages", []):
        keys = row.get("keys") or []
        if not keys:
            continue
        page_url = keys[0]
        try:
            from urllib.parse import urlparse
            path = urlparse(page_url).path.rstrip("/")
        except Exception:
            path = page_url.rstrip("/")
        impressions = int(row.get("impressions", 0))
        ctr = float(row.get("ctr", 0))
        if impressions < 20:           # filter noise
            continue
        if ctr < HIGH_CTR_THRESHOLD:
            continue
        title = url_to_title.get(path)
        if not title:
            continue
        out.append({
            "slug_path": path,
            "title": title,
            "impressions": impressions,
            "ctr": round(ctr, 4),
        })
    out.sort(key=lambda x: x["ctr"], reverse=True)
    return out[:10]


# --------------------------------------------------------------- Section 4

def _persist_report(
    site_id: UUID,
    payload: dict[str, Any],
    report_date: date,
) -> None:
    """Upsert one row in daily_reports for this Monday. Merges the
    seo_intelligence payload under data_snapshot.seo_intelligence so we
    don't overwrite a same-day daily content report."""
    markdown = (
        f"# SEO intelligence — {report_date.isoformat()}\n\n"
        f"- longtail_discovered: {payload['summary']['longtail_discovered']}\n"
        f"- longtail_skipped (already known): "
        f"{payload['summary']['longtail_skipped']}\n"
        f"- rewrite_candidates: {payload['summary']['rewrite_candidates']}\n"
        f"- high_ctr_examples: {payload['summary']['high_ctr_examples']}\n"
    )
    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into daily_reports
              (site_id, report_date, markdown, data_snapshot)
            values (%s, %s, %s, %s::jsonb)
            on conflict (site_id, report_date) do update set
              data_snapshot = coalesce(daily_reports.data_snapshot, '{}'::jsonb)
                            || jsonb_build_object('seo_intelligence',
                                 excluded.data_snapshot -> 'seo_intelligence'),
              markdown = case
                when daily_reports.markdown like '%%SEO intelligence%%'
                then excluded.markdown
                else daily_reports.markdown || E'\n\n---\n\n' || excluded.markdown
              end
            """,
            (
                str(site_id), report_date, markdown,
                json.dumps({"seo_intelligence": payload}),
            ),
        )


# --------------------------------------------------------------- main


def run(site_id: UUID, *, dry_run: bool = False) -> dict[str, Any]:
    print(f"=== seo_intelligence: site_id={site_id} dry_run={dry_run} ===")

    gsc = _fetch_gsc_window(site_id, LOOKBACK_DAYS)
    print(f"GSC window: {gsc.get('window')}; "
          f"{len(gsc.get('queries', []))} query rows, "
          f"{len(gsc.get('pages', []))} page rows")

    candidates = _discover_longtail(site_id, gsc)
    if dry_run:
        inserted = len(candidates)
    else:
        inserted = _insert_longtail_keywords(site_id, candidates)
    skipped = len(candidates) - inserted

    rewrite = _find_rewrite_candidates(site_id, gsc)
    high_ctr = _high_ctr_patterns(site_id, gsc)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "window": gsc.get("window"),
        "summary": {
            "longtail_discovered": inserted,
            "longtail_skipped": skipped,
            "rewrite_candidates": len(rewrite),
            "high_ctr_examples": len(high_ctr),
        },
        "longtail_candidates": candidates,
        "rewrite_candidates": rewrite,
        "high_ctr_examples": high_ctr,
    }

    report_date = _last_monday()
    if not dry_run:
        _persist_report(site_id, payload, report_date)
        # Email the summary (non-critical so it goes to regular inbox)
        body_lines = [
            f"Window: {gsc.get('window')}",
            f"Long-tail keywords inserted: {inserted}",
            f"Skipped (already in keyword pool): {skipped}",
            f"Rewrite candidates (pos 11-30): {len(rewrite)}",
            f"High-CTR examples (>3%): {len(high_ctr)}",
            "",
        ]
        if candidates[:5]:
            body_lines.append("Top 5 new long-tail picks:")
            for c in candidates[:5]:
                body_lines.append(
                    f"  - {c['keyword']!r}  imp={c['impressions']}  "
                    f"pos={c['position']}  ctr={c['ctr']*100:.1f}%"
                )
        try:
            send_alert(
                subject=f"[ntecodex] weekly SEO intelligence — {report_date.isoformat()}",
                body="\n".join(body_lines),
                severity="info",
            )
        except Exception as e:
            print(f"⚠️  alert send failed: {e}")

    return payload


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Compute everything but don't write to DB or send email")
    args = p.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain = 'ntecodex.com' limit 1")
        row = cur.fetchone()
        if not row:
            print("❌ ntecodex.com not in sites table")
            return 2
        site_id = row[0]

    result = run(site_id, dry_run=args.dry_run)
    print(json.dumps(result["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
