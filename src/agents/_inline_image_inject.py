"""Interleave inline images into a markdown body, one image per H2 section.

PublishAgent + the post-publish ImageAgent both produce articles whose
markdown body has H2-delimited sections (no per-section illustrations
embedded directly). This module takes that body plus a parallel list of
`(image_url, section_topic)` pairs and injects each image just below the
H2 line that matches its section topic.

Matching strategy (priority order, first hit wins per image):

1. **Exact match** on the H2 text (case-insensitive, trimmed).
2. **Substring match** of the section_topic INSIDE the H2 line (handles
   cases where the writer slightly expanded the H2 wording).
3. **Substring match** of the H2 line inside the section_topic (handles
   the writer trimming the H2 wording).
4. **Index fallback** — if no textual match, fall back to "the i-th H2"
   in document order.

Rules:

- Skip H2s inside fenced code blocks.
- A given H2 receives at most one image.
- A given image is used at most once.
- The hero image is NOT injected here — it stays in the frontmatter and
  is rendered by ArticleLayout above the article body.
- If `## Sources` is reached, stop matching against later H2s (Sources
  section is appendix-level, not content).
"""

from __future__ import annotations

import re
from typing import Iterable


_CODE_FENCE = re.compile(r"^```")
_H2_LINE = re.compile(r"^##\s+(.+?)\s*$")
_SOURCES_H2 = re.compile(r"^##\s+sources\b", re.IGNORECASE)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _find_h2_for_topic(
    h2_lines: list[tuple[int, str]],
    topic: str,
    used: set[int],
) -> int | None:
    """Return the index into h2_lines for the best match, or None."""
    if not topic:
        return None
    t = _norm(topic)

    # 1. exact match
    for i, (_lineno, h2_text) in enumerate(h2_lines):
        if i in used:
            continue
        if _norm(h2_text) == t:
            return i
    # 2. topic substring of h2 text
    for i, (_lineno, h2_text) in enumerate(h2_lines):
        if i in used:
            continue
        if t in _norm(h2_text):
            return i
    # 3. h2 substring of topic
    for i, (_lineno, h2_text) in enumerate(h2_lines):
        if i in used:
            continue
        if _norm(h2_text) in t and len(_norm(h2_text)) >= 4:
            return i
    return None


def inject_inline_images(
    content_md: str,
    inline_images: Iterable[str],
    inline_image_sections: Iterable[str] | None = None,
) -> tuple[str, list[str]]:
    """Insert markdown image lines after the H2 each image is bound to.

    Args:
        content_md: the article body (no frontmatter).
        inline_images: list of image URLs (same order as
            inline_image_sections, when present).
        inline_image_sections: optional parallel list of section labels.
            If None or shorter than the URL list, missing entries fall
            back to "" → index-based assignment.

    Returns:
        (rewritten_md, list_of_image_urls_actually_injected).

    Images that don't find an H2 slot are left out of the body (they
    remain in frontmatter for future re-runs). Image-count exceeds H2-
    count → extra images silently dropped.
    """
    urls = list(inline_images)
    sections = list(inline_image_sections or [])
    while len(sections) < len(urls):
        sections.append("")

    # Pass 1: enumerate H2 lines outside code fences, up to (but not
    # including) the Sources H2.
    lines = content_md.splitlines(keepends=False)
    h2_lines: list[tuple[int, str]] = []
    in_code = False
    for i, line in enumerate(lines):
        if _CODE_FENCE.match(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        if _SOURCES_H2.match(line):
            break
        m = _H2_LINE.match(line)
        if m:
            h2_lines.append((i, m.group(1)))

    if not h2_lines:
        return content_md, []

    # Pass 2: assign images to H2 slots using the matching strategy.
    # Track which H2s and which images are used.
    used_h2: set[int] = set()
    used_url: set[int] = set()
    # h2_index -> img_url
    assignment: dict[int, str] = {}

    # 2a. Try textual matches first, in URL order.
    for url_idx, (url, topic) in enumerate(zip(urls, sections)):
        h2_idx = _find_h2_for_topic(h2_lines, topic, used_h2)
        if h2_idx is not None:
            assignment[h2_idx] = url
            used_h2.add(h2_idx)
            used_url.add(url_idx)

    # 2b. Index-fallback for unmatched images — fill remaining H2 slots
    # in document order.
    free_h2 = [i for i in range(len(h2_lines)) if i not in used_h2]
    for url_idx, url in enumerate(urls):
        if url_idx in used_url:
            continue
        if not free_h2:
            break
        h2_idx = free_h2.pop(0)
        assignment[h2_idx] = url
        used_h2.add(h2_idx)
        used_url.add(url_idx)

    # Pass 3: build the new markdown, inserting `![](url)` after each
    # assigned H2 line. Iterate top-to-bottom; H2 lineno is in the
    # original line list, but as we insert we shift subsequent lines.
    # Sort by lineno descending so insertions don't perturb earlier
    # indices.
    out_lines = lines[:]
    injected_urls: list[str] = []
    h2_in_order_with_url = sorted(
        ((h2_lines[i][0], assignment[i], h2_lines[i][1])
         for i in assignment),
        key=lambda x: x[0],
        reverse=True,
    )
    for lineno, url, h2_text in h2_in_order_with_url:
        alt = h2_text.strip().replace("]", "").replace("[", "")
        img_md = f"\n![{alt}]({url})\n"
        out_lines.insert(lineno + 1, img_md)
        injected_urls.append(url)

    # Return in document order (top-down)
    injected_urls.reverse()
    return "\n".join(out_lines), injected_urls
