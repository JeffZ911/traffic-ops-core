"""One-shot: remove in-article AdSense <ins> blocks from every published
markdown file. Idempotent. Used when switching to AdSense Auto Ads, which
manages slot placement automatically without manual <ins> markers.

The PublishAgent has been updated separately to not inject these on
future publishes; this script cleans the existing files.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


SITE_REPO = (
    Path(__file__).resolve().parent.parent.parent / "ntecodex-site"
).resolve()
CONTENT = SITE_REPO / "src" / "content"
TARGETS = ["guides", "characters", "boss", "faq-source",
           "weapons", "news", "tier-list-source"]

# Match the entire <div class="ad-slot ..." data-ad-pos="..."> ... </div> block,
# including the trailing <script>... push({});</script> line that lives outside
# the div in our template, plus any surrounding blank lines.
AD_BLOCK_RE = re.compile(
    r'\n*<div class="ad-slot[^"]*" data-ad-pos="(?:top|mid|end)">\n'
    r'<ins class="adsbygoogle"[^>]*>\s*</ins>\n'
    r'<script>\(adsbygoogle = window\.adsbygoogle \|\| \[\]\)\.push\(\{\}\);</script>\n'
    r'</div>\n*',
    re.DOTALL,
)
# Looser variant: any <ins class="adsbygoogle" ... ></ins> + surrounding script
# (covers minor whitespace shifts)
LOOSE_INS_RE = re.compile(
    r'<ins\s+class="adsbygoogle"[\s\S]*?</ins>\s*'
    r'(?:<script>\(adsbygoogle = window\.adsbygoogle \|\| \[\]\)\.push\(\{\}\);</script>)?',
    re.MULTILINE,
)


def strip_one(text: str) -> tuple[str, int]:
    n = 0
    new, sub_n = AD_BLOCK_RE.subn("\n\n", text)
    n += sub_n
    # Catch any stragglers (e.g. partial block from manual editing)
    new2, sub_n2 = LOOSE_INS_RE.subn("", new)
    n += sub_n2
    # Collapse 3+ blank lines back to 2
    new2 = re.sub(r"\n{3,}", "\n\n", new2)
    return new2, n


def main() -> int:
    total = 0
    modified = 0
    for d in TARGETS:
        root = CONTENT / d
        if not root.exists():
            continue
        for md in root.rglob("*.md"):
            text = md.read_text(encoding="utf-8")
            new, n = strip_one(text)
            if new != text:
                md.write_text(new, encoding="utf-8")
                modified += 1
                total += n
                print(f"  ✓ {md.relative_to(SITE_REPO)} — removed {n} block(s)")
    print()
    print(f"Total: {modified} file(s) cleaned, {total} ad block(s) removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
