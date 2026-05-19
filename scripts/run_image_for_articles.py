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
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.agents.image import ImageAgent
from src.db.client import get_db_connection
from src.utils.llm import get_llm_provider


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _site_repo_path() -> Path:
    """SITE_REPO_PATH env var wins; else try sibling layout (CI nested) then
    parent-sibling layout (local dev)."""
    env = os.getenv("SITE_REPO_PATH")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    nested = here.parent.parent / "ntecodex-site"        # CI: traffic-ops-core/ntecodex-site
    parent_sibling = here.parent.parent.parent / "ntecodex-site"  # local: traffic-ops/ntecodex-site
    if nested.exists():
        return nested.resolve()
    return parent_sibling.resolve()


SITE_REPO = _site_repo_path()


# article_type → relative content path. Single source of truth lives in
# src.agents.publish.PATH_BY_TYPE (kept in sync there for both gaming
# and ecommerce niches). Phase 1B 2026-05-14: importing instead of
# maintaining a second copy — the local copy was missing the four
# ecommerce types (tool_guide / vs_comparison / use_case / policy_guide),
# so this script silently no-op'd on those — images generated on disk
# but never injected into the MD frontmatter or body.
from src.agents.publish import PATH_BY_TYPE


def _patch_frontmatter(
    md_path: Path,
    hero_url: str,
    inline_urls: list[str],
    inline_sections: list[str] | None = None,
) -> bool:
    """Inject hero_image / inline_images into the YAML frontmatter AND
    interleave each inline image into the body just below its matching
    H2 section. Hero stays in frontmatter only (rendered by Astro above
    the body), so it doesn't appear twice."""
    from src.agents._inline_image_inject import inject_inline_images as _inject

    text = md_path.read_text(encoding="utf-8")
    # Frontmatter delimited by ---  ...  ---
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        print(f"   ⚠️  no frontmatter in {md_path}; skipping patch")
        return False
    fm = m.group(1)
    body = text[m.end():]

    # Strip any previously-injected `![...](inline-N.webp)` lines from a
    # prior retrofit run so re-running doesn't accumulate duplicates.
    # Match any image whose URL contains `/img/<slug>/inline-` — that's
    # what _inject writes.
    body = re.sub(
        r"^\n?!\[[^\]]*\]\(/img/[^/)]+/inline-\d+\.[a-z]+\)\n?",
        "",
        body,
        flags=re.MULTILINE,
    )

    # Inject inline images into the body (one image per matching H2).
    if inline_urls:
        body, _ = _inject(body, inline_urls, inline_sections or [])

    # Drop any old hero_image / inline_images / inline_image_sections blocks
    fm_lines = []
    skip_block = False
    DROP_KEYS = ("hero_image:", "inline_images:", "inline_image_sections:")
    for line in fm.splitlines():
        if any(line.startswith(k) for k in DROP_KEYS):
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
    if inline_sections:
        # Section labels for each inline_images[i]. PublishAgent uses this
        # parallel list to interleave images with their matching H2.
        # JSON-encoded scalars so section titles with quotes/colons are safe.
        import json as _json
        fm_lines.append("inline_image_sections:")
        for s in inline_sections:
            fm_lines.append(f"  - {_json.dumps(s, ensure_ascii=False)}")

    new_text = "---\n" + "\n".join(fm_lines) + "\n---\n" + body
    md_path.write_text(new_text, encoding="utf-8")
    return True


def _section_topics(outline: dict | None, max_n: int = 6) -> list[str]:
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
    p.add_argument("--inline", type=int, default=6,
                   help="Inline images per article, in addition to hero. "
                        "Default 6 → 7 images per article ≈ $0.27.")
    p.add_argument("--limit", type=int, default=20,
                   help="Max articles to process")
    p.add_argument("--new-only", action="store_true",
                   help="Only consider articles published in the last 48h")
    p.add_argument("--force-regenerate", action="store_true",
                   help="Overwrite existing hero/inline files (used by the "
                        "retrofit workflow to upgrade old 3-image articles).")
    p.add_argument("--slug", default=None,
                   help="Process only the article with this exact slug. Used "
                        "by the daily backfill step to target one specific "
                        "under-imaged article instead of always picking "
                        "ordered-by-published_at.")
    args = p.parse_args()

    if not SITE_REPO.exists():
        print(f"❌ site repo not found at {SITE_REPO}")
        return 2

    site_domain = os.getenv("SITE_DOMAIN", "ntecodex.com")
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id, config from sites where domain = %s limit 1",
                    (site_domain,))
        site_row = cur.fetchone()
        if not site_row:
            print(f"❌ site {site_domain!r} not found in sites table")
            return 2
        site_id, config = site_row

        clauses: list[str] = []
        params: list = [str(site_id)]
        if args.new_only:
            clauses.append("and published_at > now() - interval '48 hours'")
        if args.slug:
            clauses.append("and slug = %s")
            params.append(args.slug)
        params.append(args.limit)
        # P1 fix (2026-05-14): --new-only must image the NEWEST articles
        # first. The old `order by published_at` (ASC) fetched the OLDEST
        # 20 within the 48h window — those mostly already had heroes and
        # got skipped, so this cron's freshly-published articles (sorted
        # last) were never reached under LIMIT 20 → every new article
        # shipped image-less. DESC puts today's batch at the front.
        # Backfill mode (no --new-only) keeps ASC so the oldest
        # under-imaged articles are still drained over time.
        order_dir = "desc" if args.new_only else "asc"
        cur.execute(
            f"""
            select id, slug, title, article_type, outline
              from articles
             where site_id = %s and status = 'published' {' '.join(clauses)}
             order by published_at {order_dir}
             limit %s
            """,
            tuple(params),
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
        # (unless --force-regenerate is set, used by the retrofit workflow
        # to upgrade older articles from 3 → 7 images).
        hero_dir = SITE_REPO / "public" / "img" / slug
        if not args.force_regenerate and (
            (hero_dir / "hero.webp").exists() or (hero_dir / "hero.png").exists()
        ):
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
        inline_sections: list[str] = []
        for img in result["images"]:
            url = img.get("url")
            if not url:
                continue
            if img["kind"] == "hero":
                hero_url = url
            elif img["kind"].startswith("inline_"):
                inline_urls.append(url)
                inline_sections.append(img.get("section_topic", ""))

        rel_path = PATH_BY_TYPE.get(atype, "").format(slug=slug)
        if rel_path:
            md_path = SITE_REPO / "src" / "content" / rel_path
            if md_path.exists() and hero_url:
                if _patch_frontmatter(md_path, hero_url, inline_urls, inline_sections):
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
