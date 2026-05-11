"""Audit-driven cleanup of published markdown files.

Three classes of fix applied in-place:

1. `#TODO:keyword:<slug>` placeholder links — these were emitted by the
   WritingAgent prompt as internal-link stubs that PublishAgent was
   supposed to resolve. We never built that resolver, so they're showing
   up live as broken anchors. Strategy:
     - If the target keyword maps to a published article, rewrite href
       to the real URL.
     - Else, strip the <a> wrapper and keep the anchor text as plain text.

2. `<img src="https://example.com/...">` inline images — leaked into
   article bodies via the WritingAgent before we tightened the prompt.
   Strategy: remove the whole `<img>` line.

3. `<img src="https://placehold.co/...">` placeholders — same idea.
   Strategy: remove.

Idempotent; safe to re-run.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from src.db.client import get_db_connection


SITE_REPO = (Path(__file__).resolve().parent.parent.parent / "ntecodex-site").resolve()
CONTENT = SITE_REPO / "src" / "content"
TARGETS = ["guides", "characters", "boss", "faq-source", "tier-list-source", "weapons", "news"]


def _slug_map() -> dict[str, str]:
    """Build keyword-token → published_url map from articles."""
    out: dict[str, str] = {}
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "select slug, published_url from articles where status = 'published'"
        )
        for slug, url in cur.fetchall():
            if url:
                out[slug] = url
    return out


def fix_text(text: str, url_map: dict[str, str]) -> tuple[str, dict[str, int]]:
    stats = {"todo_resolved": 0, "todo_stripped": 0, "example_img": 0, "placeholder_img": 0}

    def resolve_todo(m: re.Match) -> str:
        """[anchor text](#TODO:keyword:slug) → either real link or plain text."""
        anchor = m.group(1)
        token = m.group(2)
        # Try direct slug match
        if token in url_map:
            stats["todo_resolved"] += 1
            return f"[{anchor}]({url_map[token]})"
        # No match → strip the link wrapper, keep the visible text
        stats["todo_stripped"] += 1
        return anchor

    text = re.sub(
        r"\[([^\]]+)\]\(#TODO:keyword:([^)]+)\)",
        resolve_todo,
        text,
    )

    # Remove example.com inline images
    def drop_example(m: re.Match) -> str:
        stats["example_img"] += 1
        return ""

    text = re.sub(
        r"!?<img\s+src=\"https?://example\.com/[^\"]+\"[^>]*>\s*\n?",
        drop_example,
        text,
        flags=re.IGNORECASE,
    )
    # Markdown form
    text = re.sub(
        r"!\[[^\]]*\]\(https?://example\.com/[^)]+\)\s*\n?",
        drop_example,
        text,
        flags=re.IGNORECASE,
    )

    # Same for placehold.co
    def drop_placehold(m: re.Match) -> str:
        stats["placeholder_img"] += 1
        return ""

    text = re.sub(
        r"!?<img\s+src=\"https?://placehold\.co/[^\"]+\"[^>]*>\s*\n?",
        drop_placehold,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"!\[[^\]]*\]\(https?://placehold\.co/[^)]+\)\s*\n?",
        drop_placehold,
        text,
        flags=re.IGNORECASE,
    )

    # Collapse triple+ newlines from removals
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text, stats


def main() -> int:
    url_map = _slug_map()
    print(f"📚 published slug → URL map: {len(url_map)} entries")
    print()

    total = {"todo_resolved": 0, "todo_stripped": 0, "example_img": 0, "placeholder_img": 0}
    files_touched = 0

    for d in TARGETS:
        root = CONTENT / d
        if not root.exists():
            continue
        for md in root.rglob("*.md"):
            text = md.read_text(encoding="utf-8")
            new, stats = fix_text(text, url_map)
            if new == text:
                continue
            md.write_text(new, encoding="utf-8")
            files_touched += 1
            print(f"  ✓ {md.relative_to(SITE_REPO)}:")
            for k, n in stats.items():
                if n:
                    print(f"     {k}: {n}")
            for k in total:
                total[k] += stats[k]

    print()
    print(f"Files touched: {files_touched}")
    for k, n in total.items():
        print(f"  {k}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
