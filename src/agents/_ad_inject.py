"""Inject AdSense in-article ad blocks at three positions:

  1. top    — after the H1's opening paragraph, before the first H2
  2. mid    — directly above the second H2
  3. end    — after the last content paragraph, before any `## Sources`

Markdown allows inline HTML, so we splice raw <ins> blocks into the body.

Slot IDs are stable placeholders (`1111111111` / `2222222222` /
`3333333333`) until the operator creates real units in the AdSense
dashboard. AdSense Auto-fill renders ads at any slot under the publisher
once the site is approved.
"""

from __future__ import annotations

import re
from typing import Final


AD_TEMPLATE: Final[str] = (
    "<div class=\"ad-slot my-8\" data-ad-pos=\"{position}\">\n"
    "<ins class=\"adsbygoogle\"\n"
    "     style=\"display:block; text-align:center;\"\n"
    "     data-ad-layout=\"in-article\"\n"
    "     data-ad-format=\"fluid\"\n"
    "     data-ad-client=\"{pub}\"\n"
    "     data-ad-slot=\"{slot}\"></ins>\n"
    "<script>(adsbygoogle = window.adsbygoogle || []).push({{}});</script>\n"
    "</div>"
)

# Marker so re-running the injector is idempotent
MARKER_RE = re.compile(r'<div class="ad-slot[^"]*" data-ad-pos="(?:top|mid|end)">')


def _ad_block(position: str, pub_id: str, slot_id: str) -> str:
    pub = pub_id if pub_id.startswith("ca-") else f"ca-{pub_id}"
    return AD_TEMPLATE.format(position=position, pub=pub, slot=slot_id)


def inject_ads(md: str, pub_id: str) -> str:
    """Return md with three in-article ad blocks. Skips if pub_id is empty
    / 'pending', or if blocks are already present (idempotent)."""
    if not pub_id or pub_id == "pending":
        return md
    if MARKER_RE.search(md):
        return md  # already injected

    # Split off `## Sources` section so we don't put the end-of-article ad
    # below the citations footer.
    src_match = re.search(r"\n##\s+Sources\b", md)
    if src_match:
        body = md[:src_match.start()]
        sources_tail = md[src_match.start():]
    else:
        body = md
        sources_tail = ""

    body = body.rstrip("\n")

    # --- Position 3 (end): append after body, before sources_tail ---
    body = body + "\n\n" + _ad_block("end", pub_id, "3333333333")

    # --- Position 2 (mid): directly above the 2nd H2 ---
    h2_positions = [m.start() for m in re.finditer(r"^## (?!Sources\b)", body, re.MULTILINE)]
    if len(h2_positions) >= 2:
        idx = h2_positions[1]
        body = body[:idx] + _ad_block("mid", pub_id, "2222222222") + "\n\n" + body[idx:]

    # --- Position 1 (top): after the H1 + the next paragraph break ---
    h1 = re.search(r"^#\s+.*$", body, re.MULTILINE)
    if h1:
        after_h1 = body[h1.end():]
        # Find the second blank-line break after the H1 (end of first body paragraph)
        para_break = re.search(r"\n\s*\n\S.*?\n\s*\n", after_h1, re.DOTALL)
        if para_break:
            insert_at = h1.end() + para_break.end()
        else:
            # No identifiable opening paragraph — fall back to right after H1
            insert_at = h1.end() + 1
        ad = _ad_block("top", pub_id, "1111111111")
        body = body[:insert_at] + ad + "\n\n" + body[insert_at:]

    return body + ("\n\n" + sources_tail.lstrip("\n") if sources_tail else "\n")
