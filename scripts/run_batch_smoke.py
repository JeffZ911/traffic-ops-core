"""Run the orchestrator N times in a single cron invocation.

Replaces run_one_article_smoke for the velocity-accelerated cron (Phase
2.4 / 2026-05-12). Each iteration:

  - Re-reads budget_guard. If `action == 'pause_all'`, stop the batch
    immediately so we don't burn dollars after the kill switch fires.
  - Wraps run_one_article in its own try/except so a single crash
    doesn't sink the rest of the batch.
  - Tallies cost per iteration + cumulative.
  - Prints a per-article scorecard at the end (same shape as
    run_batch_articles.py so existing dashboards keep parsing).

Designed for the multi-iteration GH Actions step where we want 4 attempts
per cron × 6 cron/day = 24 attempts/day. The user's 65% pass-rate target
puts us at ~15-17 published / day.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from src.db.client import get_db_connection
from src.pipeline.orchestrator import run_one_article
from src.utils.budget_guard import check_monthly_budget


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, default=4,
                   help="How many articles to attempt this batch")
    p.add_argument("--max-retries", type=int, default=1,
                   help="QA-rewrite retry rounds inside each article")
    p.add_argument("--daily-cap", type=int, default=None,
                   help="Override sites.config.content_plan.daily_article_cap")
    p.add_argument("--reserve-source", default=None,
                   help="Reserve a daily slice for a keyword source, format "
                        "'source:N' (e.g. 'expansion:2'). While fewer than N "
                        "articles from that source are PUBLISHED today, each "
                        "iteration forces the selector to that source — so the "
                        "trend layer can't crowd out the footprint experiment. "
                        "Falls back gracefully if the source pool is empty.")
    args = p.parse_args()

    # Parse --reserve-source 'source:N'
    reserve_source: str | None = None
    reserve_n = 0
    if args.reserve_source:
        _parts = args.reserve_source.split(":", 1)
        reserve_source = _parts[0].strip() or None
        reserve_n = int(_parts[1]) if len(_parts) > 1 and _parts[1].strip().isdigit() else 1

    import os
    site_domain = os.getenv("SITE_DOMAIN", "ntecodex.com")
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id, config from sites where domain=%s limit 1", (site_domain,))
        row = cur.fetchone()
        if not row:
            print(f"❌ site {site_domain!r} not found in sites table")
            return 2
        site_id, _cfg = row
    # Daily production cap (quality-first cadence). Resolution order:
    #   --daily-cap arg → sites.config.content_plan.daily_article_cap → 5.
    _cap_cfg = ((_cfg or {}).get("content_plan") or {}).get("daily_article_cap")
    daily_cap = args.daily_cap if args.daily_cap is not None else (
        int(_cap_cfg) if _cap_cfg is not None else 5
    )

    # Per-article-type FLOOR (revenue-driver guarantee). Example:
    #   { "comparison": 3 }
    # means: as long as comparison articles published today < 3, force the
    # KeywordSelector to prefer comparison keywords. Implemented via a flag
    # passed into run_one_article → KeywordSelector that filters candidates.
    _floors_cfg = ((_cfg or {}).get("content_plan") or {}).get("article_type_floors") or {}
    type_floors: dict[str, int] = {
        k: int(v) for k, v in _floors_cfg.items() if isinstance(v, (int, str)) and str(v).isdigit()
    }
    # A floor on a BLACKLISTED type is a config contradiction that deadlocks
    # the day: the floor is never met (the type can't be written), so it is
    # forced on every iteration while the selector filters every matching
    # candidate out (ntecodex had floors on news/weapon_db/character_db with
    # all three blacklisted). Drop such floors loudly instead of honoring them.
    _bl = set((( _cfg or {}).get("content_plan") or {}).get("type_blacklist") or [])
    _contradictory = sorted(set(type_floors) & _bl)
    if _contradictory:
        print(f"⚠️  ignoring article_type_floors on BLACKLISTED types "
              f"{_contradictory} — floors and type_blacklist contradict; "
              f"fix sites.config.content_plan for {site_domain}")
        type_floors = {k: v for k, v in type_floors.items() if k not in _bl}

    def _produced_today() -> int:
        """Articles created today (UTC) for this site — any status, since a
        failed attempt still consumed a slot + spend."""
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "select count(*) from articles where site_id=%s "
                "and created_at >= date_trunc('day', now() at time zone 'utc')",
                (str(site_id),),
            )
            return int(cur.fetchone()[0])

    def _published_today_by_type(article_type: str) -> int:
        """Count articles of a given type with status='published' today.
        Used to decide whether the type-floor still binds — we count only
        published, not just attempted, because qa_failed attempts don't
        deliver revenue."""
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "select count(*) from articles where site_id=%s "
                "and article_type=%s and status='published' "
                "and created_at >= date_trunc('day', now() at time zone 'utc')",
                (str(site_id), article_type),
            )
            return int(cur.fetchone()[0])

    def _forced_type_for_next() -> str | None:
        """If a type-floor isn't met yet, return that type so the selector
        forces a matching keyword. Returns None when all floors met."""
        for atype, floor in type_floors.items():
            if _published_today_by_type(atype) < floor:
                return atype
        return None

    def _produced_today_by_source(src: str) -> int:
        """Articles this site PRODUCED today (status in qa_passed|published)
        whose source keyword has source=src, joined via article_keywords.

        NB: we count 'qa_passed', NOT just 'published'. run_one_article only
        advances an article to qa_passed; the promotion to 'published' happens
        in a SEPARATE later workflow step (scripts.publish_articles), after this
        whole batch loop returns. Counting only 'published' would freeze this
        number at its start-of-cron value for the entire batch, so the reserve
        force would never release mid-batch and expansion would seize the whole
        first cron. Counting qa_passed reflects the batch's own progress."""
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "select count(distinct a.id) from articles a "
                "join article_keywords ak on ak.article_id = a.id "
                "join keywords k on k.id = ak.keyword_id "
                "where a.site_id=%s and k.source=%s "
                "and a.status in ('qa_passed','published') "
                "and a.created_at >= date_trunc('day', now() at time zone 'utc')",
                (str(site_id), src),
            )
            return int(cur.fetchone()[0])

    print("=" * 78)
    print(f"=== Batch smoke: {args.count} articles ===")
    print("=" * 78)

    results: list[dict] = []
    t0 = time.perf_counter()
    # Per-batch cap on reserved-slice forcing: never force more than reserve_n
    # iterations in a SINGLE cron, so a fresh-trend day (whose QDF keywords need
    # a same-run slot) always keeps ≥ count-reserve_n slots, and a run where
    # forced expansion keeps QA-failing can't burn the entire batch on it.
    reserve_forced_this_run = 0

    for i in range(1, args.count + 1):
        # Daily-cap check — stop once today's quota is met, regardless of how
        # often the cron fires. This is what actually holds output to ~N/day.
        done_today = _produced_today()
        if done_today >= daily_cap:
            print(f"\n✋ daily cap reached: {done_today}/{daily_cap} articles "
                  f"already produced today for {site_domain}; stopping batch.")
            break

        # Per-iteration budget check — bail mid-batch if we cross 95%.
        bg = check_monthly_budget(site_id)
        if bg.action == "pause_all":
            print(f"\n🛑 budget_guard action='pause_all' "
                  f"(${bg.spent_usd:.2f} / ${bg.budget_usd:.2f}); "
                  f"stopping batch after {i - 1} of {args.count}")
            break

        print(f"\n{'#' * 78}\n# Article {i}/{args.count}  "
              f"(month spent ${bg.spent_usd:.2f}/${bg.budget_usd:.2f} = "
              f"{bg.percent*100:.0f}%)\n{'#' * 78}")
        # Type-floor enforcement: if any article_type's daily floor is
        # not met yet, force this iteration to that type. Lets us
        # guarantee 3 affiliate roundups/day on ntecodex (article_type
        # =comparison) and 3 vs_comparison/day on pixelmatch.
        forced_type = _forced_type_for_next() if type_floors else None
        if forced_type:
            print(f"  🎯 floor: forcing article_type={forced_type!r} this iteration")

        # Reserved-slice: while today's PRODUCED count for the reserved source
        # is below target AND we haven't already forced reserve_n times this
        # cron, force this iteration to that source (footprint experiment
        # guarantee). Independent of the type-floor above.
        forced_source = None
        if reserve_source and reserve_forced_this_run < reserve_n:
            _got = _produced_today_by_source(reserve_source)
            if _got < reserve_n:
                forced_source = reserve_source
                reserve_forced_this_run += 1
                print(f"  🧭 reserve: forcing source={forced_source!r} this "
                      f"iteration ({_got}/{reserve_n} produced today, "
                      f"{reserve_forced_this_run}/{reserve_n} forced this cron)")

        try:
            summary = run_one_article(
                site_id,
                max_retry_rounds_override=args.max_retries,
                force_article_type=forced_type,
                force_source=forced_source,
            )
        except Exception as e:
            print(f"❌ Article {i} crashed: {type(e).__name__}: {e}")
            results.append({"index": i, "status": "crashed", "error": str(e)[:200]})
            continue

        article_id = summary.get("article_id")
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "select coalesce(sum(cost_usd),0)::float from agent_runs "
                "where article_id = %s",
                (article_id,),
            )
            article_cost = float(cur.fetchone()[0])
            # Plus the selector run (no article_id link)
            cur.execute(
                "select coalesce(cost_usd, 0)::float from agent_runs "
                "where agent_name='keyword_selector' and article_id is null "
                "order by created_at desc limit 1"
            )
            sel_row = cur.fetchone()
            sel_cost = float(sel_row[0]) if sel_row else 0.0
        total_cost = article_cost + sel_cost

        qa = summary.get("qa") or {}
        fb = qa.get("feedback") or {}
        results.append({
            "index": i,
            "article_id": article_id,
            "keyword": summary.get("keyword"),
            "game": summary.get("game"),
            "article_type": summary.get("article_type"),
            "final_status": summary.get("final_status"),
            "qa_score": qa.get("score"),
            "factual_accuracy": fb.get("factual_accuracy"),
            "fabricated_terms": fb.get("fabricated_terms") or [],
            "word_count": summary.get("word_count"),
            "cost_usd": total_cost,
        })

        print(f"\n   ↪︎ result: {summary.get('final_status')}  "
              f"qa={qa.get('score')}  cost=${total_cost:.4f}")

    elapsed = time.perf_counter() - t0
    print()
    print("=" * 78)
    print("=== Batch Smoke Scorecard ===")
    print("=" * 78)
    passed = sum(1 for r in results if r.get("final_status") == "qa_passed")
    failed = sum(1 for r in results if r.get("final_status") == "qa_failed")
    crashed = sum(1 for r in results if r.get("status") == "crashed")
    qa_scores = [r["qa_score"] for r in results if r.get("qa_score") is not None]
    avg_qa = sum(qa_scores) / len(qa_scores) if qa_scores else 0
    cumulative_cost = sum(r.get("cost_usd", 0) for r in results)
    games = {}
    for r in results:
        g = r.get("game") or "n/a"
        games[g] = games.get(g, 0) + 1

    print(f"  Articles attempted   : {len(results)}")
    print(f"  ✅ qa_passed         : {passed}")
    print(f"  ❌ qa_failed         : {failed}")
    print(f"  💥 crashed           : {crashed}")
    print(f"  📈 avg qa_score      : {avg_qa:.2f}")
    print(f"  🎮 game distribution : {games}")
    print(f"  💰 total cost        : ${cumulative_cost:.4f}")
    print(f"  ⏱️  wall clock        : {elapsed:.1f}s")

    print()
    print("Per-article:")
    for r in results:
        st = r.get("final_status") or r.get("status")
        kw = r.get("keyword", "?")
        atype = r.get("article_type", "?")
        game = r.get("game", "?")
        score = r.get("qa_score")
        cost = r.get("cost_usd", 0)
        flag = "✅" if st == "qa_passed" else "❌"
        print(f"  {flag} #{r['index']}  [{st}]  {kw!r} ({atype}, {game})  "
              f"score={score} cost=${cost:.4f}")

    # ── DEADMAN (2026-06-10): a batch where EVERY attempted slot crashed is
    # a production outage, not a quality miss — and it used to hide behind a
    # green run (ntecodex was silently dead 28h, quvii 66h). Make it loud:
    # a dashboard alert row (HealthBanner) + a GitHub ::error:: annotation.
    # Still exit 0 — downstream steps (publish/image/sitemap) must keep
    # running for the rest of the pipeline; the step must not block them.
    attempted = len(results)
    if attempted > 0 and crashed == attempted:
        msg = (f"PRODUCTION DEADMAN: {site_domain} attempted {attempted} "
               f"article slot(s), ALL crashed — site is producing nothing. "
               f"First error: {results[0].get('error', '?')[:160]}")
        print(f"\n::error title=Content production dead ({site_domain})::{msg}")
        try:
            with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
                # Dedupe: at most one deadman alert per site per day.
                cur.execute(
                    "select 1 from alerts where site_id=%s and category='deadman' "
                    "and created_at >= date_trunc('day', now() at time zone 'utc') limit 1",
                    (str(site_id),),
                )
                if not cur.fetchone():
                    cur.execute(
                        "insert into alerts (site_id, level, category, title, message, context) "
                        "values (%s, 'critical', 'deadman', %s, %s, %s::jsonb)",
                        (str(site_id),
                         f"Content production dead ({site_domain})", msg,
                         json.dumps({"attempted": attempted, "crashed": crashed,
                                     "first_error": results[0].get("error", "")[:300]})),
                    )
                    print("  🚨 deadman alert written (dashboard HealthBanner)")
        except Exception as _e:  # noqa: BLE001 — alerting must never crash the run
            print(f"  ⚠️  deadman alert insert failed: {type(_e).__name__}")

    # Exit 0 always — cron continues to publish/image/deploy steps for
    # any qa_passed articles even if some attempts failed.
    return 0


if __name__ == "__main__":
    sys.exit(main())
