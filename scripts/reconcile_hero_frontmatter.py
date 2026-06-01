"""Safety-net: wire on-disk hero images into article frontmatter.

The recurring failure (see commit 9e0d5be and run_image_for_articles.py):
a hero image gets generated and saved to public/img/<slug>/hero.webp, but the
article's markdown frontmatter is left WITHOUT a `hero_image:` field — so the
homepage/tile components fall back to the branded placeholder even though a
real image exists on disk. The disk-fallback writeback in
run_image_for_articles.py only fires for articles it *processes*, and the
early "hero exists, skip" guard short-circuits before that fallback for the
exact articles that need it (hero on disk, frontmatter missing the field).

This script is the deterministic backstop. It is purely filesystem-based
(NO database), so a partial/crashed prior run can never hide an article from
it: for every `*.md` under src/content/, if `public/img/<slug>/hero.{webp,png}`
exists on disk AND the frontmatter has no usable `hero_image:`, it injects
`hero_image: /img/<slug>/hero.<ext>`. Idempotent — re-running is a no-op once
every on-disk hero is wired in.

Run it as the last step before commit in each content cron.

Usage:
    python -m scripts.reconcile_hero_frontmatter            # patch in place
    python -m scripts.reconcile_hero_frontmatter --dry-run  # report only
    python -m scripts.reconcile_hero_frontmatter --repo /path/to/site

Repo resolution: --repo > $SITE_REPO_PATH > sibling/parent-sibling heuristic
(mirrors run_image_for_articles._site_repo_path so it works in CI and locally).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


# Hero filenames we recognise, in preference order. ImageAgent writes .webp;
# .png is accepted for older articles generated before the webp switch.
HERO_NAMES = ("hero.webp", "hero.png")


def _resolve_repo(cli_repo: str | None) -> Path:
    """--repo flag > SITE_REPO_PATH env > sibling/parent-sibling heuristic.
    The heuristic mirrors run_image_for_articles._site_repo_path so behaviour
    is identical in CI (nested checkout) and local dev."""
    if cli_repo:
        return Path(cli_repo).resolve()
    env = os.getenv("SITE_REPO_PATH")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    nested = here.parent.parent / "ntecodex-site"
    parent_sibling = here.parent.parent.parent / "ntecodex-site"
    return (nested if nested.exists() else parent_sibling).resolve()


def _split_frontmatter(text: str) -> tuple[str, str] | None:
    """Return (frontmatter_inner, body) or None if there's no `--- ... ---`
    block at the top of the file."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return None
    return m.group(1), text[m.end():]


def _frontmatter_field(fm: str, key: str) -> str | None:
    """Return the raw value of a top-level scalar frontmatter `key:`, or None
    if absent. Only matches at column 0 (top-level), not nested list items."""
    for line in fm.splitlines():
        if line.startswith(f"{key}:"):
            return line[len(key) + 1:].strip()
    return None


def _has_usable_hero(fm: str) -> bool:
    """True if frontmatter already declares a real hero_image. Treats empty /
    null-ish values as missing so we still backfill them."""
    val = _frontmatter_field(fm, "hero_image")
    if val is None:
        return False
    # Strip surrounding quotes for the null check.
    stripped = val.strip().strip("'\"")
    return stripped not in ("", "null", "None", "~")


def _find_hero_on_disk(repo: Path, slug: str) -> str | None:
    """Return the hero filename (e.g. 'hero.webp') present on disk for this
    slug, preferring webp, or None."""
    img_dir = repo / "public" / "img" / slug
    for name in HERO_NAMES:
        f = img_dir / name
        if f.exists() and f.stat().st_size > 0:
            return name
    return None


def _inject_hero(text: str, hero_url: str) -> str | None:
    """Append `hero_image: <hero_url>` to the frontmatter block, leaving every
    other field and the body untouched. Returns the new text, or None if the
    file has no frontmatter block to patch."""
    split = _split_frontmatter(text)
    if split is None:
        return None
    fm, body = split
    new_fm = fm.rstrip("\n") + f"\nhero_image: {hero_url}"
    return f"---\n{new_fm}\n---\n{body}"


def reconcile_repo(repo: Path, *, dry_run: bool) -> tuple[int, int, list[str]]:
    """Scan every markdown article in `repo`. Returns
    (n_patched, n_missing_no_disk, patched_slugs).

    n_patched          — frontmatter wired to an on-disk hero this run.
    n_missing_no_disk  — frontmatter lacks hero_image AND no hero on disk
                         (a genuinely image-less article; left for the image
                         pipeline, not this safety net).
    """
    content_root = repo / "src" / "content"
    if not content_root.exists():
        print(f"   ⚠️  no src/content under {repo}")
        return 0, 0, []

    patched = 0
    missing_no_disk = 0
    patched_slugs: list[str] = []

    for md_path in sorted(content_root.rglob("*.md")):
        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"   ⚠️  unreadable {md_path}: {type(e).__name__}")
            continue
        split = _split_frontmatter(text)
        if split is None:
            continue  # no frontmatter — not an article we manage
        fm, _ = split
        if _has_usable_hero(fm):
            continue  # already wired — idempotent no-op

        # slug from frontmatter `slug:` if present, else the filename stem.
        # PATH_BY_TYPE always names files <slug>.md, so the stem is reliable.
        slug = _frontmatter_field(fm, "slug") or md_path.stem
        slug = slug.strip().strip("'\"")

        hero_name = _find_hero_on_disk(repo, slug)
        if not hero_name:
            missing_no_disk += 1
            continue

        hero_url = f"/img/{slug}/{hero_name}"
        rel = md_path.relative_to(repo)
        if dry_run:
            print(f"   would patch {rel}  →  hero_image: {hero_url}")
        else:
            new_text = _inject_hero(text, hero_url)
            if new_text is None:
                continue
            md_path.write_text(new_text, encoding="utf-8")
            print(f"   ✓ patched {rel}  →  hero_image: {hero_url}")
        patched += 1
        patched_slugs.append(slug)

    return patched, missing_no_disk, patched_slugs


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", default=None,
                   help="Path to the site repo. Defaults to $SITE_REPO_PATH "
                        "then the sibling/parent-sibling heuristic.")
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would change without writing files.")
    args = p.parse_args()

    repo = _resolve_repo(args.repo)
    if not repo.exists():
        print(f"❌ site repo not found at {repo}")
        return 2

    mode = "DRY-RUN" if args.dry_run else "patch"
    print(f"🔧 reconcile hero frontmatter ({mode}) — repo: {repo}")
    patched, missing_no_disk, _ = reconcile_repo(repo, dry_run=args.dry_run)

    verb = "would patch" if args.dry_run else "patched"
    print(
        f"\n=== Done. {verb} {patched} article(s); "
        f"{missing_no_disk} still image-less (no hero on disk — left for "
        f"the image pipeline). ==="
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
