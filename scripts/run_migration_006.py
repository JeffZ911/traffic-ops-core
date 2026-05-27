"""One-shot: apply migration 006 (widen articles.article_type CHECK
to include security_cameras article_types for quvii.com).

Without this, content_quvii cron crashes:
  CheckViolation: new row for relation "articles" violates check
  constraint "articles_article_type_check"

Idempotent: re-running just re-applies the same widened constraint.
Non-destructive: never narrows the allowed-type list.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from src.db.client import get_db_connection

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


MIGRATION = Path(__file__).resolve().parent.parent / "src/db/migrations/006_article_types_security_cameras.sql"


def main() -> int:
    sql = MIGRATION.read_text()
    print(f"Applying: {MIGRATION.name}")
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        # The SQL ends with a SELECT that returns the new constraint defn —
        # fetch + print it as a sanity check.
        try:
            result = cur.fetchone()
            if result:
                print(f"✓ New constraint:\n  {result[0]}")
        except Exception:
            pass
    print("Migration 006 applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
