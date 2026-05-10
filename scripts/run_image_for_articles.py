"""Generate images for every published article and patch their markdown.

For each article in status='published':
  1. Run ImageAgent → 1 hero + 2 inline images.
  2. Update the corresponding markdown file's frontmatter:
       hero_image: /img/<slug>/hero.png
       inline_images: ["/img/<slug>/inline-1.png", ...]
  3. Update articles.outline (jsonb) for traceability — store hero_url field.

Cost cap: $5 (well below the $20 hard limit). Override via --budget-usd.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.agents.image import ImageAgent
from src.db.client import get_db_connection
from src.utils.llm import get_llm_provider


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SITE_REPO = (
    Path(__file__).resolve().parent.parent.parent / "ntecodex-site"
).resolve()


# article_type → relative content path (mirrors PublishAgent.PATH_BY_TYPE)
PATH_BY_TYPE: dict[str, str] = {
    "build":        "guides/{slug}.md",
    "comparison":   "guides/{slug}.md",
    "boss_guide":   "boss/{slug}.md",
    "reroll":       "guides/reroll/{slug}.md",
    "character_db": "characters/{slug}.md",
    "weapon_db":    "weapons/{slug}.md",
    "news":         "news/{slug}.md",
    "tier_list":    "tier-list-source/{slug}.md",
    "faq":          "faq-source/{slug}.md",
}


def _patch_frontmatter(md_path: Path, hero_url: str, inline_urls: list[str]) -> bool:
    """Inject hero_image / inline_images into the YAML frontmatter."""
    text = md_path.read_text(encoding="utf-8")
    # Frontmatter delimited by ---  ...  ---
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        print(f"   ⚠️  no frontmatter in {md_path}; skipping patch")
        return False
    fm = m.group(1)
    body = text[m.end():]

    # Drop any old hero_image / inline_images
    fm_lines = []
    skip_block = False
    for line in fm.splitlines():
        if line.startswith("hero_image:") or line.startswith("inline_images:"):
            skip_block = True
            continue
        if skip_block and line.startswith("  -"):
            continue
        skip_block = False
        fm_lines.append(line)

    fm_lines.append(f"hero_image: {hero_url}")
    if inline_urls:
        fm_lines.append("inline_images:")
        for u in inline_urls:
            fm_lines.append(f"  - {u}")

    new_text = "---\n" + "\n".join(fm_lines) + "\n---\n" + body
    md_path.write_text(new_text, encoding="utf-8")
    return True


def _section_topics(outline: dict | None, max_n: int = 4) -> list[str]:
    if not outline:
        return []
    sections = outline.get("sections") or []
    out = []
    for s in sections:
        h2 = s.get("h2") if isinstance(s, dict) else None
        if h2 and isinstance(h2, str):
            out.append(h2)
    return out[:max_n]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--budget-usd", type=float, default=5.0)
    p.add_argument("--inline", type=int, default=2,
                   help="Inline images per article (in addition to hero)")
    p.add_argument("--limit", type=int, default=20,
                   help="Max articles to process")
    args = p.parse_args()

    if not SITE_REPO.exists():
        print(f"❌ site repo not found at {SITE_REPO}")
        return 2

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id, config from sites where domain = 'ntecodex.com' limit 1")
        site_row = cur.fetchone()
        if not site_row:
            print("❌ ntecodex.com site not found")
            return 2
        site_id, config = site_row

        cur.execute(
            """
            select id, slug, title, article_type, outline
              from articles
             where site_id = %s and status = 'published'
             order by published_at
             limit %s
            """,
            (str(site_id), args.limit),
        )
        rows = cur.fetchall()

    if not rows:
        print("ℹ️  No published articles to image.")
        return 0

    print(f"🎨 ImageAgent on {len(rows)} article(s) — hero + {args.inline} inline each")
    print(f"   est. {(1 + args.inline) * len(rows)} images @ ~$0.039 each "
          f"= ~${(1 + args.inline) * len(rows) * 0.039:.2f}")
    print()

    llm = get_llm_provider("gemini")
    agent = ImageAgent(llm=llm, site_config=config, site_repo_path=SITE_REPO)

    cumulative_cost = 0.0
    crashed = 0

    for article_id, slug, title, atype, outline in rows:
        if cumulative_cost >= args.budget_usd:
            print(f"⛔ Budget cap ${args.budget_usd:.2f} reached; stopping.")
            break

        # Skip when hero image already exists — re-running is wasteful
        hero_dir = SITE_REPO / "public" / "img" / slug
        if (hero_dir / "hero.webp").exists() or (hero_dir / "hero.png").exists():
            print(f"↪︎  hero exists, skip: {slug}")
            continue

        print(f"▶ {title}")
        print(f"  type={atype} slug={slug}")
        topics = _section_topics(outline, max_n=args.inline)
        try:
            result = agent.run(
                site_id=site_id, article_id=article_id,
                input_data={
                    "site_id": str(site_id),
                    "article_id": str(article_id),
                    "slug": slug,
                    "title": title,
                    "article_type": atype,
                    "section_topics": topics,
                    "inline_count": args.inline,
                },
            )
        except Exception as e:
            print(f"  ❌ FAILED: {type(e).__name__}: {str(e)[:200]}")
            crashed += 1
            continue

        cumulative_cost += float(result.get("total_cost_usd", 0))

        # Build URL lists, patch markdown
        hero_url = ""
        inline_urls: list[str] = []
        for img in result["images"]:
            url = img.get("url")
            if not url:
                continue
            if img["kind"] == "hero":
                hero_url = url
            elif img["kind"].startswith("inline_"):
                inline_urls.append(url)

        rel_path = PATH_BY_TYPE.get(atype, "").format(slug=slug)
        if rel_path:
            md_path = SITE_REPO / "src" / "content" / rel_path
            if md_path.exists() and hero_url:
                if _patch_frontmatter(md_path, hero_url, inline_urls):
                    print(f"  ✓ patched {md_path.relative_to(SITE_REPO)}")
            else:
                print(f"  ⚠️  md file not found: {md_path}")

        print(f"  → hero {hero_url}  ({len(inline_urls)} inline)  "
              f"cost ${result['total_cost_usd']:.4f}  cumulative ${cumulative_cost:.4f}")
        print()

    print(f"\n=== Done. Total cost: ${cumulative_cost:.4f}  Crashed: {crashed} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
