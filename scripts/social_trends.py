"""Real social-signal trend source — wraps the vendored `last30days` skill.

The trend layer used to ground keyword generation on a Gemini Google-Search
guess. This pulls REAL high-engagement items from the last 30 days off FREE,
keyless communities (Reddit RSS+shreddit, Hacker News Algolia, GitHub search)
and hands the top ones to the keyword generator as leading signals — what is
ACTUALLY bubbling, not what the model guesses is bubbling.

Design notes:
  - We run the vendored CLI headless (`--emit json`, free sources only) and
    parse `ranked_candidates`. We do NOT configure the skill's optional LLM —
    its deterministic ranking is noisy, but our downstream Gemini (the keyword
    generator) does the final relevance pick + turns signals into keywords, so
    raw recall + engagement is all we need here.
  - FAIL-SAFE: any error/timeout returns [] so the content pipeline never
    breaks just because Reddit changed its markup or the network blipped.
  - Per-niche query + curated subreddits keep recall on-topic; imade4u (gifts)
    is intentionally absent — its real signal lives on paid TikTok/IG.

Usage (standalone, for testing):
  python -m scripts.social_trends --niche security_cameras
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_VENDOR = Path(__file__).resolve().parent.parent / "vendor/last30days/scripts/last30days.py"


def _python() -> str:
    """A Python >=3.12 interpreter that actually exists. In CI sys.executable is
    correct; locally it can point at a stale path (broken Homebrew symlink) and
    the system python3 may be too old (vendored skill needs >=3.12), so prefer a
    versioned binary on PATH. SOCIAL_TRENDS_PYTHON overrides everything."""
    override = os.getenv("SOCIAL_TRENDS_PYTHON")
    if override and os.path.exists(override):
        return override
    if sys.executable and os.path.exists(sys.executable):
        return sys.executable
    for name in ("python3.13", "python3.12", "python3"):
        p = shutil.which(name)
        if p:
            return p
    return "python3"

# niche → headless query config. Sources are FREE/keyless only.
SOCIAL_CONFIG: dict[str, dict] = {
    "security_cameras": {
        # NO hackernews: its "security" hits are cyber/software, off-topic for
        # consumer cameras. Reddit (brand/home subs) + GitHub (Frigate/HA) are
        # where camera buyers + integrations actually surface.
        "query": "security camera video doorbell motion detection app outage",
        "subreddits": "homesecurity,homeautomation,homeassistant,ring,eufy,reolink,frigate_nvr,smarthome,wyzecam,arlo",
        "sources": "reddit,github",
    },
    # NOTE: "gaming" intentionally omitted. Reddit's high-engagement gacha posts
    # are memes/discussion ("which character is most slopified"), not the
    # patch/tier/banner EVENTS a guide site needs — and ntecodex's existing
    # grounded web-search trend layer already captures those better (HSR 4.3,
    # ZZZ 2.2). Adding a 50s social fetch per scan for ~0 usable keywords isn't
    # worth it. Revisit if we find a news-flair-filtered source.
    "ecommerce_tools": {
        "query": "AI product photography image generation ecommerce listing tool",
        "subreddits": "StableDiffusion,comfyui,ecommerce,AmazonSeller,Etsy",
        "sources": "github,hackernews,reddit",
    },
}


def _eng_scalar(eng) -> float:
    """Flatten a per-source engagement dict to one number for ranking."""
    if isinstance(eng, dict):
        return float(sum(v for v in eng.values() if isinstance(v, (int, float))))
    if isinstance(eng, (int, float)):
        return float(eng)
    return 0.0


def fetch_social_signals(niche: str, *, top_n: int = 14, timeout: int = 200) -> list[dict]:
    """Top real social signals for a niche → compact dicts. [] on any failure.

    Uses `items_by_source` (each source's raw on-topic recall, already scoped to
    the query + curated subreddits) rather than `ranked_candidates` — the latter
    is a noisy cross-source rerank that, without the skill's optional LLM, floats
    off-topic high-engagement junk. Raw per-source items are on-topic by
    construction; we just dedupe and sort by engagement, then let our downstream
    Gemini do the final pick + turn them into keywords."""
    cfg = SOCIAL_CONFIG.get(niche)
    if not cfg or not _VENDOR.exists():
        return []
    # Default depth (not --deep): --deep fans out top/hot/new across every
    # subreddit at 5 req/s and can take 90s+; default is ~30-45s with plenty of
    # recall for grounding. The fetch must stay well under the cron step budget.
    cmd = [
        _python(), str(_VENDOR), cfg["query"],
        "--search", cfg["sources"], "--subreddits", cfg["subreddits"],
        "--emit", "json", "--days", "30",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout, check=False)
        if out.returncode != 0 or not out.stdout.strip():
            return []
        data = json.loads(out.stdout)
    except Exception:
        return []

    rows, seen = [], set()
    for src, items in (data.get("items_by_source") or {}).items():
        for it in items or []:
            title = (it.get("title") or "").strip()
            url = it.get("url") or ""
            key = url or title.lower()
            if not title or key in seen:
                continue
            seen.add(key)
            rows.append({
                "title": title[:120], "source": src,
                "where": it.get("container") or it.get("subreddit") or "",
                "engagement": it.get("engagement"),
                "_eng": _eng_scalar(it.get("engagement")),
                "url": url,
            })
    rows.sort(key=lambda r: -r["_eng"])
    return [{k: v for k, v in r.items() if k != "_eng"} for r in rows[:top_n]]


def format_for_prompt(signals: list[dict]) -> str:
    """Render signals as a grounding block for the trend keyword prompt."""
    if not signals:
        return ""
    lines = []
    for s in signals:
        where = f" · {s['where']}" if s.get("where") else ""
        eng = f" (engagement {s['engagement']})" if s.get("engagement") not in (None, 0) else ""
        lines.append(f"  - [{s['source']}{where}] {s['title']}{eng}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche", required=True, choices=list(SOCIAL_CONFIG.keys()))
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    sigs = fetch_social_signals(args.niche, top_n=args.top)
    print(f"📡 {len(sigs)} social signal(s) for niche={args.niche}")
    print(format_for_prompt(sigs) or "  (none)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
