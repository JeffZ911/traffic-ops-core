"""
End-to-end pipeline smoke test.

Builds a minimal DummyAgent (task_type='writing') that calls Gemini once,
runs it via BaseAgent.run(), and asserts the agent_runs row was written
with the expected fields. Then deletes the test row.

Marker: input.metadata.test_run = true so cleanup is unambiguous.

Usage:
    python -m scripts.smoke_test_pipeline
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv

from src.agents.base import BaseAgent
from src.db.client import get_db_connection
from src.utils.llm import LLMResponse, get_llm_provider


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class DummyAgent(BaseAgent):
    name = "writing"
    task_type = "writing"
    max_retries = 0  # smoke test: don't burn API quota on retries

    def _execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        prompt = input_data.get(
            "prompt", "Say 'hello world' in five English words. Keep it short."
        )
        resp: LLMResponse = self._call_llm(
            prompt=prompt,
            max_tokens=8000,   # leave room for thinking tokens on preview models
            temperature=0.2,
        )
        return {"text": resp.text, "char_count": len(resp.text)}


def _load_ntecodex_site() -> tuple[UUID, dict[str, Any]]:
    """Read sites row for ntecodex.com. Returns (id, config)."""
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select id, config from sites where domain = 'ntecodex.com' limit 1"
        )
        row = cur.fetchone()
        if not row:
            print("❌ sites table has no ntecodex.com row. "
                  "Run scripts/bootstrap_first_site.py first.")
            sys.exit(2)
        return row[0], row[1]


def _fetch_run(run_id: UUID) -> dict[str, Any]:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id, site_id, article_id, agent_name, status, model,
                   tokens_in, tokens_out, cost_usd, duration_ms,
                   input, output, error_msg, created_at
              from agent_runs
             where id = %s
            """,
            (str(run_id),),
        )
        row = cur.fetchone()
        cols = [d.name for d in cur.description]
        return dict(zip(cols, row))


def main() -> int:
    print("=" * 78)
    print("=== End-to-End Pipeline Smoke ===")
    print("=" * 78)

    site_id, config = _load_ntecodex_site()
    print(f"Site: ntecodex.com  ({site_id})")
    print(f"DummyAgent invoked with task_type='writing'")
    expected_model = config["text_provider"]["writing_model"]
    print(f"  → Expected model from site_config: {expected_model}")

    llm = get_llm_provider("gemini")
    agent = DummyAgent(llm=llm, site_config=config)

    input_data = {
        "prompt": "Say 'hello world' in five English words. Keep it short.",
        "metadata": {"test_run": True},
    }

    output = agent.run(site_id=site_id, article_id=None, input_data=input_data)
    print(f"  → Agent output keys: {list(output)}")
    print(f"  → text: {output['text']!r}")
    print(f"  → char_count: {output['char_count']}")

    # Find the just-written run row by site_id + agent_name + test marker
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id from agent_runs
             where site_id = %s
               and agent_name = 'writing'
               and input->'metadata'->>'test_run' = 'true'
             order by created_at desc
             limit 1
            """,
            (str(site_id),),
        )
        run_row = cur.fetchone()
    assert run_row, "No agent_runs row found for the test invocation"
    run_id = run_row[0]

    row = _fetch_run(run_id)

    print()
    print("📋 agent_runs row written:")
    print(f"  id           : {row['id']}")
    print(f"  agent_name   : {row['agent_name']}")
    print(f"  status       : {row['status']}")
    print(f"  model        : {row['model']}")
    print(f"  tokens_in    : {row['tokens_in']}")
    print(f"  tokens_out   : {row['tokens_out']}")
    print(f"  cost_usd     : {row['cost_usd']}")
    print(f"  duration_ms  : {row['duration_ms']}")
    print(f"  output       : {row['output']}")

    # Cross-check: the in-memory cost from the LLM call vs what the row stored.
    # The row uses numeric(8,4) which truncates anything below $0.0001 to 0.0000.
    in_memory_cost = sum(c.cost_usd for c in agent._calls)
    print(f"  in-memory cost (float): ${in_memory_cost:.10f}")
    if in_memory_cost > 0 and float(row["cost_usd"] or 0) == 0:
        print("  ⚠️  Note: numeric(8,4) truncated sub-cent cost to 0.0000")
        print("      (real cost was computed correctly; only DB precision is coarse)")

    # Assertions — evidence of a real API call + correct routing + log integrity
    failures: list[str] = []
    if row["status"] != "success":
        failures.append(f"status expected 'success', got {row['status']!r}")
    if row["model"] != expected_model:
        failures.append(f"model expected {expected_model!r}, got {row['model']!r}")
    if row["agent_name"] != "writing":
        failures.append(f"agent_name expected 'writing', got {row['agent_name']!r}")
    if (row["tokens_in"] or 0) <= 0:
        failures.append(f"tokens_in expected > 0 (proves real API call), got {row['tokens_in']}")
    if (row["tokens_out"] or 0) <= 0:
        failures.append(f"tokens_out expected > 0, got {row['tokens_out']}")
    if (row["duration_ms"] or 0) < 100:
        failures.append(f"duration_ms expected ≥ 100 (real network call), got {row['duration_ms']}")
    if in_memory_cost <= 0:
        failures.append(
            f"in-memory cost expected > 0 (price calc), got {in_memory_cost}"
        )

    # Cleanup
    print()
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            delete from agent_runs
             where input->'metadata'->>'test_run' = 'true'
               and agent_name = 'writing'
            """
        )
        deleted = cur.rowcount
    print(f"[Cleanup] Deleted {deleted} test agent_run row(s)")

    if failures:
        print()
        print("❌ Pipeline smoke FAILED:")
        for f in failures:
            print(f"   - {f}")
        return 1

    print()
    print("✅ Pipeline closed loop verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
