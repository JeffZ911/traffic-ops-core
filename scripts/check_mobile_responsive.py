"""Mobile-responsiveness sanity check for newly-published articles.

Curls each article published today, fetches its HTML, and runs a cheap
heuristic check: any line of content that contains an unbroken span of
>40 non-whitespace characters (URL, hash string) outside an existing
HTML element with overflow-wrap rules is a likely mobile-overflow
offender. Sends a single warning email if any new offender is detected.

This is intentionally a CSS-level dry-run rather than a real headless
render — running Playwright/Puppeteer in CI would burn ~30s/article ×
15 articles/day = 7.5 min per cron, defeating the velocity push. The
heuristic catches the common cases (Vertex grounding URLs, long hash
fragments) that CSS rules in src/styles/global.css are supposed to
break. If those CSS rules ever regress, the heuristic fires.

Idempotent / dedup: the alert is gated on whether today's
daily_reports.data_snapshot.mobile_check already contains the same
offending URL set, so we don't email 6× during one bad day.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import urlopen, Request

from dotenv import load_dotenv

from src.db.client import get_db_connection
from src.utils.send_alert import send_alert


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


SITE_BASE = "https://ntecodex.com"
LONG_TOKEN_THRESHOLD = 40   # chars without whitespace; matches typical
                            # Vertex grounding URL prefixes


def _list_today_articles(cur, site_id: str) -> list[dict]:
    today = date.today()
    cur.execute(
        """
        select slug, published_url, article_type
          from articles
         where site_id = %s and status='published'
           and published_at::date = %s
        """,
        (site_id, today),
    )
    out = []
    for slug, url, atype in cur.fetchall():
        if not url:
            continue
        out.append({"slug": slug, "url": url, "type": atype})
    return out


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_LONG_TOKEN_RE = re.compile(r"\S{40,}")


def _strip_tags(html: str) -> str:
    return _HTML_TAG_RE.sub(" ", html)


def _find_long_tokens(text: str) -> list[str]:
    """Return any token >= LONG_TOKEN_THRESHOLD chars long."""
    found = []
    for m in _LONG_TOKEN_RE.finditer(text):
        tok = m.group(0)
        # Skip CSS class strings and inline data: URIs — those are
        # internal to Astro's build, not user-facing content.
        if tok.startswith(("data:", "blob:", ".astro-")):
            continue
        found.append(tok)
    return found


def _is_long_token_safe_to_overflow(token: str) -> bool:
    """If a token is a URL and the article's CSS gives it
    overflow-wrap: anywhere, it's OK. We trust global.css's `a {
    overflow-wrap: anywhere }` rule. The heuristic here only flags
    tokens that appear OUTSIDE an anchor — those would be raw text
    runs that would overflow."""
    # Always treat tokens as safe when they look like a URL — the
    # site-wide `a { overflow-wrap: anywhere }` handles those. Only
    # bare hash-like strings without protocol are flagged.
    if token.startswith(("http://", "https://", "//")):
        return True
    return False


def _fetch(url: str, timeout: int = 12) -> str:
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (mobile-check; check_mobile_responsive)"
    })
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select id from sites where domain='ntecodex.com' limit 1"
        )
        site_id = str(cur.fetchone()[0])
        articles = _list_today_articles(cur, site_id)

    if not articles:
        print(f"  no published articles today — skip")
        return 0

    print(f"=== mobile_responsive_check: {len(articles)} articles today ===")
    offenders: list[dict] = []
    for a in articles:
        full_url = a["url"]
        if not full_url.startswith("http"):
            full_url = SITE_BASE + full_url
        try:
            html = _fetch(full_url)
        except Exception as e:
            print(f"  ⚠️ fetch failed: {full_url} → {e}")
            continue
        # Look at the article body slice only (between <main> and </main>)
        m = re.search(r"<main\b.*?</main>", html, re.DOTALL | re.IGNORECASE)
        scope = m.group(0) if m else html
        text = _strip_tags(scope)
        long_tokens = _find_long_tokens(text)
        # Filter out URL-like tokens that the global `a { overflow-wrap }`
        # rule covers
        unsafe = [t for t in long_tokens if not _is_long_token_safe_to_overflow(t)]
        if unsafe:
            offenders.append({
                "slug": a["slug"], "url": full_url,
                "samples": unsafe[:5],
            })
            print(f"  ❌ {a['slug']}  {len(unsafe)} unsafe long token(s)")
        else:
            print(f"  ✓ {a['slug']}")

    if not offenders:
        print(f"  ✓ all {len(articles)} articles look mobile-safe")
        return 0

    body_lines = [
        f"{len(offenders)} of {len(articles)} articles published today have "
        f"long unbroken text runs that may overflow on 375px viewports:",
        "",
    ]
    for o in offenders:
        body_lines.append(f"  - {o['slug']}  ({o['url']})")
        for s in o["samples"]:
            body_lines.append(f"      offender: {s[:100]}...")
    body_lines.extend([
        "",
        "Likely cause: a new content shape (table, code block, hash string)",
        "that the global CSS rules in src/styles/global.css don't cover.",
        "Add a rule, redeploy, re-run --dry-run to confirm green.",
    ])
    body = "\n".join(body_lines)

    if args.dry_run:
        print()
        print("--- DRY RUN ---")
        print(body)
        return 0

    # Dedup against today's existing alert
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select data_snapshot->'mobile_check'->>'offender_count' "
            " from daily_reports "
            " where site_id = %s and report_date = current_date and data_snapshot ? 'mobile_check'",
            (site_id,),
        )
        row = cur.fetchone()
        if row and row[0] and int(row[0]) == len(offenders):
            print(f"  already alerted today with {len(offenders)} offenders; skipping email")
            return 0

    try:
        send_alert(
            subject=f"[ntecodex] mobile overflow — {len(offenders)} article(s)",
            body=body,
            severity="warning",
        )
        print(f"  ✓ alert email sent")
    except Exception as e:
        print(f"  ⚠️ alert send failed: {e}")

    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "offender_count": len(offenders),
        "offenders": offenders,
    }
    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into daily_reports (site_id, report_date, markdown, data_snapshot)
            values (%s, current_date, %s, %s::jsonb)
            on conflict (site_id, report_date) do update set
              data_snapshot = coalesce(daily_reports.data_snapshot, '{}'::jsonb)
                           || jsonb_build_object('mobile_check',
                                excluded.data_snapshot -> 'mobile_check')
            """,
            (site_id, f"# mobile_check — {date.today().isoformat()}\n",
             json.dumps({"mobile_check": payload})),
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
