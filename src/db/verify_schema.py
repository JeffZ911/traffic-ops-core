"""
Read-only schema verification — runs after migrate.py.

Checks 9 categories (see CHECKS list below). Each check prints ✅ or ❌
with a Chinese-language explanation so a non-SQL reader can follow.

Usage:
    python -m src.db.verify_schema
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent.parent

# Expected: 13 tables, with their column counts (matches 001_initial_schema.sql)
EXPECTED_TABLES: dict[str, int] = {
    "sites":              8,
    "keywords":           14,
    "articles":           19,
    "article_keywords":   3,
    "agent_runs":         14,
    "images":             11,
    "metrics_raw":        6,
    "metrics_daily":      25,
    "ad_campaigns":       9,
    "alerts":             11,
    "agent_runs_summary": 10,
    "daily_reports":      8,
    "model_catalog":      20,
}

# Tables that must have a CHECK constraint (table → expected count of CHECKs)
EXPECTED_CHECKS: dict[str, int] = {
    "sites":         1,  # status
    "keywords":      1,  # status
    "articles":      2,  # status, article_type
    "agent_runs":    1,
    "metrics_raw":   1,
    "alerts":        1,  # level
    "model_catalog": 2,  # modality, status
}
EXPECTED_CHECK_TOTAL = sum(EXPECTED_CHECKS.values())  # 9

EXPECTED_POLICIES = 13   # one SELECT policy per table

EXPECTED_TRIGGERS = {
    "trg_sites_set_updated_at": "sites",
    "trg_keywords_set_updated_at": "keywords",
    "trg_articles_set_updated_at": "articles",
}

EXPECTED_MODEL_IDS = [
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    # gemini-3.1-flash-lite-preview retired by Google 2026-05-26 — removed
    # so verify_schema flags any lingering reference to it.
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image-preview",
    "gemini-3-pro-image-preview",
]

EXPECTED_KEY_INDEXES = [
    "idx_keywords_priority",
    "idx_articles_type",
    "idx_articles_site_status",
    "idx_agent_runs_cleanup",
    "idx_alerts_site_unack",
    "uq_model_catalog",
    "idx_model_catalog_modality_status",
]


def _ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def _bad(msg: str) -> None:
    print(f"  ❌ {msg}")


def main() -> int:
    load_dotenv(ROOT / ".env")
    dsn = os.getenv("SUPABASE_DB_URL")
    if not dsn:
        print("❌ SUPABASE_DB_URL not set in .env", file=sys.stderr)
        return 2

    failures: list[str] = []

    print("🔎 Schema verification — connecting...")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:

        # -------------------------------------------------------------------
        # (a) 13 tables exist
        # -------------------------------------------------------------------
        print("\n[a] 13 张表都存在")
        cur.execute(
            "select table_name from information_schema.tables "
            "where table_schema = 'public' order by table_name"
        )
        actual_tables = {r[0] for r in cur.fetchall()}
        missing = set(EXPECTED_TABLES) - actual_tables
        extra = actual_tables - set(EXPECTED_TABLES)
        if not missing and not extra:
            _ok(f"全部 13 张表都已建好：{', '.join(sorted(EXPECTED_TABLES))}")
        else:
            if missing:
                _bad(f"缺表：{sorted(missing)}")
                failures.append(f"missing tables: {sorted(missing)}")
            if extra:
                _bad(f"多余的表（不在预期里）：{sorted(extra)}")
                failures.append(f"unexpected tables: {sorted(extra)}")

        # -------------------------------------------------------------------
        # (b) Column count per table
        # -------------------------------------------------------------------
        print("\n[b] 每张表的列数符合预期")
        cur.execute(
            "select table_name, count(*) from information_schema.columns "
            "where table_schema = 'public' group by table_name"
        )
        actual_cols = dict(cur.fetchall())
        all_match = True
        for tbl, expected in EXPECTED_TABLES.items():
            got = actual_cols.get(tbl, 0)
            if got != expected:
                _bad(f"{tbl}：预期 {expected} 列，实际 {got} 列")
                failures.append(f"{tbl} columns mismatch ({got} vs {expected})")
                all_match = False
        if all_match:
            _ok("13 张表的列数全部匹配预期")

        # -------------------------------------------------------------------
        # (c) 9 CHECK constraints in place
        # -------------------------------------------------------------------
        print("\n[c] 9 个 CHECK 约束都生效")
        cur.execute(
            """
            select c.conrelid::regclass::text as tbl, c.conname
              from pg_constraint c
              join pg_class t on t.oid = c.conrelid
              join pg_namespace n on n.oid = t.relnamespace
             where c.contype = 'c'
               and n.nspname = 'public'
               and t.relname = any(%s)
            """,
            (list(EXPECTED_CHECKS.keys()),),
        )
        rows = cur.fetchall()
        per_table: dict[str, int] = {}
        for tbl, _ in rows:
            per_table[tbl] = per_table.get(tbl, 0) + 1

        all_match = True
        for tbl, expected in EXPECTED_CHECKS.items():
            got = per_table.get(tbl, 0)
            if got != expected:
                _bad(f"{tbl}：预期 {expected} 个 CHECK，实际 {got} 个")
                failures.append(f"{tbl} CHECK count {got} vs {expected}")
                all_match = False
        if all_match:
            _ok(f"9 个 CHECK 约束全部存在（合计 {sum(per_table.values())}）")

        # -------------------------------------------------------------------
        # (d) RLS enabled on all 13 tables
        # -------------------------------------------------------------------
        print("\n[d] 13 张表都启用了 RLS")
        cur.execute(
            "select tablename, rowsecurity from pg_tables "
            "where schemaname = 'public' and tablename = any(%s)",
            (list(EXPECTED_TABLES.keys()),),
        )
        rls_state = dict(cur.fetchall())
        not_enabled = [t for t in EXPECTED_TABLES if not rls_state.get(t, False)]
        if not not_enabled:
            _ok("13 张表的 row-level security 全部已启用")
        else:
            _bad(f"未启用 RLS 的表：{not_enabled}")
            failures.append(f"RLS not enabled: {not_enabled}")

        # -------------------------------------------------------------------
        # (e) 13 RLS policies present
        # -------------------------------------------------------------------
        print(f"\n[e] {EXPECTED_POLICIES} 个 RLS policy 都建好")
        cur.execute(
            "select tablename, policyname from pg_policies "
            "where schemaname = 'public' order by tablename"
        )
        policies = cur.fetchall()
        # Each of our 13 tables should have at least one SELECT policy
        tables_with_policies = {t for t, _ in policies}
        missing_pol = set(EXPECTED_TABLES) - tables_with_policies
        if len(policies) >= EXPECTED_POLICIES and not missing_pol:
            _ok(f"共 {len(policies)} 个 policy；每张表都至少有一条")
        else:
            _bad(f"policy 数量 {len(policies)}，期望 ≥ {EXPECTED_POLICIES}")
            if missing_pol:
                _bad(f"缺 policy 的表：{sorted(missing_pol)}")
            failures.append(f"policies: {len(policies)} (need {EXPECTED_POLICIES})")

        # -------------------------------------------------------------------
        # (f) set_updated_at function + 3 triggers
        # -------------------------------------------------------------------
        print("\n[f] set_updated_at() 函数 + 3 个触发器")
        cur.execute(
            "select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
            "where n.nspname = 'public' and p.proname = 'set_updated_at'"
        )
        if cur.fetchone():
            _ok("set_updated_at() 函数存在")
        else:
            _bad("set_updated_at() 函数缺失")
            failures.append("set_updated_at function missing")

        cur.execute(
            """
            select t.tgname, c.relname
              from pg_trigger t
              join pg_class c on c.oid = t.tgrelid
              join pg_namespace n on n.oid = c.relnamespace
             where n.nspname = 'public'
               and not t.tgisinternal
               and t.tgname = any(%s)
            """,
            (list(EXPECTED_TRIGGERS.keys()),),
        )
        actual_trigs = dict(cur.fetchall())
        all_match = True
        for trg_name, expected_table in EXPECTED_TRIGGERS.items():
            if actual_trigs.get(trg_name) != expected_table:
                _bad(f"触发器 {trg_name} 缺失或挂错表（实际：{actual_trigs.get(trg_name)}）")
                failures.append(f"trigger {trg_name} mismatch")
                all_match = False
        if all_match:
            _ok("3 个 updated_at 触发器全部存在并挂在正确的表上")

        # -------------------------------------------------------------------
        # (g) model_catalog has 6 seed rows
        # -------------------------------------------------------------------
        print("\n[g] model_catalog 初始 seed 数据 = 6 行")
        cur.execute("select count(*) from model_catalog")
        cnt = cur.fetchone()[0]
        if cnt == 6:
            _ok("model_catalog 共 6 行（与 spec 一致）")
        else:
            _bad(f"model_catalog 实际 {cnt} 行，预期 6 行")
            failures.append(f"model_catalog rows: {cnt}")

        # -------------------------------------------------------------------
        # (h) 6 model_id strings match spec exactly
        # -------------------------------------------------------------------
        print("\n[h] 6 个 model_id 拼写与 CODE-SPEC §2.2.13 一致")
        cur.execute("select model_id from model_catalog order by model_id")
        actual_ids = sorted(r[0] for r in cur.fetchall())
        expected_ids = sorted(EXPECTED_MODEL_IDS)
        if actual_ids == expected_ids:
            _ok("6 个 model_id 全部精确匹配")
        else:
            _bad(f"差异 — 多/少：{set(actual_ids).symmetric_difference(expected_ids)}")
            failures.append("model_id mismatch")

        # -------------------------------------------------------------------
        # (i) Key indexes exist
        # -------------------------------------------------------------------
        print("\n[i] 关键索引都存在")
        cur.execute(
            "select indexname from pg_indexes "
            "where schemaname = 'public' and indexname = any(%s)",
            (EXPECTED_KEY_INDEXES,),
        )
        actual_idx = {r[0] for r in cur.fetchall()}
        missing_idx = set(EXPECTED_KEY_INDEXES) - actual_idx
        if not missing_idx:
            _ok(f"{len(EXPECTED_KEY_INDEXES)} 个关键索引全部存在")
        else:
            _bad(f"缺索引：{sorted(missing_idx)}")
            failures.append(f"missing indexes: {sorted(missing_idx)}")

    print("\n" + "=" * 78)
    if not failures:
        print("✅ Schema 部署成功（9 项检查全部通过）")
        return 0
    print(f"❌ Schema 部署有问题（{len(failures)} 项失败）：")
    for f in failures:
        print(f"   - {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
