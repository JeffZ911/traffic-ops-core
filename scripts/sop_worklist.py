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
        "EARNED editorial backlinks by answering journalist source requests — "
        "the safest, cheapest, highest-ROI authority play for a young site. "
        "~15 min/day. Signed as: Jeff Zen, Founder & Editor, Quvii.\n\n"
        "DO THIS TODAY:\n"
        "  1. Open Featured.com + Qwoted (signed in as jeff@quvii.com).\n"
        "  2. Filter for: home security / smart home / privacy / consumer tech.\n"
        "  3. Answer 1-2 queries you can genuinely speak to. Pick a template "
        "from the playbook, customize the first two lines to the journalist's "
        "EXACT question, 80-130 words, plain text.\n"
        "  4. END every reply with the attribution block:\n"
        "     '— Jeff Zen, Founder & Editor, Quvii (https://quvii.com)'\n"
        "  5. Log every pitch in the ledger.\n\n"
        f"PLATFORMS: {PLATFORMS}\n"
        "PLAYBOOK (bio + 5 answer templates + full SOP): docs/HARO_PLAYBOOK.md\n"
        f"LEDGER (log every pitch): {ledger_url}\n\n"
        "RULES: reply within ~30 min of a request; be specific, never sell. "
        "Slow burn — 0-2 placements/week at first is normal; the bi-weekly "
        "indexing census shows if Quvii's coverage moves."
    )


def guestpost_detail() -> str:
    return (
        "PAID guest-post pilot — a SMALL, safe top-up to the earned (HARO) "
        "links. Buy DIRECT from established vendors (skip Fiverr middlemen). "
        "Gray-hat: keep it small + relevant; never bulk.\n\n"
        "DO THIS (when ready, not daily):\n"
        "  1. Use FatJoe (fatjoe.com/blogger-outreach) or Rhino Rank "
        "(rhinorank.io). Both are legit white-label vendors.\n"
        "  2. Order just 3-5 placements for Quvii. Hard filters:\n"
        "       - niche = home security / smart home / consumer tech\n"
        "       - min organic traffic >= ~1,000/mo (ask for the Ahrefs number)\n"
        "       - dofollow link\n"
        "  3. Anchor text: mostly branded ('Quvii') or natural phrases "
        "('home security guide'); avoid exact-match commercial anchors.\n"
        "  4. Point links at PILLAR pages, not thin posts:\n"
        "       /learn/what-is-poe-camera-how-it-works\n"
        "       /blog/best-outdoor-security-camera-without-subscription\n"
        "       /learn/are-wireless-cameras-safe-from-hackers\n"
        "  5. REJECT any placement on a low-traffic / general-topic / 'write "
        "for us' spam site. Vet each URL before approving.\n\n"
        "BUDGET: ~$150-250/link is the real market rate. Cap the pilot at "
        "$500-750. Verify impact in the bi-weekly census before scaling.\n"
        "AVOID entirely: bulk packages (1000+ links), PBNs, web2.0, "
        "directory/profile links, press-release links (nofollow, no value)."
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="quvii.com")
    args = ap.parse_args()

    ledger_url = os.getenv("HARO_LEDGER_URL") or "see docs/HARO_PLAYBOOK.md → Ledger"

    r1 = upsert_open_task(
        f"HARO / digital-PR — daily 15 min ({args.site} pilot)",
        haro_detail(ledger_url),
        priority="normal", category="authority", site_domain=args.site,
    )
    r2 = upsert_open_task(
        f"Guest-post direct-buy SOP ({args.site} pilot)",
        guestpost_detail(),
        priority="low", category="authority", site_domain=args.site,
    )
    print(f"  ✓ {args.site}: HARO card {r1}, guest-post card {r2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
