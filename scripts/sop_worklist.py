"""Clear, methodology-driven SOP cards → dashboard /todos.

Replaces the old vague "Authority · Community/Outreach/Asset" cards (generic
lists with no clear action) with concrete, do-this-now SOPs. Idempotent
(stable titles → upsert_open_task updates in place, never duplicates).

Cards written (Quvii pilot):
  1. HARO / digital-PR daily SOP   — earned editorial links (safest, cheapest)
  2. Guest-post direct-buy SOP     — how to buy a small, safe paid pilot

The concrete GSC Request-Indexing card is handled by daily_indexing_worklist.

Usage:
  python -m scripts.sop_worklist --site quvii.com
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from src.utils.ops_tasks import upsert_open_task  # noqa: E402

PLATFORMS = ("Featured.com · Qwoted · SourceBottle · Help a B2B Writer · "
             "#JournoRequest (X/Bluesky)")


def haro_detail(ledger_url: str) -> str:
    return (
        "通过回答记者提问挣编辑类反向链接（earned editorial backlinks）—— 对新站"
        "最安全、最便宜、ROI 最高的权威建设。每天约 15 分钟。"
        "署名：Jeff Zen, Founder & Editor, Quvii。\n\n"
        "今天就做 (DO THIS TODAY):\n"
        "  1. 打开 Featured.com + Qwoted（用 jeff@quvii.com 登录）。\n"
        "  2. 筛选：家用安防 / 智能家居 / 隐私 / 消费科技 相关的记者请求。\n"
        "  3. 挑 1–2 个你能专业回答的，从 playbook 选一个模板，把头两句改成贴合"
        "记者的具体问题，80–130 词，纯文本。\n"
        "  4. 每条回复结尾必带署名块 (attribution block)：\n"
        "     '— Jeff Zen, Founder & Editor, Quvii (https://quvii.com)'\n"
        "  5. 每条投递都记进台账。\n\n"
        f"平台 PLATFORMS: {PLATFORMS}\n"
        "模板手册 PLAYBOOK（bio + 10 个回答模板 + 完整 SOP）: docs/HARO_PLAYBOOK.md\n"
        f"台账 LEDGER（每条都记）: {ledger_url}\n\n"
        "规则：收到请求 ~30 分钟内回复；要具体、别推销。慢功夫 —— 头几周每周 0–2 "
        "条命中是正常的；双周普查会告诉你 Quvii 的收录有没有动。"
    )


def guestpost_detail() -> str:
    return (
        "付费 guest-post 试点 —— 对挣来的（HARO）链接做少量、安全的补充。直接找正规"
        "服务商（跳过 Fiverr 中间商）。灰帽 (gray-hat)：少量 + 相关，绝不走量。\n\n"
        "什么时候做（不是每天）:\n"
        "  1. 用 FatJoe (fatjoe.com/blogger-outreach) 或 Rhino Rank "
        "(rhinorank.io)，都是正规白标商。\n"
        "  2. Quvii 只下 3–5 条。硬性过滤：\n"
        "       - niche = 家用安防 / 智能家居 / 消费科技\n"
        "       - 月自然流量 ≥ ~1,000（让对方给 Ahrefs 数字）\n"
        "       - dofollow 链接\n"
        "  3. 锚文本 (anchor text)：以品牌词（'Quvii'）或自然短语（'home security "
        "guide'）为主；避免精确匹配的商业词。\n"
        "  4. 链接指向支柱页 (pillar pages)，不是薄文：\n"
        "       /learn/what-is-poe-camera-how-it-works\n"
        "       /blog/best-outdoor-security-camera-without-subscription\n"
        "       /learn/are-wireless-cameras-safe-from-hackers\n"
        "  5. 拒绝任何低流量 / 泛主题 / 'write for us' 垃圾站。每条 URL 批准前先核。\n\n"
        "预算：单条真实市场价 ~$150–250。试点封顶 $500–750。先用双周普查看效果再放量。\n"
        "绝对避免：批量套餐（1000+ 链接）、PBN、web2.0、目录/profile 链接、"
        "新闻稿链接（nofollow、无价值）。"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="quvii.com")
    args = ap.parse_args()

    ledger_url = os.getenv("HARO_LEDGER_URL") or "see docs/HARO_PLAYBOOK.md → Ledger"

    r1 = upsert_open_task(
        f"HARO / 数字PR — 每日 15 分钟 ({args.site} 试点)",
        haro_detail(ledger_url),
        priority="normal", category="authority", site_domain=args.site,
    )
    r2 = upsert_open_task(
        f"Guest-post 直采 SOP ({args.site} 试点)",
        guestpost_detail(),
        priority="low", category="authority", site_domain=args.site,
    )
    print(f"  ✓ {args.site}: HARO card {r1}, guest-post card {r2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
