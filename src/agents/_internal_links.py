"""Auto-insert internal links into an article body.

Given a lookup of `keyword -> url`, walk the markdown line by line and
replace the FIRST occurrence of each keyword with `[keyword](url)`.
Subsequent occurrences are left alone — avoids link spam.

Rules (in addition to the once-per-doc cap):

- Code fences ``` ... ``` are skipped entirely.
- Inside an existing markdown link `[...](...)`, no replacement.
- Inside a markdown heading line (`#`), no replacement — headings should
  stay clean and not turn into links.
- Inside an HTML tag (`<a ...>`), no replacement.
- The current article's own URL is never used as a link target.
- Match is case-insensitive but preserves the matched text in the link
  label so "nanally" -> "[nanally](/characters/nanally-guide-nte/)".

Keywords are matched as whole words. Longer keywords are tried first so
"Beyond the Rails" beats a substring match against "Beyond".
"""

from __future__ import annotations

import re
from typing import Iterable


_CODE_FENCE = re.compile(r"^```")
_HEADING = re.compile(r"^\s*#")
# A markdown link target span we need to avoid stepping on
_LINK_SPAN = re.compile(r"\[[^\]]*\]\([^)]*\)")
_HTML_TAG_SPAN = re.compile(r"<[^>]+>")


def _spans_to_skip(line: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for m in _LINK_SPAN.finditer(line):
        spans.append(m.span())
    for m in _HTML_TAG_SPAN.finditer(line):
        spans.append(m.span())
    return spans


def _in_skip_span(pos: int, spans: Iterable[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


def inject_internal_links(
    content_md: str,
    keyword_to_url: dict[str, str],
    self_url: str | None = None,
) -> tuple[str, list[str]]:
    """Insert links into markdown body.

    Returns (rewritten_md, list_of_keywords_linked).
    """
    # Strip self-url from the lookup so we don't self-link.
    if self_url:
        keyword_to_url = {
            k: u
            for k, u in keyword_to_url.items()
            if u.rstrip("/") != self_url.rstrip("/")
        }

    # Longest first so "Beyond the Rails" wins over "Beyond".
    ordered_keywords = sorted(keyword_to_url.keys(), key=len, reverse=True)

    # Pre-compile case-insensitive whole-word matchers
    patterns = {
        kw: re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
        for kw in ordered_keywords
    }

    used: set[str] = set()
    out_lines: list[str] = []
    in_code = False
    for line in content_md.splitlines():
        if _CODE_FENCE.match(line):
            in_code = not in_code
            out_lines.append(line)
            continue
        if in_code or _HEADING.match(line):
            out_lines.append(line)
            continue

        skip_spans = _spans_to_skip(line)
        # Try each keyword in order; first hit per line that lands outside
        # an existing link/tag wins. Update spans after a hit so subsequent
        # keywords don't double-link this new link.
        for kw in ordered_keywords:
            if kw in used:
                continue
            m = patterns[kw].search(line)
            if not m:
                continue
            if _in_skip_span(m.start(), skip_spans):
                continue
            replacement = f"[{m.group(0)}]({keyword_to_url[kw]})"
            line = line[: m.start()] + replacement + line[m.end():]
            used.add(kw)
            # Re-scan skip spans (the new link is itself a span to skip)
            skip_spans = _spans_to_skip(line)
        out_lines.append(line)

    return "\n".join(out_lines), sorted(used)


def build_keyword_lookup_from_articles(rows: Iterable[dict]) -> dict[str, str]:
    """Build a `keyword -> url` lookup from a list of published-article rows.

    Each row must have at least: `title`, `published_url`, `article_type`,
    and optionally `outline` (for character_db rows that carry a clean
    character name in outline.character_id).

    Heuristic: for character_db rows, key the lookup by the character's
    short name (everything before the first colon in the title). For
    other rows, we use a few significant title tokens as fallback.
    """
    lookup: dict[str, str] = {}
    for r in rows:
        url = r.get("published_url")
        if not url:
            continue
        title = r.get("title") or ""
        article_type = r.get("article_type")

        if article_type == "character_db":
            outline = r.get("outline") or {}
            name = None
            if isinstance(outline, dict):
                name = outline.get("character_id")
            if not name:
                name = title.split(":", 1)[0].strip()
            if name and len(name) >= 3:
                lookup.setdefault(name, url)
        # We deliberately DO NOT add fuzzy title-keyword links for non-
        # character articles for now — too easy to hit false-positives.
        # If we want this later, add curated topic terms here.
    return lookup
