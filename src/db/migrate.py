"""
Run a single SQL migration file against SUPABASE_DB_URL.

Usage:
    python -m src.db.migrate                          # runs 001_initial_schema.sql
    python -m src.db.migrate 002_xxx.sql              # positional: runs the named file
    python -m src.db.migrate --file 002_xxx.sql       # explicit flag, same effect

Behavior:
    - Loads .env from traffic-ops-core/ root.
    - Splits the file into top-level statements (respects $$ blocks, comments).
    - Wraps the whole run in ONE transaction: any failure → full rollback.
    - Prints a one-line label per statement (✅/❌).
    - On success, prints a summary: tables / indexes / policies / triggers
      created and rows inserted.

Never logs the full DB URL or any credential.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from src.db._sql_split import split_sql_statements


HERE = Path(__file__).resolve().parent
MIGRATIONS_DIR = HERE / "migrations"
ROOT = HERE.parent.parent  # traffic-ops-core/


def _label(stmt: str, max_len: int = 90) -> str:
    """Short human-readable label for a SQL statement."""
    # collapse whitespace, strip leading comment lines
    lines = [ln for ln in stmt.splitlines() if not ln.strip().startswith("--")]
    one = " ".join(" ".join(lines).split())
    if len(one) > max_len:
        one = one[: max_len - 1] + "…"
    return one


def _classify(stmt: str) -> str:
    """Return a coarse category for summary stats. Skips leading comment lines."""
    first_code = ""
    for line in stmt.splitlines():
        s = line.strip()
        if not s or s.startswith("--"):
            continue
        first_code = s.lower()
        break
    head = first_code
    if head.startswith("create table"):
        return "table"
    if head.startswith("create index") or head.startswith("create unique index"):
        return "index"
    if head.startswith("create policy"):
        return "policy"
    if head.startswith("create trigger"):
        return "trigger"
    if head.startswith("create or replace function") or head.startswith("create function"):
        return "function"
    if head.startswith("alter table") and "row level security" in head:
        return "rls_enable"
    if head.startswith("insert into"):
        return "insert"
    if head.startswith("create extension"):
        return "extension"
    return "other"


def _safe_dsn_host(dsn: str) -> str:
    """Extract just the host:port for log messages — never the password."""
    m = re.search(r"@([^/]+)", dsn)
    return m.group(1) if m else "<unknown host>"


def main() -> int:
    load_dotenv(ROOT / ".env")
    dsn = os.getenv("SUPABASE_DB_URL")
    if not dsn:
        print("❌ SUPABASE_DB_URL not set in .env", file=sys.stderr)
        return 2

    # Argument parsing: support both positional and --file
    args = sys.argv[1:]
    fname = "001_initial_schema.sql"
    if args:
        if args[0] == "--file":
            if len(args) < 2:
                print("❌ --file requires a filename", file=sys.stderr)
                return 2
            fname = args[1]
        else:
            fname = args[0]
    sql_path = MIGRATIONS_DIR / fname
    if not sql_path.exists():
        print(f"❌ Migration file not found: {sql_path}", file=sys.stderr)
        return 2

    sql_text = sql_path.read_text(encoding="utf-8")
    statements = split_sql_statements(sql_text)

    print(f"📂 Migration: {fname}")
    print(f"🔌 Connecting to {_safe_dsn_host(dsn)} ...")
    print(f"📝 Statements to execute: {len(statements)}")
    print("-" * 78)

    counts: dict[str, int] = {}
    seed_rows = 0

    try:
        with psycopg.connect(dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                for idx, stmt in enumerate(statements, 1):
                    cat = _classify(stmt)
                    label = _label(stmt)
                    try:
                        cur.execute(stmt)
                    except Exception as e:
                        print(f"❌ [{idx:02d}/{len(statements)}] {label}")
                        print(f"   error: {e}")
                        conn.rollback()
                        print("-" * 78)
                        print("🛑 Rolled back. No changes applied.")
                        return 1

                    if cat == "insert":
                        # rowcount = number of seed rows inserted
                        seed_rows += cur.rowcount or 0

                    counts[cat] = counts.get(cat, 0) + 1
                    print(f"✅ [{idx:02d}/{len(statements)}] [{cat}] {label}")

            conn.commit()
    except psycopg.OperationalError as e:
        print(f"❌ Connection failed: {e}", file=sys.stderr)
        return 3

    print("-" * 78)
    print("✅ Migration committed successfully.")
    print()
    print("📊 Summary")
    print(f"   tables created:      {counts.get('table', 0)}")
    print(f"   indexes created:     {counts.get('index', 0)}")
    print(f"   functions created:   {counts.get('function', 0)}")
    print(f"   triggers created:    {counts.get('trigger', 0)}")
    print(f"   RLS enables:         {counts.get('rls_enable', 0)}")
    print(f"   policies created:    {counts.get('policy', 0)}")
    print(f"   extensions ensured:  {counts.get('extension', 0)}")
    print(f"   seed rows inserted:  {seed_rows}")
    if counts.get("other", 0):
        print(f"   other statements:    {counts['other']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
