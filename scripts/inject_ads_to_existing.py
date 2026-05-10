"""One-shot: inject AdSense in-article ad blocks into all already-published
markdown files in ntecodex-site/src/content/. Idempotent.

Future articles get ads at publish time via PublishAgent (already wired)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.agents._ad_inject import inject_ads


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SITE_REPO = (
    Path(__file__).resolve().parent.parent.parent / "ntecodex-site"
).resolve()
CONTENT = SITE_REPO / "src" / "content"

# Match any folder under content where md files live
TARGET_DIRS = ["guides", "characters", "boss", "faq-source",
               "weapons", "news", "tier-list-source"]


def main() -> int:
    pub = os.getenv("NTECODEX_ADSENSE_PUBLISHER_ID") or ""
    if not pub or pub == "pending":
        print(f"❌ no live AdSense pub id (got {pub!r}); skipping injection.")
        return 1

    print(f"🔧 Injecting AdSense (pub={pub}) into existing markdown")
    touched = 0
    for d in TARGET_DIRS:
        root = CONTENT / d
        if not root.exists():
            continue
        for md in root.rglob("*.md"):
            text = md.read_text(encoding="utf-8")
            m = re.match(r"^(---\n.*?\n---\n)", text, re.DOTALL)
            if not m:
                print(f"  ⚠️  no frontmatter: {md}")
                continue
            front, body = m.group(1), text[m.end():]
            new_body = inject_ads(body, pub)
            if new_body == body:
                print(f"  ↪︎ already injected: {md.relative_to(SITE_REPO)}")
                continue
            md.write_text(front + new_body, encoding="utf-8")
            touched += 1
            print(f"  ✓ {md.relative_to(SITE_REPO)}")

    print(f"\nTotal modified: {touched}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
