"""QDF P3 — next-day retrospective for fresh trend pages.

Manual §5 (次日复盘): every day, check each recent QDF/trend page's index
status + search performance, so the keyword strategy can learn (which trend
angles got crawled/indexed/impressions = repeat; which got nothing = refine).

For each trend page published in the last N days:
  - GSC urlInspection → coverageState (unknown / discovered / crawled / indexed)
    + last crawl time. (READ-ONLY, fully compliant.)
  - GSC searchanalytics dimensions=['page'] → impressions / clicks / position.
Classify:
  WIN     indexed + impressions > 0
  PENDING crawled or discovered, no impressions yet
  COLD    still unknown to Google (not crawled) after the QDF window
A rolling /todos card per site shows the digest; COLD pages older than 3 days
are the signal to refine keyword selection (or that crawl trust is the blocker).

Usage:
  python -m scripts.qdf_report --site quvii.com
  python -m scripts.qdf_report --site quvii.com --days 7
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection
from src.utils.ops_tasks import upsert_open_task

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_INDEXED = {"Submitted and indexed", "Indexed, not submitted in sitemap"}


def _fresh_trend_pages(site_id: str, days: int) -> list[tuple[str, str, int, str]]:
    """(public_url, keyword, age_days, notes) for trend pages published in last
    N days. `notes` carries the original selection rationale (the trigger event
    + source) — context the AI uses to learn which angles performed."""
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select distinct a.published_url, k.keyword,
                   floor(extract(epoch from (now() - a.published_at)) / 86400.0)::int,
                   coalesce(k.notes, '')
              from articles a
              join article_keywords ak on ak.article_id = a.id
              join keywords k on k.id = ak.keyword_id
             where a.site_id = %s and a.status = 'published'
               and a.published_url is not null and k.source = 'trend'
               and a.published_at >= now() - %s * interval '1 day'
             order by 3 asc
            """,
            (site_id, days),
        )
        return [(r[0], r[1], r[2], r[3]) for r in cur.fetchall()]


_AI_PROMPT = """You are the SEO performance analyst for {site}, a young,
low-authority site running a QDF (Query-Deserves-Freshness) strategy: publish
timely trend pages fast to win the freshness window.

OUR OBJECTIVE, in order: (1) win IMPRESSIONS first (get crawled + indexed +
shown), (2) then convert impressions to CLICKS. This is a self-reinforcing
loop — more impressions → more clicks → more authority → more impressions.

Below is how the trend pages we published recently actually performed. Each has
the keyword, WHY we picked it (selection_notes = the trigger event/source), how
old it is, its Google coverage_state (unknown=not crawled, discovered/crawled=
seen but not indexed, indexed), and impressions/clicks/position.

PERFORMANCE DATA (JSON):
{data}

Analyse signal even when numbers are small — coverage_state progression IS
signal (which angles got crawled/discovered fastest vs stayed unknown; which
topic types, freshness, or sources correlate with faster pickup).

Return ONLY a JSON object (no fence):
{{
  "retrospective": "<3-5 sentences: what worked, what didn't, and WHY, grounded in the data above>",
  "guidance": "<concrete, imperative rules for selecting TOMORROW's trend keywords to maximise impressions — e.g. which angle types / freshness / source kinds to favour or avoid. 3-6 short bullet lines. This text is injected verbatim into the next keyword-generation prompt, so write it AS instructions to the keyword generator.>"
}}
"""


def _ai_retrospect(site: str, site_id: str, records: list[dict], model: str) -> str:
    """Gemini analyses performance → forward guidance; store it so the next
    trend generation reads it (the self-improvement loop). Returns a markdown
    block to append to the /todos card."""
    import json
    from src.utils.llm import get_llm_provider
    from src.utils.qdf_memory import save_qdf_learning

    provider = get_llm_provider("gemini")
    prompt = _AI_PROMPT.format(site=site, data=json.dumps(records, ensure_ascii=False))
    # Pro model emits a thinking step + two prose fields; 1200 truncated the
    # 'guidance' mid-string. 2500 gives both fields room to complete.
    resp = provider.generate(prompt=prompt, model=model, max_tokens=2500,
                             temperature=0.3, json_mode=True)
    text = (resp.text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        obj = json.loads(text)
    except Exception:
        i, j = text.find("{"), text.rfind("}")
        obj = json.loads(text[i:j + 1]) if i >= 0 and j > i else {}
    retro = (obj.get("retrospective") or "").strip()
    guidance = (obj.get("guidance") or "").strip()
    if not guidance:
        print("  ⚠️  AI returned no guidance"); return ""
    save_qdf_learning(site_id, retro, guidance, model=model)
    print(f"  🤖 AI guidance saved (model={model}, cost ${resp.cost_usd:.4f})")
    return (f"\n— — — AI 复盘({model})— — —\n"
            f"【复盘】{retro}\n\n【明日选词指导(已注入下次生成)】\n{guidance}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--model", default="gemini-3.1-pro-preview",
                    help="Gemini model for the AI analyst (Pro = best reasoning)")
    ap.add_argument("--no-ai", action="store_true",
                    help="skip the AI retrospect/guidance step (data-only report)")
    args = ap.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain=%s", (args.site,))
        row = cur.fetchone()
        if not row:
            print(f"❌ site {args.site!r} not in sites"); return 2
        site_id = str(row[0])

    pages = _fresh_trend_pages(site_id, args.days)
    if not pages:
        print(f"  no fresh trend pages (last {args.days}d) on {args.site}")
        return 0

    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from src.utils.google_oauth import get_user_credentials

    prop = f"sc-domain:{args.site}"
    svc = build("searchconsole", "v1", credentials=get_user_credentials(),
                cache_discovery=False)

    # Page-level performance (last 7d) → {full_url: (impr, clicks, pos)}
    perf: dict[str, tuple[int, int, float]] = {}
    try:
        body = {
            "startDate": (date.today() - timedelta(days=7)).isoformat(),
            "endDate": date.today().isoformat(),
            "dimensions": ["page"], "rowLimit": 1000,
        }
        resp = svc.searchanalytics().query(siteUrl=prop, body=body).execute()
        for r in resp.get("rows", []):
            perf[r["keys"][0].rstrip("/")] = (
                int(r.get("impressions", 0)), int(r.get("clicks", 0)),
                float(r.get("position", 0.0)),
            )
    except HttpError as e:
        print(f"  ⚠️  page-perf query failed: {e.resp.status}")

    wins = pending = cold = 0
    lines: list[str] = []
    cold_kws: list[str] = []
    records: list[dict] = []   # structured per-page data for the AI analyst
    for url, kw, age, notes in pages:
        full = f"https://{args.site}{url}" if not url.startswith("http") else url
        state = "?"
        try:
            ins = svc.urlInspection().index().inspect(
                body={"inspectionUrl": full, "siteUrl": prop}
            ).execute()
            state = (ins.get("inspectionResult", {})
                        .get("indexStatusResult", {}).get("coverageState", "?"))
        except HttpError as e:
            state = f"inspect {e.resp.status}"

        impr, clk, pos = perf.get(full.rstrip("/"), (0, 0, 0.0))
        indexed = state in _INDEXED
        if indexed and impr > 0:
            tag = "WIN"; wins += 1
        elif indexed or "rawl" in state or "iscover" in state:
            tag = "PENDING"; pending += 1
        else:
            tag = "COLD"; cold += 1
            if age >= 3:
                cold_kws.append(kw)
        lines.append(
            f"  [{tag}] d{age} impr={impr} clk={clk} pos={pos:.0f} "
            f"{state[:34]} — {url}"
        )
        records.append({
            "keyword": kw, "selection_notes": notes[:200], "age_days": age,
            "coverage_state": state, "impressions": impr, "clicks": clk,
            "position": round(pos, 1), "tag": tag,
        })

    digest = "\n".join(lines)
    print(f"📊 QDF retrospective — {args.site} ({len(pages)} fresh trend page(s))")
    print(digest)
    print(f"  → WIN {wins} · PENDING {pending} · COLD {cold}")

    # ── AI self-improvement loop: Gemini analyses what performed and writes
    # forward guidance that the NEXT trend generation reads (qdf_memory). This
    # is the channel that lets learning flow → keywords auto-iterate toward the
    # objective (impressions first, then clicks).
    ai_block = ""
    if not args.no_ai:
        try:
            ai_block = _ai_retrospect(args.site, site_id, records, args.model)
        except Exception as e:  # noqa: BLE001 — never let the AI step break the report
            print(f"  ⚠️  AI analyst skipped: {type(e).__name__}: {str(e)[:120]}")

    body_md = (
        f"昨日 QDF 热点页表现({len(pages)} 个,近 {args.days} 天):"
        f"WIN {wins} · PENDING {pending} · COLD {cold}\n"
        "WIN=已收录+有曝光 · PENDING=已爬/已发现待收 · COLD=谷歌还没爬到\n\n"
        f"{digest}\n\n"
    )
    if cold_kws:
        body_md += (
            "⚠️ 发了 3 天+仍没被爬到的词(迭代信号 —— 要么换更有热度的角度,"
            "要么是整站爬取信任不足,需配合收录基建):\n"
            + "\n".join(f"  · {k}" for k in cold_kws) + "\n\n"
        )
    if ai_block:
        body_md += ai_block

    upsert_open_task(
        f"QDF 次日复盘 — {args.site}",
        body_md,
        priority="low", category="qdf-report", site_domain=args.site,
    )
    print(f"  ✓ /todos card updated (QDF 次日复盘 — {args.site})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
