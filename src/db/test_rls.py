"""
RLS 实战演练 — 证明 RLS 不只是语法对、而是真正生效。

流程：
    a) 用 SUPABASE_DB_URL（service_role 等价权限）插入一行 sites 测试数据
    b) 如果 .env 提供了 SUPABASE_URL + SUPABASE_ANON_KEY：
       通过 PostgREST 用 anon 身份查询 sites → 期望 0 行（RLS 拦住未登录用户）
       否则：跳过此步并报告
    c) 直连 SUPABASE_DB_URL select sites → 期望能看到刚插入那行（service_role 绕过 RLS）
    d) 删除测试数据

Usage:
    python -m src.db.test_rls
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import psycopg
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent.parent

TEST_DOMAIN = "__rls_test__.example.com"
TEST_SITE_NAME = "RLS Drill"
# owner_id stays NULL: sites.owner_id has an FK to auth.users(id), so any
# non-null UUID we make up would violate the FK. NULL is a valid value
# (column is nullable) and is sufficient to exercise RLS — the policy
# `owner_id = auth.uid()` will not match for any caller, which is the
# correct expectation for an anon read attempt.


def main() -> int:
    load_dotenv(ROOT / ".env")
    dsn = os.getenv("SUPABASE_DB_URL")
    sb_url = os.getenv("SUPABASE_URL")
    anon_key = os.getenv("SUPABASE_ANON_KEY")

    if not dsn:
        print("❌ SUPABASE_DB_URL not set in .env", file=sys.stderr)
        return 2

    print("🔬 RLS 实战演练开始")
    print("-" * 78)

    test_id = str(uuid.uuid4())
    cleanup_needed = False
    findings: list[str] = []

    try:
        # ---- (a) Insert test row via direct DB (service_role-equivalent) ----
        print("\n[a] 直连数据库插入一条测试 sites 行（service_role 绕过 RLS，应当成功）")
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into sites (id, domain, site_name, owner_id)
                values (%s, %s, %s, NULL)
                """,
                (test_id, TEST_DOMAIN, TEST_SITE_NAME),
            )
            cleanup_needed = True
            print(f"   ✅ 插入成功（id={test_id[:8]}…）")

        # ---- (b) Anon read via PostgREST ----
        print("\n[b] 用 anon key 通过 PostgREST 查询同一行（应当被 RLS 拦住，返回 0 行）")
        if not (sb_url and anon_key):
            print("   ⏭️  跳过：缺 SUPABASE_URL 或 SUPABASE_ANON_KEY（可后续补加）")
            findings.append("RLS 演练部分跳过：缺 anon key（无法验证 RLS 实际拦截行为）")
        else:
            url = f"{sb_url.rstrip('/')}/rest/v1/sites"
            r = httpx.get(
                url,
                params={
                    "select": "id,domain",
                    "domain": f"eq.{TEST_DOMAIN}",
                },
                headers={
                    "apikey": anon_key,
                    "Authorization": f"Bearer {anon_key}",
                },
                timeout=10,
            )
            if r.status_code != 200:
                print(f"   ❌ HTTP {r.status_code}: {r.text[:200]}")
                findings.append(f"PostgREST anon query failed: HTTP {r.status_code}")
            else:
                rows = r.json()
                if rows == []:
                    print("   ✅ anon 用户看到 0 行（RLS 实际生效）")
                else:
                    print(f"   ❌ anon 用户竟然看到 {len(rows)} 行 — RLS 没拦住！")
                    findings.append(
                        f"RLS 失效：未登录用户能读到 sites（看到 {len(rows)} 行）"
                    )

        # ---- (c) Direct DB read should still see it ----
        print("\n[c] 直连数据库查同一行（应当看到 1 行，证明数据确实写进去了）")
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "select id, domain, site_name from sites where domain = %s",
                (TEST_DOMAIN,),
            )
            rows = cur.fetchall()
            if len(rows) == 1:
                print(f"   ✅ 看到 1 行：{rows[0][1]} / {rows[0][2]}")
            else:
                print(f"   ❌ 看到 {len(rows)} 行（预期 1）")
                findings.append(f"service_role 读不到测试行（{len(rows)} 行）")

    finally:
        # ---- (d) Cleanup ----
        if cleanup_needed:
            print("\n[d] 清理测试数据")
            try:
                with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
                    cur.execute("delete from sites where domain = %s", (TEST_DOMAIN,))
                    print(f"   ✅ 已删除（受影响行数：{cur.rowcount}）")
            except Exception as e:
                print(f"   ⚠️  清理失败：{e}")
                findings.append(
                    f"测试数据清理失败：{e}（请手动 DELETE FROM sites WHERE domain='{TEST_DOMAIN}'）"
                )

    print("\n" + "=" * 78)
    if not findings:
        print("✅ RLS 实际生效（service_role 可读写，anon 被拦截）")
        return 0
    print("⚠️  RLS 演练结果如下：")
    for f in findings:
        print(f"   - {f}")
    # If only the "skipped" finding is present, treat as soft-pass
    blockers = [f for f in findings if "跳过" not in f]
    if not blockers:
        print("\n（仅缺 anon key，未能验证拦截行为；service_role 路径已确认正常）")
        return 0
    print("\n❌ RLS 配置存在问题（见上）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
