"""One-shot pool-cleanup: verify every planned keyword's specific
proper nouns against real NTE info via Google-grounded LLM.

For each keyword:
  1. Detect specific proper nouns (capitalized 4+ char tokens that
     aren't `NTE` / `Neverness`). If none, mark `entity_status=general`
     and keep (these are abstract / mechanic / how-to questions that
     don't risk fabrication).
  2. If proper nouns present, ask Pro+grounded LLM to verify each one.
     Strict: only 'real' if the LLM can cite an actual NTE source for
     the entity. Any "I'm not sure" → 'fabricated'.
  3. Output: list of {keyword_id, keyword, entity_status,
     fabricated_entities, verdict}.

Does NOT modify the DB. The caller (you) decides which rows to archive.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.agents._json_extract import extract_json
from src.db.client import get_db_connection
from src.utils.llm import get_llm_provider


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# Tokens we ignore when scanning for "capitalized proper nouns" — these
# show up in NTE keyword text but aren't entity names.
COMMON_TOKEN_WHITELIST = {
    "NTE", "Neverness", "Everness", "DPS", "DPS,", "PvE", "PvP", "F2P",
    "iOS", "Android", "PC", "RPG", "FAQ", "TLDR",
}

# Pattern: ≥4-char tokens with leading uppercase or all-caps. Catches
# "Minerva", "Nanally", "Frost Guardian" but not "the", "build", "nte".
PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-zA-Z]{3,}\b")


VERIFY_PROMPT = """You are a fact-checker for a Neverness to Everness (NTE, released
2026-04-29) guide site. The keyword below is a search query — players
use lowercase. It MAY contain specific entities (character / weapon /
Arc / boss / mechanic names) hidden in lowercase, OR it may be a
general topic (tier list, build guide, beginner reroll, etc.).

Your job: identify any tokens in the keyword that LOOK like specific
named entities (character/weapon/boss/mechanic NAMES — not categories
like "dps" or "healer" or "build"), and verify EACH one against real
NTE info via Google Search.

Keyword: {keyword!r}

Rules:
- 'real' ONLY if you can find at least one credible source (official
  game site, prydwen.gg, Game8, Reddit r/NeverNess, IGN, GameSpot, or
  similar gaming news) that mentions this entity in the context of NTE.
- 'fabricated' if your search shows the name does not exist in NTE, OR
  if your search is inconclusive. Be CONSERVATIVE — when in doubt,
  fabricated wins.
- 'general' is for ordinary words like "DPS", "tier list", "build" —
  these aren't entities and don't need verification.

Use Google Search to verify. Reply ONLY with JSON (no markdown fence):
{{
  "candidate_nouns": ["minerva"],  // extracted from the keyword
  "per_noun": [
    {{"noun": "minerva", "verdict": "fabricated",
      "evidence": "no NTE search results mention a character named Minerva"}}
  ],
  "entity_status": "real" | "fabricated" | "general" | "mixed",
  "verdict": "keep" | "archive",
  "reason": "<one sentence>"
}}

Decision rules:
- No candidate nouns at all (pure category query) → entity_status='general', verdict='keep'
- All candidate nouns 'real' → entity_status='real', verdict='keep'
- ANY noun 'fabricated' → verdict='archive' (be CONSERVATIVE; inconclusive
  searches count as fabricated)
"""


def extract_proper_nouns(keyword: str) -> list[str]:
    """Return distinct capitalized 4+ char tokens, minus the whitelist."""
    raw = PROPER_NOUN_RE.findall(keyword)
    return [t for t in dict.fromkeys(raw) if t not in COMMON_TOKEN_WHITELIST]


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--budget-usd", type=float, default=1.00,
                   help="Hard cap; abort if cumulative cost exceeds this")
    args = p.parse_args()

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id, config from sites where domain='ntecodex.com' limit 1")
        site_id, config = cur.fetchone()
        cur.execute(
            """
            select id, keyword, source, priority_score
              from keywords
             where site_id = %s and status='planned'
             order by priority_score desc nulls last, keyword
             limit %s
            """,
            (str(site_id), args.limit),
        )
        rows = cur.fetchall()

    print(f"=== Verifying {len(rows)} planned keywords ===\n")
    provider = get_llm_provider("gemini")
    text_cfg = config.get("text_provider") or {}
    model = text_cfg.get("qa_model") or "gemini-3.1-pro-preview"

    results: list[dict] = []
    cumulative = 0.0
    for i, (kid, kw, src, pri) in enumerate(rows, 1):
        if cumulative >= args.budget_usd:
            print(f"⛔ budget cap ${args.budget_usd} hit at row {i}; stopping")
            break

        # Send every keyword to the verifier — search queries are
        # lowercase, so regex pre-filtering for capitalized tokens misses
        # all the real entity names ("minerva", "frost guardian" etc.).
        # The LLM is responsible for both extraction and verification.
        prompt = VERIFY_PROMPT.format(keyword=kw)
        try:
            resp = provider.generate(
                prompt=prompt, model=model, max_tokens=2000,
                temperature=0.1, json_mode=True, enable_search=True,
            )
            cumulative += float(resp.cost_usd or 0)
        except Exception as e:
            print(f"[{i:2d}/{len(rows)}] {kw!r}  → ERROR: {e}")
            results.append({
                "keyword_id": str(kid),
                "keyword": kw,
                "entity_status": "error",
                "fabricated_entities": nouns,
                "verdict": "keep",
                "reason": f"verify call failed; leaving alone: {str(e)[:120]}",
            })
            continue

        try:
            j = extract_json(resp.text)
        except Exception as e:
            j = {"verdict": "keep", "entity_status": "error",
                 "reason": f"parse failed: {e}", "per_noun": []}

        status = j.get("entity_status", "unknown")
        verdict = j.get("verdict", "keep")
        fab = [n["noun"] for n in (j.get("per_noun") or []) if n.get("verdict") == "fabricated"]
        results.append({
            "keyword_id": str(kid),
            "keyword": kw,
            "entity_status": status,
            "fabricated_entities": fab,
            "verdict": verdict,
            "reason": j.get("reason", ""),
            "per_noun": j.get("per_noun", []),
        })

        marker = "❌" if verdict == "archive" else "✓"
        fab_str = f" (fab: {fab})" if fab else ""
        print(f"[{i:2d}/{len(rows)}] {marker} {kw!r}  → {status} → {verdict}{fab_str}")

    print()
    print(f"=== Summary ===")
    n_archive = sum(1 for r in results if r["verdict"] == "archive")
    n_keep = sum(1 for r in results if r["verdict"] == "keep")
    print(f"  keep:    {n_keep}")
    print(f"  archive: {n_archive}")
    print(f"  cost:    ${cumulative:.4f}")
    print()

    out_path = Path("/tmp/keyword_verify_result.json")
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"Full result written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
