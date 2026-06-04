"""Clear, methodology-driven SOP cards → dashboard /todos.

Strategy pivot (2026-06-04): GSC diagnosis for quvii.com shows the only
indexing blocker is **"Discovered – currently not indexed" (18 pages)** —
i.e. Google hasn't even CRAWLED the pages; it's rationing crawl budget on a
no-trust new site. This is a trust / crawl-demand wall, NOT a content-quality
verdict. So the two levers that actually move it:

  - Guest-post direct-buy (THIS card) — a few real editorial links raise
    trust / crawl demand (do this NOW, per founder decision).
  - Daily Request-indexing lives in daily_indexing_worklist.py (per-site
    cards with copy-paste URL lists for GSC).

The old HARO / digital-PR card was RETIRED here (too slow / luck-based to be a
primary lever) — main() also auto-resolves any open HARO card so it leaves the
board.

Idempotent (stable titles → upsert_open_task updates in place, never dupes).

Usage:
  python -m scripts.sop_worklist --site quvii.com
"""
from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from src.utils.ops_tasks import resolve_open_task, upsert_open_task  # noqa: E402

# Retired cards — auto-resolve so they leave the /todos board.
# (The daily Request-indexing worklist now lives in daily_indexing_worklist.py
#  as per-site cards; the single-site card once made here is retired too.)
RETIRED_TITLES = (
    "HARO / 数字PR — 每日 15 分钟 ({site} 试点)",
    "HARO / 数字PR — 每日 15 分钟 (quvii.com 试点)",
    "Guest-post 直采 SOP ({site} 试点)",
    "Guest-post 直采 SOP (quvii.com 试点)",
    "每日 Request indexing — 强制抓取 ({site})",
    "每日 Request indexing — 强制抓取 (quvii.com)",
)

# quvii.com pillar pages — what guest-post links should point at (NOT thin pages).
PILLAR_PAGES = (
    "/learn/what-is-poe-camera-how-it-works",
    "/blog/best-outdoor-security-camera-without-subscription",
    "/learn/are-wireless-cameras-safe-from-hackers",
)


def guestpost_detail() -> str:
    pillars = "\n".join(f"       {p}" for p in PILLAR_PAGES)
    return (
        "现在就做（founder 决定）：少量、安全的付费 guest-post，给新站灌入前几条"
        "真实编辑链接，抬高信任 / 抓取需求 —— 直接对着 'Discovered–not indexed' "
        "下药。直接找正规白标商（跳过 Fiverr 中间商）。灰帽：少量 + 相关，绝不走量。\n\n"
        "执行步骤:\n"
        "  1. 用 FatJoe (fatjoe.com/blogger-outreach) 或 Rhino Rank "
        "(rhinorank.io)，都是正规白标商。\n"
        "  2. Quvii 只下 3–5 条。硬性过滤:\n"
        "       - niche = 家用安防 / 智能家居 / 消费科技\n"
        "       - 月自然流量 ≥ ~1,000（让对方给 Ahrefs 数字）\n"
        "       - dofollow 链接\n"
        "  3. 锚文本 (anchor text)：以品牌词（'Quvii'）或自然短语（'home security "
        "guide'）为主；避免精确匹配的商业词。\n"
        "  4. 链接指向支柱页 (pillar pages)，不是薄文:\n"
        f"{pillars}\n"
        "  5. 拒绝任何低流量 / 泛主题 / 'write for us' 垃圾站。每条 URL 批准前先核。\n\n"
        "预算：单条真实市场价 ~$150–250。试点封顶 $500–750。先用双周普查看效果再放量。\n"
        "绝对避免：批量套餐（1000+ 链接）、PBN、web2.0、目录/profile 链接、"
        "新闻稿链接（nofollow、无价值）。"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="quvii.com")
    args = ap.parse_args()

    # Retire superseded cards (HARO, old guest-post SOP, the single-site
    # request-indexing card — daily_indexing_worklist.py owns indexing now).
    resolved = 0
    for tmpl in RETIRED_TITLES:
        resolved += resolve_open_task(tmpl.format(site=args.site), site_domain=args.site)

    r2 = upsert_open_task(
        f"Guest-post 直采 — 现在就做 ({args.site})",
        guestpost_detail(),
        priority="high", category="authority", site_domain=args.site,
    )
    print(f"  ✓ {args.site}: retired {resolved} card(s); guest-post {r2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
