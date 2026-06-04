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

PLATFORMS = ("Featured.com（主入口/发现）· Qwoted（投递落地+补充搜索）· "
             "SourceBottle · Help a B2B Writer · #JournoRequest (X/Bluesky)")

# 主入口 Featured 对话框直接粘贴这句（圈定领域 + 排除杂项 → AI 排序更准）
FEATURED_PROMPT = (
    "Find journalist requests where a CONSUMER home-security expert can comment: "
    "home security cameras, video doorbells, smart locks, package/porch theft, "
    "DIY/renter security, camera privacy & subscriptions. EXCLUDE: enterprise/B2B "
    "cybersecurity, MSP, fintech, criminology/true-crime, pure data-privacy law."
)


def haro_detail(ledger_url: str) -> str:
    return (
        "通过回答记者提问挣编辑类反向链接（earned editorial backlinks）—— 对新站"
        "最安全、最便宜、ROI 最高的权威建设。每天约 15 分钟。"
        "署名：Jeff Zen, Founder & Editor, Quvii。\n\n"
        "平台分工（重要）：Featured = 主入口（它聚合 Qwoted/HARO/播客/约稿，一处看全网）；"
        "Qwoted = 投递落地（标 QWOTED 的请求要点 Open on Qwoted 回去投）+ 补充搜索。"
        "两个账号都别停。\n\n"
        "今天就做 (DO THIS TODAY):\n"
        "  1. 打开 Featured.com（用 jeff@quvii.com 登录），在对话框粘贴这句精准检索词：\n"
        f"     « {FEATURED_PROMPT} »\n"
        "  2. 出结果后嫌不准，点 Refine opportunities 再补一句"
        "'only consumer home security, exclude B2B & cybersecurity'。\n"
        "  3. 人工终审（30 秒，省不掉）：hashtag/来源是 #HomeSecurity #SmartHome "
        "#HomeSafety → 投；是 #MSP #Cybersecurity #Fintech #TrueCrime → 跳。\n"
        "  4. 挑 1–2 个能专业回答的：对口且按钮是 Draft Pitch/Send Email → 直接在"
        " Featured 投；标 QWOTED → 点 Open on Qwoted 回 Qwoted 投。\n"
        "  5. 从 playbook 选一个模板，把头两句改成贴合记者的具体问题，80–130 词，"
        "纯文本；结尾必带署名块：\n"
        "     '— Jeff Zen, Founder & Editor, Quvii (https://quvii.com)'\n"
        "  6. 顺手在 Qwoted 用 48h 筛选翻一遍（捞 Featured 没抓进来的新鲜请求）。\n"
        "  7. 每条投递都记进台账。\n\n"
        "省额度：Qwoted 免费额度有限 —— 只在'对口 + 没过期'的请求上花，过期/B2B 一律不投。\n\n"
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
