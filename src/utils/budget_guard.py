"""Monthly budget guardrail for the daily content pipeline.

The daily cron and any scheduled maintenance task asks `check_monthly_budget`
*before* doing anything that costs money. The function returns one of four
actions:

  - 'normal'        : <50% of budget used; run everything
  - 'warn'          : 50-80%; run everything, but log a warning + email
  - 'limit_extras'  : 80-95%; only the core "produce one article" path
                      runs. Optional maintenance tasks (retrofit images,
                      keyword gardener auto-seed, banner batch) skip.
  - 'pause_all'     : >95% or sites.config.cron_paused == true; cron
                      should exit 0 immediately and send an alert email.
                      Resumes automatically on month rollover (next cron
                      run after midnight on the 1st sees a fresh
                      month-to-date sum and drops back to 'normal').

Budget is `sites.config.monthly_budget_usd` (default $30).

Cost basis is the SUM of `agent_runs.cost_usd` for the calling site
where `created_at >= date_trunc('month', now())`. This includes both
text-LLM and image-generation runs (per ImageAgent._record_image which
also goes through agent_runs).

`sites.config.cron_paused` is a manual kill switch. Toggle it from the
dashboard or via SQL when you want to stop the cron without rewriting
the workflow file.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from src.db.client import get_db_connection


DEFAULT_BUDGET_USD = 30.0

ACTION = Literal["normal", "warn", "limit_extras", "pause_all"]


@dataclass
class BudgetCheck:
    site_id: str
    month: str             # 'YYYY-MM'
    budget_usd: float      # monthly cap
    spent_usd: float       # month-to-date sum
    percent: float         # spent / budget, in [0, 1+)
    action: ACTION
    cron_paused: bool      # explicit kill switch from sites.config
    reason: str


def _month_label(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m")


def check_monthly_budget(site_id: UUID | str) -> BudgetCheck:
    """Read site config + sum agent_runs.cost_usd for the current month."""
    site_id_str = str(site_id)
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select config from sites where id = %s", (site_id_str,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"site_id {site_id} not in sites")
        cfg = row[0] or {}

        budget = float(cfg.get("monthly_budget_usd", DEFAULT_BUDGET_USD))
        cron_paused = bool(cfg.get("cron_paused", False))

        cur.execute(
            """
            select coalesce(sum(cost_usd), 0)::float
              from agent_runs
             where site_id = %s
               and created_at >= date_trunc('month', now() at time zone 'utc')
            """,
            (site_id_str,),
        )
        spent = float(cur.fetchone()[0])

    percent = spent / budget if budget > 0 else 1.0

    if cron_paused:
        action: ACTION = "pause_all"
        reason = "sites.config.cron_paused = true (manual kill switch)"
    elif percent > 0.95:
        action = "pause_all"
        reason = f"month-to-date spend ${spent:.2f} > 95% of ${budget:.2f}"
    elif percent > 0.80:
        action = "limit_extras"
        reason = (
            f"month-to-date spend ${spent:.2f} = {percent*100:.0f}% of "
            f"${budget:.2f}; suspending non-essential maintenance"
        )
    elif percent > 0.50:
        action = "warn"
        reason = (
            f"month-to-date spend ${spent:.2f} = {percent*100:.0f}% of "
            f"${budget:.2f}; on track but worth watching"
        )
    else:
        action = "normal"
        reason = (
            f"month-to-date spend ${spent:.2f} = {percent*100:.0f}% of "
            f"${budget:.2f}"
        )

    return BudgetCheck(
        site_id=site_id_str,
        month=_month_label(),
        budget_usd=budget,
        spent_usd=round(spent, 4),
        percent=round(percent, 4),
        action=action,
        cron_paused=cron_paused,
        reason=reason,
    )


# --------------------------------------------------------------- CLI

def _cli() -> int:
    """`python -m src.utils.budget_guard --site ntecodex.com [--json]`

    Prints the BudgetCheck as a key=value summary by default, or JSON.
    Exit code mirrors the action:
        normal/warn/limit_extras → 0
        pause_all                → 78   (chosen non-zero, non-error code
                                        for "skip the cron, not a crash")
    """
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--site", default="ntecodex.com",
                   help="Site domain to look up (default ntecodex.com)")
    p.add_argument("--json", action="store_true",
                   help="Print full BudgetCheck as JSON")
    args = p.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain = %s limit 1", (args.site,))
        row = cur.fetchone()
        if not row:
            print(f"❌ site {args.site!r} not in sites")
            return 2
        site_id = row[0]

    check = check_monthly_budget(site_id)
    if args.json:
        print(json.dumps(asdict(check), indent=2, default=str))
    else:
        print(f"site:   {args.site}")
        print(f"month:  {check.month}")
        print(f"spent:  ${check.spent_usd:.2f} / ${check.budget_usd:.2f} "
              f"({check.percent*100:.1f}%)")
        print(f"action: {check.action}")
        print(f"reason: {check.reason}")
        if check.cron_paused:
            print("flag:   cron_paused = true  (sites.config kill switch)")

    return 78 if check.action == "pause_all" else 0


if __name__ == "__main__":
    sys.exit(_cli())
